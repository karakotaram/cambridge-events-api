"""CambridgeSide (mall) — its own events page plus its Eventbrite organizer page.

The two overlap but neither covers the other, so both are read:

  - **cambridgeside.com/events** is a Placewise (Next.js) site. Its page data
    lists each event with an `occurrence_type`: one-off events are
    "time_range" with a UTC start and end ("Pumpkin Pie: A Cake Decorating
    Experience", 2026-11-24T23:30Z = 6:30 PM). Everything else is a standing
    listing — month-long "November Events Calendar" pages (date_range), weekly
    series summarised as one months-long span ("Mahjesty Mahjong: The Tuesday
    Table", Sept 15 to Dec 22), store promotions with no end — and is not an
    event with a time. Only time_range entries no longer than MAX_HOURS are kept.
  - **The Eventbrite organizer** (base_eventbrite) lists each night of the
    weekly series as its own dated event — "Music Bingo" on Nov 18, "Trivia
    Night" on Nov 19 — which the mall's page only describes in prose
    ("Thursdays from 6pm to 8pm"). The owner was adding these by hand.

An Eventbrite event the mall's page already lists is dropped: the mall's own
entries link their Eventbrite ticket page, and the id in that link identifies
it (backed by matching start and title). A series entry's text, which links
that night's ticket page, becomes the Eventbrite event's description, since
Eventbrite's summary is empty.

Every event is at the mall (100 CambridgeSide Place), whatever room it names.
"""
import logging
import re
from datetime import timedelta
from typing import Dict, List, Optional, Set, Tuple

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_eventbrite import BaseEventbriteScraper, event_id, iso_instant, next_data
from src.scrapers.base_feed import Place

logger = logging.getLogger(__name__)

SITE = "https://www.cambridgeside.com"
ORGANIZER = "https://www.eventbrite.com/o/cambridgeside-107372334671"
MAX_HOURS = 12

EVENTBRITE_LINK = re.compile(r"eventbrite\.com/e/(?:[\w-]*-)?(\d{6,})")
AT_MALL = re.compile(r"\b100\s+cambridge\s*side\s+pl", re.I)


def _norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", title.lower()).strip()


class CambridgeSideScraper(BaseEventbriteScraper):
    HOME = Place("CambridgeSide", "100 CambridgeSide Place", "Cambridge", "02141")
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="CambridgeSide", source_url=f"{SITE}/events", page_url=ORGANIZER)

    def place(self, title, venue, street, city, zip_code=None) -> Optional[Place]:
        if street and AT_MALL.search(street):
            return self.HOME
        return super().place(title, venue, street, city, zip_code)

    def scrape_events(self) -> List[EventCreate]:
        own, listed_ids, series_text = self.read_mall_page(self.fetch(self.source_url))
        seen = {(e.start_datetime, _norm(e.title)) for e in own}
        events = list(own)
        for event in self.read_page(self.fetch(self.page_url)):
            eid = event_id(event.source_url)
            if eid in listed_ids or (event.start_datetime, _norm(event.title)) in seen:
                continue
            if eid in series_text:
                event = event.model_copy(update={"description": series_text[eid][:2000]})
            events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- the mall's own page ----------------------------------------------- #

    def read_mall_page(self, html: str) -> Tuple[List[EventCreate], Set[str], Dict[str, str]]:
        """(events, Eventbrite ids those events link, {Eventbrite id: text} for series entries)."""
        props = ((next_data(html) or {}).get("props") or {}).get("pageProps") or {}
        container = (props.get("sectionsData") or {}).get("events_list_container")
        if container is None or "events" not in container:
            raise ValueError(f"{self.source_name}: no events_list_container in {self.source_url} page data")

        events: List[EventCreate] = []
        listed: Set[str] = set()
        series: Dict[str, str] = {}
        for wrapper in container["events"]:
            item = wrapper.get("event") or {}
            body = item.get("body") or ""
            ids = set(EVENTBRITE_LINK.findall(body))
            event = self.parse_mall_item(item)
            if event:
                events.append(event)
                listed |= ids
            else:
                text = self.text(body)
                for eid in ids:
                    if len(text) >= 20:
                        series[eid] = text
        return events, listed, series

    def parse_mall_item(self, item: dict) -> Optional[EventCreate]:
        title = self.text(item.get("title"))
        url = f"{SITE}/{item['url'].lstrip('/')}" if item.get("url") else self.source_url
        if not self.is_event(title) or item.get("occurrence_type") != "time_range":
            return None
        occurrence = item.get("occurrence") or {}
        start = iso_instant(occurrence.get("starts_at"))
        end = iso_instant(occurrence.get("ends_at"))
        if start is None or end is None or end <= start or end - start > timedelta(hours=MAX_HOURS):
            logger.info(f"{self.source_name}: skipping '{title}' - {occurrence.get('starts_at')} to "
                        f"{occurrence.get('ends_at')} is a standing listing, not one event")
            return None
        if start.second or start.microsecond:
            logger.warning(f"Skipping '{title}' - start {start} is not on the minute ({url})")
            return None
        description = self.text(item.get("body")) or self.text(item.get("teaser"))
        room = self.text(item.get("location"))
        if len(description) < 20:
            description = f"{title} at {room or 'CambridgeSide'}, CambridgeSide, Cambridge."
        image = item.get("image")
        image = image.get("url") if isinstance(image, dict) else image
        return self.build(title, description, start, end, self.HOME, url,
                          image=image if isinstance(image, str) and image.startswith("http") else None)
