"""McCarthy's and Toad, Porter Square — an Elfsight event-calendar widget.

mccarthystoad.com/calendar embeds Elfsight's Event Calendar. The widget boots
from one public JSON document holding its settings, and the settings hold the
events themselves: each with a local `start` {"date", "time"}, its
`timeZone`, a location id (Toad, Upstairs @ McCarthys, McCarthys Session Table)
and a repeat rule.

**Rooms.** All three rooms are one building, 1920 Massachusetts Avenue (the
widget's address spells it "Massaachusetts"). Toad is listed as Toad; the
upstairs room, the session table and unplaced events (the nightly Irish
sessions) as McCarthy's, the name BostonShows.org lists them under, so the two
sources' copies of a show are recognised as one.

**Recurring events.** The widget repeats the nightly sessions and weekly
residencies from a start date with no end, expanding them relative to the
viewer's clock. A pure scraper has no clock (CLAUDE.md rule 3), so only the
first occurrence of a repeating event is emitted — usually long past, and
dropped by EventValidator. One-off shows, which are most of the calendar, are
read as written.
"""
import logging
from datetime import datetime
from typing import List, Optional
from urllib.parse import quote

import requests

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import EASTERN_ZONES, BaseFeedScraper, Place

logger = logging.getLogger(__name__)

PAGE = "https://www.mccarthystoad.com/calendar"
WIDGET = "3fdd143a-e20d-4e92-9fcb-84d101240a59"
STREET = "1920 Massachusetts Avenue"


class McCarthysToadScraper(BaseFeedScraper):
    HOME = Place("McCarthy's", STREET, "Cambridge", "02140")
    TOAD = Place("Toad", STREET, "Cambridge", "02140")
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="McCarthy's Toad", source_url=PAGE)

    def fetch_settings(self) -> dict:
        response = requests.get(f"https://core.service.elfsight.com/p/boot/?page={quote(PAGE, safe='')}&w={WIDGET}",
                                headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        widget = ((response.json().get("data") or {}).get("widgets") or {}).get(WIDGET) or {}
        settings = (widget.get("data") or {}).get("settings") or {}
        if not isinstance(settings.get("events"), list):
            raise ValueError(f"{self.source_name}: Elfsight widget {WIDGET} returned no events")
        return settings

    def scrape_events(self) -> List[EventCreate]:
        settings = self.fetch_settings()
        rooms = {loc.get("id"): self.text(loc.get("name")) for loc in settings.get("locations") or []}
        events = []
        for item in settings["events"]:
            event = self.parse_item(item, rooms)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    @staticmethod
    def wall_clock(moment) -> Optional[datetime]:
        moment = moment or {}
        try:
            day = datetime.strptime(str(moment.get("date")), "%Y-%m-%d")
            if moment.get("type") == "date":
                return day
            hour, minute = str(moment.get("time")).split(":")
            return day.replace(hour=int(hour), minute=int(minute))
        except (TypeError, ValueError):
            return None

    def room(self, item: dict, rooms: dict) -> Place:
        ids = item.get("location") or []
        names = {rooms.get(i, "") for i in (ids if isinstance(ids, list) else [ids])}
        return self.TOAD if "Toad" in names else self.HOME

    def parse_item(self, item: dict, rooms: dict) -> Optional[EventCreate]:
        title = self.text(item.get("name"))
        if not self.is_event(title):
            return None

        if item.get("timeZone") not in EASTERN_ZONES:
            logger.warning(f"Skipping '{title}' - timezone {item.get('timeZone')!r} is not Eastern")
            return None
        start = self.wall_clock(item.get("start"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date ({PAGE})")
            return None
        all_day = bool(item.get("isAllDay")) or (item.get("start") or {}).get("type") == "date"
        if all_day:
            start = start.replace(hour=0, minute=0)
        end = None if all_day else self.wall_clock(item.get("end"))
        if end is not None and end <= start:
            end = None
        if item.get("repeatPeriod") not in (None, "noRepeat"):
            logger.info(f"{self.source_name}: '{title}' repeats; listing its first date only")

        where = self.room(item, rooms)
        description = self.text(item.get("description"))
        if len(description) < 20:
            description = self.fallback_description(title, where)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.categorize(title),
            source_name=self.source_name,
            source_url=PAGE,
            image_url=(item.get("coverImage") or {}).get("url") if isinstance(item.get("coverImage"), dict) else None,
        )
