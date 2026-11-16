"""Cambridge Art Association — the public event feed of its NeonCRM account.

The calendar the association links (eventCalendarBig.jsp, now
/nx/portal/event-calendar) is a month grid with titles only, its event pages
render client-side behind a token exchange, and its grid numbers months from
0 (November is `month=10`). Its "Event Feed" button is plain RSS:
`/nx/feed?orgId=cambridgeart`, one item per upcoming event.

**When.** Each item ends with a line "12/01/2026 06:30 PM - 08:30 PM ET", and
its pubDate is the same start with a zone ("Tue, 01 Dec 2026 18:30:00 EST").
The two must agree or the item is skipped. Dates written in the description
prose ("Tuesdays from 6:30 - 8:30pm 9/15, 9/22, ...") are never read: the feed
names one occurrence, and that is the one listed.

**Where.** The location is prose after a "Location" label. The association
runs two galleries, both in Cambridge; an item is placed at whichever one its
location names, and skipped when it names neither.
"""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import List, Optional

import requests

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

FEED_URL = "https://cambridgeart.app.neoncrm.com/nx/feed?orgId=cambridgeart"

WHEN = re.compile(
    r"(\d{2}/\d{2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)"
    r"(?:\s*-\s*(?:(\d{2}/\d{2}/\d{4})\s+)?(\d{1,2}:\d{2}\s*[AP]M))?\s*ET",
    re.I,
)
LOCATION = re.compile(r"Location\s*:?\s*(.{3,200})", re.I | re.S)

GALLERIES = (
    (re.compile(r"\bcanal gallery\b|650 e\.? kendall", re.I),
     Place("CAA Canal Gallery", "650 East Kendall Street", "Cambridge", "02142")),
    (re.compile(r"\bkathryn schultz gallery\b|\b25 lowell\b", re.I),
     Place("Kathryn Schultz Gallery", "25 Lowell Street", "Cambridge", "02138")),
)
LABELS = re.compile(r"([^\s.:])(Date ?/? ?Time|Location|Admission Fee|Program Description|Feedback)(?=[A-Z0-9$])")
ONLINE_LOCATION = re.compile(r"^\W*(online|virtual|zoom)\b", re.I)


class CambridgeArtAssociationScraper(BaseFeedScraper):
    HOME = None  # two galleries; an item that names neither is not guessed
    CATEGORY = EventCategory.ARTS_CULTURE

    def __init__(self):
        super().__init__(
            source_name="Cambridge Art Association",
            source_url="https://cambridgeart.app.neoncrm.com/np/clients/cambridgeart/publicaccess/eventCalendarBig.jsp",
        )

    def fetch_feed(self) -> str:
        response = requests.get(FEED_URL, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        root = ET.fromstring(self.fetch_feed().encode("utf-8"))
        channel = root.find("channel")
        if channel is None:
            raise ValueError(f"{self.source_name}: {FEED_URL} is not an RSS feed")
        events = []
        for item in channel.findall("item"):
            event = self.parse_item(item)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    @staticmethod
    def _clock(day: str, clock: str) -> datetime:
        return datetime.strptime(f"{day} {clock.upper().replace(' ', '')}", "%m/%d/%Y %I:%M%p")

    def where(self, title: str, text: str) -> Optional[Place]:
        match = LOCATION.search(text)
        location = match.group(1) if match else ""
        if ONLINE_LOCATION.search(location):
            return Place("Online", None, None, None)
        for pattern, gallery in GALLERIES:
            if pattern.search(location):
                return gallery
        logger.info(f"{self.source_name}: skipping '{title}' - location not one of its galleries "
                    f"({location[:80]!r})")
        return None

    def parse_item(self, item) -> Optional[EventCreate]:
        title = self.text(item.findtext("title"))
        if not self.is_event(title):
            return None
        url = (item.findtext("link") or self.source_url).strip().replace("http://", "https://", 1)
        description_html = item.findtext("description") or ""
        text = self.text(description_html)

        # The feed's own date line is the last one; prose above it may hold others.
        matches = list(WHEN.finditer(text))
        if not matches:
            logger.warning(f"Skipping '{title}' - no 'MM/DD/YYYY hh:mm AM - hh:mm PM ET' line ({url})")
            return None
        day, clock, end_day, end_clock = matches[-1].groups()
        start = self._clock(day, clock)
        end = self._clock(end_day or day, end_clock) if end_clock else None
        if end is not None and end <= start:
            end = None

        try:
            published = to_eastern_naive(parsedate_to_datetime(item.findtext("pubDate") or ""))
        except (TypeError, ValueError):
            published = None
        if published != start:
            logger.warning(f"Skipping '{title}' - date line says {start:%Y-%m-%d %H:%M}, "
                           f"pubDate says {item.findtext('pubDate')!r} ({url})")
            return None

        where = self.where(title, text)
        if where is None:
            return None

        # The description ends with the date line and a registration link.
        # Some items' HTML was flattened before it reached the feed, running
        # each section heading into the text before it ("8:30pmLocationKathryn").
        description = LABELS.sub(r"\1. \2: ", text[:matches[-1].start()]).strip()
        if len(description) < 20:
            description = self.fallback_description(title, where)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city or "Cambridge",
            state="MA",
            zip_code=where.zip_code,
            category=self.categorize(title),
            cost="Free" if re.search(r"\bfree\b", title, re.I) else None,
            source_name=self.source_name,
            source_url=url,
        )
