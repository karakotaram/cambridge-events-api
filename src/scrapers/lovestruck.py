"""Lovestruck Books, Harvard Square — events from the store's BookManager site.

lovestruckbooks.com is a BookManager storefront (a JavaScript app). Its events
page asks BookManager's customer API for `event/v2/list` over a date range,
after opening an anonymous session (`session/get`), which every visit does.
Each event carries `date` ("20261117"), `start_time`/`end_time` ("19:00:00")
on the store's wall clock, an all-day flag, and a free-text `location_text`.

The older `event/getList` the app also knows returns epoch timestamps that run
one to three hours late against the times the store wrote in the same events'
descriptions, and stops at October 2025. It is not used.

**Where.** `location_text` is blank for events in the shop. When it names a
place, that place is used only if it is one we know in Cambridge or Somerville
(First Parish, for the larger author talks); anything else — Encore Boston
Harbor, WBUR CitySpace — is skipped rather than placed in the shop, and
"Virtual" is listed online. Unlike base_feed, a place with no city is not
assumed to be in the store's own city.
"""
import logging
import re
from datetime import date, datetime, timedelta
from typing import List, Optional

import requests

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

API = "https://api.bookmanager.com/customer/"
WEBSTORE = "9934936"     # window.san on lovestruckbooks.com
STORE_ID = "1237278"     # store_info.id from store/getSettings
WINDOW_DAYS = 120

KNOWN_PLACES = (
    ("first parish", Place("First Parish in Cambridge", "3 Church Street", "Cambridge", "02138")),
    ("lovestruck", None),  # resolved to HOME below
)
ONLINE_PLACE = re.compile(r"^\W*(virtual|online|zoom|livestream)", re.I)
EVENTBRITE = re.compile(r'href="((?:https?://)?(?:www\.)?[\w.-]*eventbrite\.com/[^"]+)"', re.I)


class LovestruckBooksScraper(BaseFeedScraper):
    HOME = Place("Lovestruck Books", "44 Brattle Street", "Cambridge", "02138")
    CATEGORY = EventCategory.LECTURES

    def __init__(self, today: Optional[date] = None):
        super().__init__(source_name="Lovestruck Books", source_url="https://lovestruckbooks.com/events")
        # Only bounds the query window.
        self.today = today

    def call(self, path: str, fields: dict) -> dict:
        response = requests.post(f"{API}{path}", params={"_cb": WEBSTORE},
                                 files={k: (None, str(v)) for k, v in fields.items()},
                                 headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get("error"):
            raise ValueError(f"{self.source_name}: BookManager {path}: {data['error']}")
        return data

    def fetch_events(self, start: date, end: date) -> dict:
        session = self.call("session/get", {"store_id": STORE_ID})["session_id"]
        return self.call("event/v2/list", {
            "store_id": STORE_ID, "session_id": session,
            "start_date": start.strftime("%Y%m%d"), "end_date": end.strftime("%Y%m%d"),
        })

    def scrape_events(self) -> List[EventCreate]:
        today = self.today or date.today()
        data = self.fetch_events(today, today + timedelta(days=WINDOW_DAYS))
        if "rows" not in data:
            raise ValueError(f"{self.source_name}: event/v2/list returned no 'rows'")
        events = [e for e in (self.parse_row(r) for r in data["rows"]) if e]
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- one event -------------------------------------------------------- #

    def where(self, title: str, location: str) -> Optional[Place]:
        location = self.text(location)
        if not location:
            return self.HOME
        if ONLINE_PLACE.search(location):
            return Place("Online", None, self.HOME.city, None)
        for needle, place in KNOWN_PLACES:
            if needle in location.lower():
                return place or self.HOME
        logger.info(f"{self.source_name}: skipping '{title}' - held at {location!r}, not a "
                    f"Cambridge/Somerville place we know")
        return None

    @staticmethod
    def wall_clock(day: str, clock: Optional[str]) -> Optional[datetime]:
        try:
            when = datetime.strptime(str(day), "%Y%m%d")
        except ValueError:
            return None
        if clock is None:
            return when
        m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?$", str(clock))
        if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
            return None
        return when.replace(hour=int(m.group(1)), minute=int(m.group(2)))

    def parse_row(self, row: dict) -> Optional[EventCreate]:
        title = self.text(row.get("title"))
        if not self.is_event(title):
            return None

        all_day = bool(row.get("all_day"))
        start = self.wall_clock(row.get("date"), None if all_day else row.get("start_time"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date (date={row.get('date')!r}, "
                           f"start_time={row.get('start_time')!r}) ({self.source_url})")
            return None
        end = None if all_day else self.wall_clock(row.get("date"), row.get("end_time"))
        if end is not None and end <= start:
            end = None

        place = self.where(title, row.get("location_text"))
        if place is None:
            return None

        raw = row.get("description") or ""
        link = EVENTBRITE.search(raw)
        url = link.group(1) if link else self.source_url
        if url.startswith("www."):
            url = f"https://{url}"
        description = self.text(raw) or self.text(row.get("summary"))
        if len(description) < 20:
            description = f"{title} at {place.venue}."
        category_name = ((row.get("category") or {}).get("name") or "").lower()
        category = self.categorize(title) if "author" not in category_name else EventCategory.LECTURES

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            venue_name=place.venue,
            street_address=place.street,
            city=place.city,
            state="MA",
            zip_code=place.zip_code,
            category=category,
            source_name=self.source_name,
            source_url=url,
            image_url=row.get("image_url") or None,
        )
