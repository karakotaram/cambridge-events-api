"""MIT Events, read from calendar.mit.edu's Localist API

calendar.mit.edu is Localist. Its homepage JSON-LD, which this scraper used to
read through Playwright, carries only today's and featured events - about 5% of
the calendar. The API carries all of it:

    https://calendar.mit.edu/api/2/events?days=60&pp=100&page=N

returns one item per occurrence in the window, paged (`page.total`). Each
item's `event_instances[].event_instance` holds `start` as ISO 8601 with an
offset ("2026-10-06T18:00:00-04:00") and an `all_day` flag.

Most of the calendar is internal: seminars, office hours, and socials for the
MIT community. Only events open to the general public belong here. MIT records
that in the "Events By Audience" filter (`filters.event_audience`), whose
values are Public, MIT Community, Students, Alumni, Faculty and Staff. An event
is published when:

  - its audience includes "Public", or its description says it is open to the
    public ("Free and open to the public") - some are untagged; and
  - its description does not restrict it ("open the MIT Community only",
    "invite-only", "not open to the public") - one event tagged Public says so.

Institute Holidays (Veterans Day, Thanksgiving) are closures, not events.
"""
import html
import logging
import re
from datetime import datetime
from typing import Iterable, List, Optional

import requests

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper, USER_AGENT

logger = logging.getLogger(__name__)

API = "https://calendar.mit.edu/api/2/events"

# Server-side window. ~7 pages of 100 as of 2026-10; the cap only stops a
# pager that never ends.
WINDOW_DAYS = 60
PAGE_SIZE = 100
MAX_PAGES = 30

PUBLIC_AUDIENCE = "Public"

OPEN_TO_PUBLIC = re.compile(
    r"(?<!not )(?<!n't )open\s+to\s+the\s+(?:general\s+)?public|public\s+is\s+welcome", re.IGNORECASE)
# Statements about who may attend - not about who gets in free. "Free for MIT
# students only" describes a price, and must not hide a public event.
RESTRICTED = re.compile(
    r"\bopen\s+(?:only\s+)?(?:to\s+)?(?:the\s+)?(?:mit|harvard)\s+"
    r"(?:community|students|affiliates|faculty|staff)(?:\s+members)?\s+only"
    r"|\b(?:only\s+open|open\s+only)\s+to\s+(?:the\s+)?mit\b"
    r"|\brestricted\s+to\s+(?:the\s+)?mit\b"
    r"|\binvite[\s-]+only"
    r"|(?:not|n't)\s+open\s+to\s+the\s+(?:general\s+)?public"
    r"|\bclosed\s+to\s+the\s+public"
    r"|\bmit\s+(?:id|kerberos|certificate)\s+(?:is\s+)?required", re.IGNORECASE)

NOT_EVENTS = {"Institute Holidays"}

CATEGORY_BY_TYPE = {
    "Conferences/Seminars/Lectures": EventCategory.LECTURES,
    "Career Development": EventCategory.LECTURES,
    "Workshops/Fairs": EventCategory.LECTURES,
    "Thesis defense": EventCategory.LECTURES,
    "Exhibits": EventCategory.ARTS_CULTURE,
    "Campus Tours": EventCategory.ARTS_CULTURE,
    "Athletics/Recreation": EventCategory.SPORTS,
    "Community Event": EventCategory.COMMUNITY,
    "Meetings/Gatherings": EventCategory.COMMUNITY,
}


class MITCalendarScraper(BaseScraper):
    """Public events from the MIT Events calendar (calendar.mit.edu)"""

    def __init__(self):
        super().__init__(
            source_name="MIT Events",
            source_url="https://calendar.mit.edu/",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()
        page, total = 1, 1
        while page <= total and page <= MAX_PAGES:
            body = self._fetch(page)
            total = int((body.get("page") or {}).get("total") or 0)
            for event in self.parse_items(x.get("event") or {} for x in body.get("events") or []):
                # Departments re-post events ("copy-of-copy-of-symplectic-
                # geometry-seminar"), so identity is what, when and where.
                key = (event.title.lower(), event.start_datetime, event.venue_name)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
            page += 1
        if page <= total:
            logger.warning(f"MIT Events: stopped at {MAX_PAGES} of {total} pages")
        logger.info(f"Scraped {len(events)} public events from {self.source_name}")
        return events

    def _fetch(self, page: int) -> dict:
        response = requests.get(
            API,
            params={"days": WINDOW_DAYS, "pp": PAGE_SIZE, "page": page},
            timeout=30,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        return response.json()

    @staticmethod
    def is_public(item: dict) -> bool:
        """Open to the general public, by MIT's audience tag or its own words."""
        audiences = {a.get("name") for a in (item.get("filters") or {}).get("event_audience") or []}
        text = f"{item.get('title') or ''}\n{item.get('description_text') or ''}"
        if RESTRICTED.search(text):
            return False
        return PUBLIC_AUDIENCE in audiences or bool(OPEN_TO_PUBLIC.search(text))

    def parse_items(self, items: Iterable[dict]) -> List[EventCreate]:
        events = []
        for item in items:
            try:
                events.extend(self.parse_item(item))
            except Exception as e:
                logger.warning(f"Failed to parse MIT Events item {item.get('id')}: {e}")
        return events

    def parse_item(self, item: dict) -> List[EventCreate]:
        """One EventCreate per instance of a public event."""
        title = self._clean(item.get("title"))
        url = (item.get("localist_url") or "").strip()
        if not title or not url.startswith("http"):
            return []

        types = [t.get("name") for t in (item.get("filters") or {}).get("event_types") or []]
        if NOT_EVENTS.intersection(types) or not self.is_public(item):
            return []

        description = self._clean(item.get("description_text")) or f"{title} at MIT"
        venue_name, street, city, state, zip_code, lat, lng = self._location(item)
        category = self._category(title, types)
        cost = self._cost(item)
        image_url = item.get("photo_url") or None

        events = []
        for wrapper in item.get("event_instances") or []:
            instance = wrapper.get("event_instance") or {}
            start = self._instant(instance.get("start"))
            if start is None:
                logger.warning(f"Skipping an instance of '{title}' - unreadable start "
                               f"{instance.get('start')!r} ({url})")
                continue
            end = self._instant(instance.get("end"))
            all_day = bool(instance.get("all_day"))
            events.append(EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start,
                end_datetime=end if end and end > start and not all_day else None,
                all_day=all_day,
                venue_name=venue_name,
                street_address=street,
                city=city,
                state=state,
                zip_code=zip_code,
                latitude=lat,
                longitude=lng,
                category=category,
                cost=cost,
                source_url=url,
                source_name=self.source_name,
                website_url=(item.get("url") or None),
                image_url=image_url,
            ))
        return events

    @staticmethod
    def _instant(value) -> Optional[datetime]:
        """ISO 8601 with an offset. The model converts it to naive Eastern."""
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return None
        if dt.tzinfo is None or dt.second or dt.microsecond:
            # Localist always sends an offset and whole minutes; anything else
            # means the field changed meaning.
            return None
        return dt

    def _location(self, item: dict) -> tuple:
        """(venue, street, city, state, zip, lat, lng). Virtual events are "Online"."""
        if item.get("experience") == "virtual":
            return "Online", None, None, None, None, None, None

        geo = item.get("geo") or {}
        name = self._clean(item.get("location_name")) or self._clean(item.get("location"))
        room = self._clean(item.get("room_number"))
        if name and room:
            # "449" -> "Building 2, Room 449"; "Thomas Tull Concert Hall" as is
            name = f"{name}, Room {room}" if re.match(r"^[A-Z]?-?\d", room) else f"{name}, {room}"
        street = self._clean(geo.get("street")) or None
        city = self._clean(geo.get("city")) or None
        zip_code = self._clean(geo.get("zip")) or None
        try:
            lat = float(geo["latitude"]) if geo.get("latitude") else None
            lng = float(geo["longitude"]) if geo.get("longitude") else None
        except (TypeError, ValueError):
            lat = lng = None
        state = (self._clean(geo.get("state")) or None) if city else None
        if state and len(state) != 2:
            state = None
        return (name or "MIT")[:150], street, city, state, zip_code, lat, lng

    @staticmethod
    def _category(title: str, types: list) -> EventCategory:
        for name in types:
            if name == "Performing Arts":
                lowered = title.lower()
                if any(w in lowered for w in ("concert", "music", "orchestra", "ensemble", "choir", "jazz")):
                    return EventCategory.MUSIC
                if any(w in lowered for w in ("theater", "theatre", "play", "dance")):
                    return EventCategory.THEATER
                return EventCategory.ARTS_CULTURE
            if name in CATEGORY_BY_TYPE:
                return CATEGORY_BY_TYPE[name]
        return EventCategory.OTHER

    def _cost(self, item: dict) -> Optional[str]:
        raw = self._clean(str(item.get("ticket_cost") or ""))
        if raw and raw not in ("0", "$0"):
            return (raw[:1].upper() + raw[1:])[:100]
        if item.get("free") or raw in ("0", "$0"):
            return "Free"
        return None

    @staticmethod
    def _clean(value) -> str:
        if not value or not isinstance(value, str):
            return ""
        return " ".join(html.unescape(value).split())
