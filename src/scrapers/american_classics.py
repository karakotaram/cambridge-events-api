"""American Classics — a concert series of American musical-theater song.

One hand-typed page per season (current-season.html). Each concert is an
`<h3>` ("Fall Concert") followed by a paragraph of `<br>`-separated lines that
holds its performances, each a date line, then venue, street and town:

    Friday, Nov. 13, 2026, 7:30pm
    First Church Congregational
    11 Garden Street
    Cambridge, MA
    Tickets ($25 - $30)
    also
    Sunday, Nov. 15, 2026, 3:00pm
    First Parish Church ... Bedford, MA

Each concert is given in Cambridge and repeated in Bedford; only performances
in Cambridge or Somerville are kept. The date line carries weekday, year and
time; a line whose weekday disagrees with its date is skipped as a typo.
Performer lists and old ticket links are left inside HTML comments by the
editor between seasons and are not read.
"""
import logging
import re
from datetime import datetime
from typing import List, Optional

from bs4 import Comment, NavigableString, Tag

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import LOCAL_CITIES, BaseFeedScraper

logger = logging.getLogger(__name__)

PAGE = "https://americanclassics.benandbrad.com/current-season.html"

# "Friday, Nov. 13, 2026, 7:30pm" / "Friday, April. 16, 2027, 7:30pm"
DATE_LINE = re.compile(
    r"^(?P<weekday>[A-Za-z]+),\s*(?P<month>[A-Za-z]+)\.?\s+(?P<day>\d{1,2}),\s*(?P<year>\d{4}),\s*"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<meridiem>[ap])\.?m\.?$", re.I)
TOWN = re.compile(r"^(?P<city>[A-Za-z .'-]+),\s*MA\b")
PRICE = re.compile(r"\((\$[^)]*)\)")
MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}


def read_date_line(line: str) -> Optional[datetime]:
    match = DATE_LINE.match(line.strip())
    if not match:
        return None
    month = MONTHS.get(match.group("month")[:3].lower())
    if month is None:
        return None
    hour = int(match.group("hour")) % 12 + (12 if match.group("meridiem").lower() == "p" else 0)
    try:
        when = datetime(int(match.group("year")), month, int(match.group("day")), hour, int(match.group("minute")))
    except ValueError:
        return None
    if when.strftime("%A").lower()[:3] != match.group("weekday").lower()[:3]:
        logger.warning(f"American Classics: skipping {line!r} - {when:%B %d, %Y} is a {when:%A}")
        return None
    return when


class AmericanClassicsScraper(BaseFeedScraper):
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="American Classics", source_url=PAGE)

    def scrape_events(self) -> List[EventCreate]:
        return self.parse_page(self.fetch_html(self.source_url))

    @staticmethod
    def lines_of(paragraph: Tag) -> List[tuple]:
        """[(text, ticket href or None)] per <br>-separated line; comments skipped."""
        lines, text, href = [], [], None
        for node in paragraph.descendants:
            if isinstance(node, Comment):
                continue
            if isinstance(node, Tag) and node.name == "br":
                lines.append((" ".join("".join(text).split()), href))
                text, href = [], None
            elif isinstance(node, Tag) and node.name == "a" and node.get("href"):
                href = node["href"]
            elif isinstance(node, NavigableString):
                text.append(str(node))
        lines.append((" ".join("".join(text).split()), href))
        return [line for line in lines if line[0]]

    def parse_page(self, html: str) -> List[EventCreate]:
        soup = self.parse_html(html)
        season = soup.find("h2")
        program = None
        if season:
            intro = season.find_next("p")
            lead = intro.find(["strong", "em"]) if intro else None
            program = self.clean_text(lead.get_text()) if lead else None
            blurb = self.clean_text(intro.get_text(" ")) if intro else ""
        else:
            blurb = ""

        events = []
        for heading in soup.find_all("h3"):
            concert = self.clean_text(heading.get_text())
            paragraph = heading.find_next_sibling("p")
            if not concert or paragraph is None:
                continue
            for performance in self.performances(self.lines_of(paragraph)):
                event = self.build(performance, concert, program, blurb)
                if event:
                    events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def performances(self, lines: List[tuple]) -> List[dict]:
        """Group lines into performances, each opened by a date line."""
        found, current = [], None
        for text, href in lines:
            when = read_date_line(text)
            if when is not None:
                current = {"start": when, "lines": [], "tickets": None, "price": None}
                found.append(current)
                continue
            if current is None or text.lower() == "also":
                continue
            if text.lower().startswith("tickets"):
                current["tickets"] = href
                price = PRICE.search(text)
                current["price"] = price.group(1) if price else None
            else:
                current["lines"].append(text)
        return found

    def build(self, performance: dict, concert: str, program: Optional[str], blurb: str) -> Optional[EventCreate]:
        title = f"American Classics: {program} ({concert})" if program else f"American Classics: {concert}"
        lines = performance["lines"]
        town_at = next((i for i, line in enumerate(lines) if TOWN.match(line)), None)
        if town_at is None or town_at < 1:
            logger.warning(f"Skipping '{title}' on {performance['start']} - no venue and town under it")
            return None
        city = TOWN.match(lines[town_at]).group("city").strip().title()
        if city not in LOCAL_CITIES:
            return None
        venue = lines[0]
        street = lines[town_at - 1] if town_at >= 2 else None

        description = f"{blurb} At {venue}, {city}.".strip() if blurb else f"{title} at {venue}, {city}."
        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=performance["start"],
            venue_name=venue[:150],
            street_address=street,
            city=city,
            state="MA",
            category=self.CATEGORY,
            cost=performance["price"],
            source_name=self.source_name,
            source_url=self.source_url,
            website_url=performance["tickets"],
        )
