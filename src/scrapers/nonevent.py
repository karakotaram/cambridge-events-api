"""Non-Event — an experimental-music series, read from its Squarespace blog.

Concerts are posts in the /concerts blog collection, not an events collection,
so there is no start field. Each post's body states the performance on one
line — "friday, october 23, 2026 at 6:30pm", "Saturday, APRIL 18, 2026 7:30pm"
— and its location block names the room and city. The editors also set each
post's publish date to the concert day, which is used only as a check: a
post whose body date, weekday and publish date do not all agree is skipped.
On 2026-11-16 three of twenty posts failed that check: two whose bodies carry
the wrong year ("sunday, march 22, 2025" on a March 2026 concert) and the
two-day Waterworks festival ("Friday, April 24, 2026 | Doors at 7pm"). All
three were outside the area anyway.

The series is Boston-based; most concerts are in Boston and are dropped. The
Cambridge ones (MIT List, CAMlab, MIT Music, The Foundry) are kept.
"""
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_squarespace import BaseSquarespaceScraper

logger = logging.getLogger(__name__)

WHEN = re.compile(
    r"\b(?P<weekday>monday|tuesday|wednesday|thursday|friday|saturday|sunday),?\s+"
    r"(?P<month>[a-z]+)\s+(?P<day>\d{1,2}),?\s+(?P<year>\d{4}),?\s+(?:at\s+)?"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d\d))?\s*(?P<ampm>[ap])\.?m\b",
    re.I)


class NonEventScraper(BaseSquarespaceScraper):
    HOME = None
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Non-Event", site="https://www.nonevent.org", collection="/concerts")

    def scrape_events(self) -> List[EventCreate]:
        payload = self.fetch_collection()
        if not isinstance(payload.get("items"), list):
            raise ValueError(f"{self.source_name}: {self.source_url} is no longer a blog collection")
        events = []
        for item in payload["items"]:
            event = self.parse_item(item)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    @staticmethod
    def stated_start(text: str) -> Optional[datetime]:
        m = WHEN.search(text)
        if m is None:
            return None
        try:
            day = datetime.strptime(f"{m.group('month')} {m.group('day')} {m.group('year')}", "%B %d %Y")
        except ValueError:
            return None
        if day.strftime("%A").lower() != m.group("weekday").lower():
            return None
        hour = int(m.group("hour")) % 12 + (12 if m.group("ampm").lower() == "p" else 0)
        return day.replace(hour=hour, minute=int(m.group("minute") or 0))

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        title = self.text(item.get("title"))
        if not self.is_event(title):
            return None
        url = f"{self.site}{item['fullUrl']}" if item.get("fullUrl") else self.source_url

        loc = item.get("location") or {}
        where = self.place(title, loc.get("addressTitle"), loc.get("addressLine1"), loc.get("addressLine2"))
        if where is None:
            return None

        body = self.body_text(item.get("body"))
        start = self.stated_start(body)
        published = item.get("publishOn")
        posted_day = (to_eastern_naive(datetime.fromtimestamp(published / 1000, tz=timezone.utc)).date()
                      if isinstance(published, (int, float)) else None)
        if start is None or start.date() != posted_day:
            logger.warning(f"Skipping '{title}' - no performance date that agrees with the post "
                           f"(body: {start}, posted for: {posted_day}) ({url})")
            return None

        description = body or self.text(item.get("excerpt"))
        if len(description) < 20:
            description = self.fallback_description(title, where)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.categorize(title),
            source_name=self.source_name,
            source_url=url,
            image_url=item.get("assetUrl") or None,
        )
