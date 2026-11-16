"""Eventbrite organizers and collections, read from the public page — no API key.

Eventbrite's v3 API needs a private token, and its organizer/collection search
endpoints are not public. The public pages carry two machine-readable copies
of their events, and this reader uses each for what it is good at:

  - **schema.org JSON-LD** (`<script type="application/ld+json">`, one `Event`
    per listing on a collection page and on every event page). This is the
    published contract — search engines consume it — and it is the only copy
    on a collection page that says *where* an event is: `location.address`
    with `addressLocality`. Times are ISO 8601 with an offset
    ("2026-12-12T14:00:00-0500"), so they convert to Eastern exactly.
  - **The Next.js page data** (`<script id="__NEXT_DATA__">`). Internal and
    liable to change shape, but it is where Eventbrite itself splits
    *upcoming* from *past* and marks status ("live", "completed", "canceled"),
    listing and privacy. The JSON-LD on a collection page mixes past and
    upcoming events and has no status, and a scraper may not consult the clock
    (CLAUDE.md rule 3) — so "future" has to come from Eventbrite's own split.

A collection page (`/cc/<slug>-<id>`) is read as: the events Eventbrite lists
under `events_in_collection.upcoming`, live, listed and not locked, each placed
and timed from its JSON-LD (the two copies' start instants must agree). An
organizer page (`/o/<slug>-<id>`) has no JSON-LD; its `upcomingEvents` records
carry their own venue (`primary_venue.address.city`) and a local start with its
`timezone`, which is converted to Eastern. Their `end_time` repeats the start
time, so it is not used.

If the page data cannot be found the scrape fails loudly rather than falling
back to the JSON-LD alone, which would publish past events as current; a
failed source keeps its existing listings (scrape.py's build_publish_set).

**Where.** base_feed's rules: Cambridge and Somerville only. Online events are
dropped unless a subclass sets KEEP_ONLINE — an Eventbrite organizer is
rarely a Cambridge venue, so its webinars are not Cambridge events.

Eventbrite shows one page of results (on 2026-11-16: 24 upcoming events on a
collection page, 12 on an organizer page; `?page=2` is ignored). Both are
ordered soonest first, so what is cut off is the far future, which arrives on
later days.
"""
import json
import logging
import re
from datetime import datetime
from typing import Dict, Iterable, List, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__" type="application/json"[^>]*>(.*?)</script>', re.S)
EVENT_ID = re.compile(r"(\d{6,})/?(?:\?.*)?$")

# Collection statuses that mean the event is on: "started" is today's, in progress.
LIVE = {"live", "started"}


def jsonld_events(html: str) -> List[dict]:
    """Every schema.org Event in a page's JSON-LD, however it is nested."""
    found: List[dict] = []
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except (TypeError, ValueError):
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                kind = node.get("@type")
                kinds = kind if isinstance(kind, list) else [kind]
                if any(isinstance(k, str) and k.endswith("Event") for k in kinds):
                    found.append(node)
                for key in ("@graph", "itemListElement", "item"):
                    if key in node:
                        stack.append(node[key])
    return found


def iso_instant(value) -> Optional[datetime]:
    """'2026-12-12T14:00:00-0500' -> naive Eastern. None without an offset:
    a JSON-LD time with no zone is a guess, and is not made."""
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = date_parser.isoparse(value)
    except (ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        return None
    return to_eastern_naive(parsed)


def event_id(url: Optional[str]) -> Optional[str]:
    m = EVENT_ID.search(url or "")
    return m.group(1) if m else None


def next_data(html: str) -> Optional[dict]:
    m = NEXT_DATA.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


class BaseEventbriteScraper(BaseFeedScraper):
    """One Eventbrite collection or organizer page."""

    KEEP_ONLINE = False

    def __init__(self, source_name: str, source_url: str, page_url: Optional[str] = None):
        super().__init__(source_name=source_name, source_url=source_url)
        self.page_url = page_url or source_url

    def fetch(self, url: str) -> str:
        response = requests.get(url, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        events = self.read_page(self.fetch(self.page_url))
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def read_page(self, html: str) -> List[EventCreate]:
        props = ((next_data(html) or {}).get("props") or {}).get("pageProps") or {}
        collection = (props.get("serverData") or {}).get("events_in_collection")
        if collection is not None:
            return self.read_collection(collection, jsonld_events(html))
        if "upcomingEvents" in props:
            if props.get("hasMoreUpcoming"):
                logger.info(f"{self.source_name}: organizer has more upcoming events than its first page")
            return [e for e in (self.from_organizer_record(r) for r in props["upcomingEvents"] or []) if e]
        raise ValueError(f"{self.source_name}: no Eventbrite page data at {self.page_url} — "
                         f"has the page changed shape?")

    # -- collection pages -------------------------------------------------- #

    def read_collection(self, collection: dict, ld: Iterable[dict]) -> List[EventCreate]:
        upcoming = (collection.get("upcoming") or {})
        if (upcoming.get("pagination") or {}).get("has_more_items"):
            logger.info(f"{self.source_name}: collection has more upcoming events than its first page")
        by_id: Dict[str, dict] = {}
        for item in ld:
            if event_id(item.get("url")):
                by_id[event_id(item.get("url"))] = item

        events = []
        for record in upcoming.get("events") or []:
            title = self.text((record.get("name") or {}).get("text"))
            if record.get("status") not in LIVE or not record.get("listed") \
                    or record.get("privacy_setting", "unlocked") != "unlocked" or record.get("hide_start_date"):
                logger.info(f"{self.source_name}: skipping '{title}' - status {record.get('status')}, "
                            f"listed {record.get('listed')}, privacy {record.get('privacy_setting')}")
                continue
            item = by_id.get(str(record.get("id")))
            if item is None:
                logger.warning(f"Skipping '{title}' - no JSON-LD for event {record.get('id')}, so no venue")
                continue
            event = self.from_jsonld(item, record)
            if event:
                events.append(event)
        return events

    # -- organizer pages --------------------------------------------------- #

    def from_organizer_record(self, record: dict) -> Optional[EventCreate]:
        title = self.text(record.get("name"))
        url = record.get("url") or self.source_url
        if not self.is_event(title):
            return None
        if record.get("is_cancelled") or record.get("is_protected_event") or record.get("hide_start_date"):
            logger.info(f"{self.source_name}: skipping '{title}' - cancelled, protected, or date hidden")
            return None
        try:
            local = datetime.strptime(f"{record['start_date']} {record['start_time']}", "%Y-%m-%d %H:%M:%S")
            start = to_eastern_naive(local.replace(tzinfo=ZoneInfo(record["timezone"])))
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
            logger.warning(f"Skipping '{title}' - no usable start ({record.get('start_date')!r} "
                           f"{record.get('start_time')!r} {record.get('timezone')!r}) ({url})")
            return None
        if start.second:
            logger.warning(f"Skipping '{title}' - start {start} is not on the minute ({url})")
            return None

        if record.get("is_online_event"):
            if not self.KEEP_ONLINE:
                return None
            where: Optional[Place] = Place("Online", None, self.HOME.city if self.HOME else None, None)
        else:
            venue = record.get("primary_venue") or {}
            address = venue.get("address") or {}
            where = self.place(title, venue.get("name"), address.get("address_1"),
                               address.get("city"), address.get("postal_code"))
        if where is None:
            return None

        description = self.text(record.get("summary"))
        if len(description) < 20:
            description = self.fallback_description(title, where)
        tickets = record.get("ticket_availability") or {}
        price = (tickets.get("minimum_ticket_price") or {}).get("display")
        return self.build(title, description, start, None, where, url,
                          cost="Free" if tickets.get("is_free") else price,
                          image=(record.get("image") or {}).get("url"))

    # -- schema.org Event -------------------------------------------------- #

    def jsonld_place(self, title: str, item: dict) -> Optional[Place]:
        mode = str(item.get("eventAttendanceMode") or "")
        status = str(item.get("eventStatus") or "")
        locations = item.get("location")
        locations = locations if isinstance(locations, list) else [locations]
        physical = [loc for loc in locations if isinstance(loc, dict) and loc.get("@type") == "Place"]
        if not physical or "OnlineEventAttendanceMode" in mode or "EventMovedOnline" in status:
            if not self.KEEP_ONLINE:
                logger.info(f"{self.source_name}: skipping online event '{title}'")
                return None
            return Place("Online", None, self.HOME.city if self.HOME else None, None)
        loc = physical[0]
        address = loc.get("address") or {}
        if isinstance(address, str):
            return self.place(title, loc.get("name"), address, None)
        return self.place(title, loc.get("name"), address.get("streetAddress"),
                          address.get("addressLocality"), address.get("postalCode"))

    def from_jsonld(self, item: dict, record: Optional[dict] = None) -> Optional[EventCreate]:
        """A schema.org Event (Eventbrite's, or another ticketing site's) -> EventCreate.

        With a collection `record`, its UTC start must name the same instant.
        """
        title = self.text(item.get("name"))
        url = item.get("url") or self.source_url
        if not self.is_event(title):
            return None
        status = str(item.get("eventStatus") or "")
        if "EventCancelled" in status or "EventPostponed" in status:
            logger.info(f"{self.source_name}: skipping '{title}' - {status}")
            return None

        start = iso_instant(item.get("startDate"))
        if start is None:
            logger.warning(f"Skipping '{title}' - startDate {item.get('startDate')!r} has no usable offset ({url})")
            return None
        if record is not None:
            server = iso_instant((record.get("start") or {}).get("utc"))
            if server != start:
                logger.warning(f"Skipping '{title}' - JSON-LD start {start} and page data {server} disagree ({url})")
                return None
        if start.second:
            logger.warning(f"Skipping '{title}' - start {start} is not on the minute ({url})")
            return None
        end = iso_instant(item.get("endDate"))
        if end is not None and end <= start:
            end = None

        where = self.jsonld_place(title, item)
        if where is None:
            return None

        description = self.text(item.get("description"))
        if len(description) < 20:
            description = self.fallback_description(title, where)
        image = item.get("image")
        if isinstance(image, list):
            image = next((i for i in image if isinstance(i, str)), None)
        cost = None
        if record is not None and record.get("is_free"):
            cost = "Free"
        return self.build(title, description, start, end, where, url, cost=cost,
                          image=image if isinstance(image, str) else None)

    def build(self, title: str, description: str, start: datetime, end: Optional[datetime], where: Place,
              url: str, cost: Optional[str] = None, image: Optional[str] = None,
              category: Optional[EventCategory] = None) -> EventCreate:
        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=category or self.categorize(title),
            cost=cost,
            source_name=self.source_name,
            source_url=url.split("?")[0],
            image_url=image,
        )
