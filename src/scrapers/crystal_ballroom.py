"""Crystal Ballroom (at the Somerville Theatre, Davis Square) — the events page.

WordPress with a theme-defined `event` post type that the REST API does not
expose (`/wp-json/wp/v2/event` answers with zero pages). The /events/ page
renders every upcoming show server-side, one `article.event` each:

    Lee Emi
    Sat, Nov 14, 2026      Show 8:00 pm | Doors 7:00 pm      All Ages

The listing is one page (no pagination) and carries the year, so nothing is
inferred. The printed weekday must agree with the date. A listing without a
show time falls back to its door time; with neither it is an all-day listing.
"Festival pass" listings are tickets, not events, and are skipped.

The shows also reach the calendar through BostonShows.org; this source is the
venue's own word, and outranks the aggregator in deduplication.
"""
import logging
import re
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place
from src.scrapers.base_printed_date import at, clock, dated, month_number, weekday_number

logger = logging.getLogger(__name__)

SITE = "https://www.crystalballroomboston.com"

DATE = re.compile(r"^\s*([a-z]{3,9})\.?,?\s+([a-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})\s*$", re.I)
SHOW = re.compile(r"\bshow\b(.*?)(?=\bdoors\b|$)", re.I)
DOORS = re.compile(r"\bdoors\b(.*?)(?=\bshow\b|$)", re.I)
TICKET_ONLY = re.compile(r"\b(festival|season|weekend|day|vip) pass\b", re.I)


class CrystalBallroomScraper(BaseFeedScraper):
    HOME = Place("Crystal Ballroom", "55 Davis Square", "Somerville", "02144")
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Crystal Ballroom", source_url=f"{SITE}/events/")

    def fetch(self) -> str:
        response = requests.get(self.source_url, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        soup = BeautifulSoup(self.fetch(), "html.parser")
        listings = soup.select("#event-listings article.event")
        if not listings:
            raise ValueError(f"{self.source_name}: no listings on {self.source_url} — page layout changed?")
        events = [e for e in (self.parse_listing(a) for a in listings) if e]
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_listing(self, article) -> Optional[EventCreate]:
        link = article.select_one(".entry-title a")
        title = self.text(link.get_text(" ")) if link else ""
        if not self.is_event(title):
            return None
        if TICKET_ONLY.search(title):
            logger.info(f"{self.source_name}: skipping ticket listing '{title}'")
            return None
        url = link.get("href") or self.source_url

        meta = [self.text(span.get_text(" ")) for span in article.select(".event-meta > span")]
        day = None
        for text in meta:
            match = DATE.match(text)
            if match:
                weekday, month, dom, year = match.groups()
                month_no = month_number(month)
                day = dated(int(year), month_no, int(dom), weekday_number(weekday)) if month_no else None
                break
        if day is None:
            logger.warning(f"Skipping '{title}' - no parseable date in {meta!r} ({url})")
            return None

        times = next((t for t in meta if re.search(r"\b(show|doors)\b", t, re.I)), "")
        show = SHOW.search(times)
        doors = DOORS.search(times)
        time = clock(show.group(1)) if show else None
        door_time = clock(doors.group(1)) if doors else None
        start_time = time or door_time

        ages = next((t for t in meta if re.search(r"\b(all ages|\d{2}\+)", t, re.I)), "")
        age = re.search(r"all ages|\d{2}\+", ages, re.I)
        details = []
        if door_time and time:
            details.append(f"Doors {self.hhmm(door_time)}, show {self.hhmm(time)}.")
        if ages:
            # "All Ages" and "Standing room only" are separate spans.
            notes = next(span for span in article.select(".event-meta > span") if self.text(span.get_text(" ")) == ages)
            details.append(". ".join(self.text(s) for s in notes.stripped_strings) + ".")
        description = " ".join([f"{title} at the Crystal Ballroom, Somerville."] + details)

        image = article.select_one(".event-grid-header img")
        image_url = image.get("data-src") or image.get("src") if image else None
        if not image_url or "no-photo" in image_url or image_url.startswith("data:"):
            image_url = None

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=at(day, start_time),
            all_day=start_time is None,
            venue_name=self.HOME.venue,
            street_address=self.HOME.street,
            city=self.HOME.city,
            state="MA",
            zip_code=self.HOME.zip_code,
            category=self.categorize(title),
            age_restrictions=age.group(0) if age else None,
            source_name=self.source_name,
            source_url=url,
            image_url=image_url,
        )

    @staticmethod
    def hhmm(time) -> str:
        hour, minute = time
        suffix = "pm" if hour >= 12 else "am"
        return f"{(hour % 12) or 12}:{minute:02d} {suffix}"
