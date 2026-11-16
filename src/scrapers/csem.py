"""Cambridge Society for Early Music — the concert season.

WordPress (WPBakery) with no events plugin. The season page lists each
program as a heading, a blurb naming the performers, a summary of where it
plays ("January 15 in Cambridge | January 16 in Weston | ..."), and a
"Tickets & Info" link. The summary prints no year and no time, so it is not
read; each program's own page has a "Performance times" block with both:

    Friday, January 15, 2027 at 7:30pm
    First Church Cambridge
    11 Garden Street, Cambridge

Every program tours: one night in Cambridge, the others in Weston, Beverly
Farms or Boston. Only the Cambridge/Somerville performances are kept, by the
city in the printed address (see base_feed).
"""
import logging
import re
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper
from src.scrapers.base_printed_date import at, clock, dated, month_number, weekday_number

logger = logging.getLogger(__name__)

SITE = "https://csem.org"
PERFORMANCE = re.compile(
    r"^\s*((?:mon|tues|wednes|thurs|fri|satur|sun)day),?\s+([a-z]+)\.?\s+(\d{1,2}),?\s+(\d{4})\b(.*)$", re.I)


class CSEMScraper(BaseFeedScraper):
    HOME = None
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Cambridge Society for Early Music", source_url=f"{SITE}/concerts/")

    def categorize(self, title: str) -> EventCategory:
        """CSEM presents concerts only."""
        return self.CATEGORY

    def fetch(self, url: str) -> str:
        response = requests.get(url, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        programs = self.programs(BeautifulSoup(self.fetch(self.source_url), "html.parser"))
        if not programs:
            raise ValueError(f"{self.source_name}: no programs on {self.source_url} — page layout changed?")

        # A program page that fails to load fails the run: its dates exist
        # nowhere else, and a partial result would delete that program's
        # listings, where a failed run preserves them.
        events: List[EventCreate] = []
        for url, title, blurb in programs:
            page = BeautifulSoup(self.fetch(url), "html.parser")
            events.extend(self.performances(page, url, title, blurb))
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- the season page --------------------------------------------------- #

    def programs(self, soup: BeautifulSoup) -> List[Tuple[str, str, str]]:
        """(program page URL, title, blurb) for each program on the season page.

        In document order a program is an h2 (its title), h3s (the blurb, then
        the tour summary), and the "Tickets & Info" link to its page.
        """
        found = []
        title, blurb = None, None
        for el in soup.find_all(["h2", "h3", "a"]):
            if el.find_parent(["nav", "header", "footer"]):
                continue
            if el.name == "h2":
                title, blurb = self.text(el.get_text(" ")), None
            elif el.name == "h3":
                if blurb is None:
                    blurb = self.text(el.get_text(" "))
            elif "tickets" in el.get_text().lower() and title:
                href = el.get("href") or ""
                if href.startswith(f"{SITE}/concerts/") and href.rstrip("/") != self.source_url.rstrip("/"):
                    found.append((href, title, blurb or ""))
                title, blurb = None, None
        return found

    # -- a program page ---------------------------------------------------- #

    def performances(self, page: BeautifulSoup, url: str, title: str, blurb: str) -> List[EventCreate]:
        events = []
        seen = set()
        for p in page.find_all("p"):
            lines = self.lines(p)
            if not lines:
                continue
            match = PERFORMANCE.match(lines[0])
            if not match:
                continue
            start = self.start(match, title, url)
            if start is None:
                continue
            venue = lines[1] if len(lines) > 1 else None
            address = lines[2] if len(lines) > 2 else None
            street, city = self.split(address)
            where = self.place(title, venue, street, city)
            if where is None:
                continue
            if (start, where.venue) in seen:
                continue
            seen.add((start, where.venue))

            description = blurb if len(blurb) >= 20 else self.fallback_description(title, where)
            if not description.endswith((".", "!", "?")):
                description += "."
            events.append(EventCreate(
                title=title[:200],
                description=f"{description} Presented by the Cambridge Society for Early Music."[:2000],
                start_datetime=start[0],
                all_day=start[1],
                venue_name=where.venue[:150],
                street_address=where.street,
                city=where.city,
                state="MA",
                zip_code=where.zip_code,
                category=self.categorize(title),
                source_name=self.source_name,
                source_url=url,
            ))
        return events

    @staticmethod
    def lines(element) -> List[str]:
        element = BeautifulSoup(str(element), "html.parser")
        for br in element.find_all("br"):
            br.replace_with("\n")
        return [" ".join(line.split()) for line in element.get_text().split("\n") if line.strip()]

    @staticmethod
    def split(address: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
        """'11 Garden Street, Cambridge' -> ('11 Garden Street', 'Cambridge')."""
        parts = [p.strip() for p in (address or "").split(",") if p.strip()]
        if len(parts) < 2:
            return None, None
        return parts[0], parts[1]

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
