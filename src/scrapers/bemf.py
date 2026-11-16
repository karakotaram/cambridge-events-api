"""Boston Early Music Festival — the concert season page.

BEMF's site (WordPress, Cornerstone page builder) runs no events plugin; the
season is one hand-built page with a block per program: ensemble (h2),
program (h3), and an h4 per venue holding its performance dates
("FRIDAY, DECEMBER 4, 2026 at 7:30pm") above the venue's name.

**Where.** BEMF presents in Cambridge (St. Paul Church, First Church in
Cambridge), Boston (Jordan Hall) and on tour (Troy, NY). The venue line links
to BEMF's own venue page; the addresses come from BEMF's concert-venues list,
read once per run, because the season page names "First Church in Cambridge,
Congregational" without a city. Only Cambridge and Somerville are kept (see
base_feed). A venue BEMF has no page for falls back to the city printed after
its name.

**Not events.** Each program also has a "VIRTUAL PREMIERE" — a stream that
stays up for two weeks. Those lines are skipped; the in-person performances
are what the calendar lists.
"""
import logging
import re
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place
from src.scrapers.base_printed_date import at, clock, dated, month_number, weekday_number

logger = logging.getLogger(__name__)

SITE = "https://bemf.org"
VENUES_URL = f"{SITE}/concert-season/concert-venues/"

PERFORMANCE = re.compile(
    r"\b((?:mon|tues|wednes|thurs|fri|satur|sun)day),?\s+([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b(.*)$", re.I)
NOT_IN_PERSON = re.compile(r"virtual|available to watch|livestream", re.I)
STATE_OR_ZIP = re.compile(r"^(?:[A-Z]{2}|\d{5})(?:\s+\d{5})?$")

# Kept in capitals when a shouted heading is put into title case.
ACRONYMS = {"BEMF", "NEC", "II", "III", "IV"}


def title_case(text: str) -> str:
    """'THE TALLIS SCHOLARS' -> 'The Tallis Scholars'; mixed-case words are left alone."""
    def fix(match):
        word = match.group(0)
        if word in ACRONYMS or not word.isupper() or len(word) < 2:
            return word
        return word.capitalize()
    return re.sub(r"[^\W\d_][^\W\d_'’.-]*", fix, text)


def split_address(text: str) -> tuple:
    """'29 Mt Auburn Street, Cambridge' -> ('29 Mt Auburn Street', 'Cambridge');
    'Troy Savings Bank Music Hall, Troy, NY' -> ('Troy Savings Bank Music Hall', 'Troy')."""
    parts = [p.strip() for p in (text or "").split(",") if p.strip()]
    while len(parts) > 1 and STATE_OR_ZIP.match(parts[-1]):
        parts.pop()
    if len(parts) < 2:
        return (parts[0] if parts else None), None
    return parts[0], parts[-1]


class BEMFScraper(BaseFeedScraper):
    HOME = None
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Boston Early Music Festival", source_url=f"{SITE}/concert-season/")

    def categorize(self, title: str) -> EventCategory:
        """Every BEMF program is a concert or an opera. Read by title, "A Musical
        Journey" would file The Royal Wind Music under theater."""
        return self.CATEGORY

    def fetch(self, url: str) -> str:
        response = requests.get(url, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        season = BeautifulSoup(self.fetch(self.source_url), "html.parser")
        venues = self.read_venues(BeautifulSoup(self.fetch(VENUES_URL), "html.parser"))
        blocks = [s for s in season.select("#cs-content div.x-section") if s.find("h2")]
        if not blocks:
            raise ValueError(f"{self.source_name}: no program blocks on {self.source_url} — page layout changed?")

        events: List[EventCreate] = []
        seen = set()
        for block in blocks:
            for event in self.parse_program(block, venues):
                key = (event.title, event.start_datetime)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- venues ------------------------------------------------------------ #

    def read_venues(self, soup: BeautifulSoup) -> Dict[str, Place]:
        """{venue page URL: Place} from BEMF's concert-venues list.

        Each venue is an h5 name, a paragraph with its address, and a
        "LEARN MORE" link to its page — the page the season list links to.
        """
        venues: Dict[str, Place] = {}
        for h5 in soup.find_all("h5"):
            name = self.text(h5.get_text(" "))
            address = h5.find_next("p")
            link = h5.find_next("a", href=re.compile(r"/concert-venues/[^/]+/?$"))
            if not (name and address and link):
                continue
            street, city = split_address(self.text(address.get_text(" ")))
            venues[link["href"].rstrip("/")] = Place(name, street, city, None)
        if not venues:
            raise ValueError(f"{self.source_name}: no venues on {VENUES_URL} — page layout changed?")
        return venues

    # -- one program ------------------------------------------------------- #

    @staticmethod
    def lines(element) -> List[str]:
        """An element's text split at its <br>s — one printed line each."""
        element = BeautifulSoup(str(element), "html.parser")
        for br in element.find_all("br"):
            br.replace_with("\n")
        return [" ".join(line.split()) for line in element.get_text().split("\n") if line.strip()]

    def parse_program(self, block, venues: Dict[str, Place]) -> List[EventCreate]:
        ensemble = title_case(self.text(block.find("h2").get_text(" ")))
        program_el = block.find("h3")
        program = self.text(program_el.get_text(" ")) if program_el else ""
        title = f"{ensemble}: {program}" if program else ensemble

        more = next((a["href"] for a in block.find_all("a", href=True)
                     if "learn more" in a.get_text().lower() and "/concert-venues/" not in a["href"]), None)
        url = more or self.source_url
        image = next((img.get("src") for img in block.find_all("img")
                      if (img.get("src") or "").startswith("http")), None)

        credits = []
        dated_h4s = []
        for h4 in block.find_all("h4"):
            lines = self.lines(h4)
            if any(PERFORMANCE.search(line) for line in lines):
                dated_h4s.append((h4, lines))
            elif not any(NOT_IN_PERSON.search(line) for line in lines):
                credits.append(self.text(" ".join(lines)))
        sentences = [title] + [c for c in credits if c] + ["Presented by the Boston Early Music Festival"]
        description = " ".join(s if s.endswith((".", "!", "?")) else f"{s}." for s in sentences)

        events = []
        for h4, lines in dated_h4s:
            starts = []
            venue_text = None
            for line in lines:
                if NOT_IN_PERSON.search(line):
                    continue
                match = PERFORMANCE.search(line)
                if match:
                    start = self.start(match, title, url)
                    if start is not None:
                        starts.append(start)
                elif starts:
                    venue_text = line
            if not starts:
                continue

            where = self.venue(h4, venue_text, venues, title)
            if where is None:
                continue
            for start, all_day in starts:
                events.append(EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start,
                    all_day=all_day,
                    venue_name=where.venue[:150],
                    street_address=where.street,
                    city=where.city,
                    state="MA",
                    zip_code=where.zip_code,
                    category=self.categorize(title),
                    source_name=self.source_name,
                    source_url=url,
                    image_url=image,
                ))
        return events

    def start(self, match, title: str, url: str):
        weekday, month, day, year, rest = match.groups()
        month_no = month_number(month)
        day_ = dated(int(year), month_no, int(day), weekday_number(weekday)) if month_no else None
        if day_ is None:
            logger.warning(f"Skipping a '{title}' date - {match.group(0)!r} is not a real date "
                           f"or its weekday disagrees ({url})")
            return None
        time = clock(rest)
        return at(day_, time), time is None

    def venue(self, h4, venue_text: Optional[str], venues: Dict[str, Place], title: str) -> Optional[Place]:
        link = h4.find("a", href=re.compile(r"/concert-venues/"))
        known = venues.get(link["href"].rstrip("/")) if link else None
        if known:
            return self.place(title, known.venue, known.street, known.city)
        if not venue_text:
            logger.info(f"{self.source_name}: skipping '{title}' - no venue printed")
            return None
        name, city = split_address(venue_text)
        return self.place(title, name, None, city)
