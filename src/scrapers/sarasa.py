"""Sarasa Ensemble — a baroque ensemble whose season pages state each performance.

sarasamusic.org is Squarespace without an events collection. Each season has a
page (linked from the home page as "/202627-concerts") that links to one page
per program, and each program page gives its performances one per paragraph:

    Saturday, January 9, 2027 at 7pm at Friends Meeting House, Cambridge MA
    Sunday, January 10, 2027 at 3:30pm at Follen Church, Lexington

Every line is a complete date, time and place, so nothing is inferred. A line
whose weekday does not match its date is a typo somewhere and is skipped. Each
program plays Brattleboro VT, Cambridge and Lexington on consecutive days; only
Cambridge is kept. The Cambridge room is written "Friends Meeting House" or
"Cambridge Friends Meeting House"; the season page's venue list gives its
address, 5 Longfellow Park.

Titles follow the "Sarasa: <program>" form BostonShows.org uses for the same
concerts, so the two are recognised as one in deduplication.

The ticket links go to SimpleTix, whose JSON-LD has the times but labels every
performance "Cambridge & Lexington", so it cannot say which is which.
"""
import logging
import re
from datetime import datetime
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

SITE = "https://www.sarasamusic.org"
SEASON_LINK = re.compile(r'href="(/\d{4,6}-concerts)"')
PAGE_LINK = re.compile(r'href="(/[a-z0-9-]+)"')
PERFORMANCE = re.compile(
    r"^(?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+"
    r"(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4}),?\s+at\s+"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d\d))?\s*(?P<ampm>[ap])\.?m\.?\s+(?:at|-)\s+(?P<place>.+)$",
    re.I)
STATE = re.compile(r"\s+[A-Z]{2}$")


class SarasaScraper(BaseFeedScraper):
    HOME = Place("Friends Meeting House", "5 Longfellow Park", "Cambridge", "02138")
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Sarasa Ensemble", source_url=SITE)

    def get(self, path: str, as_json: bool):
        response = requests.get(f"{SITE}{path}", params={"format": "json"} if as_json else None,
                                headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.json() if as_json else response.text

    def scrape_events(self) -> List[EventCreate]:
        season = SEASON_LINK.search(self.get("/", as_json=False))
        if season is None:
            raise ValueError(f"{self.source_name}: no season page (/YYYYYY-concerts) linked from {SITE}")
        season_html = self.get(season.group(1), as_json=True).get("mainContent") or ""
        programs = list(dict.fromkeys(p for p in PAGE_LINK.findall(season_html) if p != season.group(1)))

        events = []
        for path in programs:
            page = self.get(path, as_json=True)
            events.extend(self.parse_program(path, page))
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_program(self, path: str, page: dict) -> List[EventCreate]:
        name = self.text((page.get("collection") or {}).get("title"))
        soup = BeautifulSoup(page.get("mainContent") or "", "html.parser")
        lines = [self.clean_text(p.get_text(" ")) for p in soup.find_all(["p", "h1", "h2", "h3", "h4"])]
        shows = [(line, PERFORMANCE.match(line)) for line in lines]
        about = " ".join(line for line, m in shows if not m and len(line) > 60)
        events = []
        for line, match in shows:
            if match:
                event = self.parse_performance(name, path, match, about)
                if event:
                    events.append(event)
        return events

    def wall_clock(self, m: re.Match) -> Optional[datetime]:
        hour = int(m.group("hour")) % 12 + (12 if m.group("ampm").lower() == "p" else 0)
        try:
            when = datetime.strptime(f"{m.group('month')} {m.group('day')} {m.group('year')}", "%B %d %Y")
        except ValueError:
            return None
        if when.strftime("%A").lower() != m.group("weekday").lower():
            return None
        return when.replace(hour=hour, minute=int(m.group("minute") or 0))

    def parse_performance(self, name: str, path: str, m: re.Match, about: str) -> Optional[EventCreate]:
        title = f"Sarasa: {name}"
        url = f"{SITE}{path}"
        if not name or not self.is_event(name):
            return None
        start = self.wall_clock(m)
        if start is None:
            logger.warning(f"Skipping '{title}' - '{m.group(0)}' is not a valid date ({url})")
            return None

        # "Friends Meeting House, Cambridge MA" / "Brattleboro Music Center, Brattleboro VT (tickets at ...)"
        place = re.sub(r"\(.*?\)", "", m.group("place")).strip()
        venue, _, city = (part.strip() for part in place.rpartition(","))
        if "friends meeting house" in venue.lower() and city.lower().startswith("cambridge"):
            where = self.HOME
        else:
            where = self.place(title, venue, None, STATE.sub("", city))
        if where is None:
            return None

        description = about if len(about) >= 20 else self.fallback_description(title, where)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=EventCategory.MUSIC,
            source_name=self.source_name,
            source_url=url,
        )
