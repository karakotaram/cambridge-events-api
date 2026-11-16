"""Wix Events, read from the data a Wix page renders into its own HTML.

A Wix site's events widget (the Wix Events app,
140603ad-af8d-84a5-2c80-a0f60cb47351) draws itself in the browser, but the
page's HTML already carries everything the widget will show, in
`<script id="wix-warmup-data" type="application/json">`: per event, a
location with street, city and ZIP, and a schedule given as a UTC instant, the
site's timezone, and the text the widget prints ("November 19, 2026 at 6:30
PM"). No browser and no API token are needed.

Written for batch 2, group H (Metamovements, New School of Music) as a
self-contained reader. A shared Wix base belongs to group C; if theirs reads
Wix Events, these two sources should move onto it.

**What it covers.** Only what the widget renders first. A list widget pages
("hasMore": New School of Music showed its next 15 events, four months, on
2026-11-16); a month-calendar widget carries the months it draws. The widget
is ordered by date, so what is missing is the far future, which arrives as
the date approaches. This is logged, not raised.

**Times.** `startDate` is an instant ("2026-11-19T23:30:00.000Z"). Converted
to Eastern wall clock it must reproduce the date and time the widget prints
(`startDateFormatted`, `startTimeFormatted`); an event where the two disagree
is skipped, as is one whose schedule is TBD or whose `timeZoneId` is not
Eastern. Same rule as somerville_gov: two readings that agree, or nothing.
"""
import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

from bs4 import BeautifulSoup

from src.models.event import EventCreate, to_eastern_naive
from src.scrapers.base_feed import EASTERN_ZONES, LOCAL_CITIES, BaseFeedScraper, Place

logger = logging.getLogger(__name__)

WIX_EVENTS_APP = "140603ad-af8d-84a5-2c80-a0f60cb47351"
CANCELED = 3  # Wix EventStatus: SCHEDULED 0, STARTED 1, ENDED 2, CANCELED 3
ONLINE = 1    # Wix LocationType: VENUE 0, ONLINE 1
STATE_ZIP = re.compile(r"^MA\b\s*(\d{5})?")


class WixEventsPageScraper(BaseFeedScraper):
    """The Wix Events widget(s) on one page of a Wix site."""

    # Where an event's page lives: "<site><EVENT_PATH><slug>". Wix's default
    # is /event-details/; a site can rename it (New School of Music: /events/).
    EVENT_PATH = "/event-details/"
    # Room names the site uses for its own building, which mean HOME.
    HOME_ROOMS: tuple = ()

    def __init__(self, source_name: str, site: str, page: str):
        super().__init__(source_name=source_name, source_url=f"{site}{page}")
        self.site = site.rstrip("/")

    def scrape_events(self) -> List[EventCreate]:
        return self.parse_page(self.fetch_html(self.source_url))

    # -- the page --------------------------------------------------------- #

    def widget_events(self, html: str) -> List[dict]:
        """Every event in the page's Wix Events widgets, once each."""
        script = BeautifulSoup(html, "html.parser").find("script", id="wix-warmup-data")
        if script is None or not script.string:
            raise ValueError(f"{self.source_name}: no wix-warmup-data on {self.source_url} "
                             f"- is it still a Wix page with an events widget?")
        apps = json.loads(script.string).get("appsWarmupData") or {}
        widgets = apps.get(WIX_EVENTS_APP)
        if not widgets:
            raise ValueError(f"{self.source_name}: {self.source_url} has no Wix Events widget data")

        seen: Dict[str, dict] = {}
        for widget_id, widget in widgets.items():
            block = (widget or {}).get("events") or {}
            if block.get("hasMore"):
                logger.info(f"{self.source_name}: widget {widget_id} has more events than it rendered; "
                            f"later ones arrive as they come into its first page")
            for item in block.get("events") or []:
                seen.setdefault(item.get("id") or id(item), item)
        return list(seen.values())

    def parse_page(self, html: str) -> List[EventCreate]:
        events = []
        for item in self.widget_events(html):
            try:
                event = self.parse_item(item)
            except Exception as e:  # one malformed event must not sink the rest
                logger.warning(f"{self.source_name}: could not read {item.get('slug')!r}: {e}")
                continue
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- one event -------------------------------------------------------- #

    @staticmethod
    def _instant(value) -> Optional[datetime]:
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None

    def wall_clock(self, item: dict) -> tuple:
        """(start, end) as naive Eastern, or (None, None) if they cannot be trusted."""
        scheduling = item.get("scheduling") or {}
        config = scheduling.get("config") or {}
        if config.get("scheduleTbd") or config.get("timeZoneId") not in EASTERN_ZONES:
            return None, None
        start_instant = self._instant(config.get("startDate"))
        if start_instant is None or start_instant.tzinfo is None:
            return None, None
        start = to_eastern_naive(start_instant)

        printed = f"{scheduling.get('startDateFormatted')} {scheduling.get('startTimeFormatted')}"
        try:
            shown = datetime.strptime(printed, "%B %d, %Y %I:%M %p")
        except ValueError:
            return None, None
        if shown != start:
            return None, None

        end = None
        end_instant = self._instant(config.get("endDate"))
        if end_instant is not None and end_instant.tzinfo is not None and not config.get("endDateHidden"):
            end = to_eastern_naive(end_instant)
            if end <= start:
                end = None
        return start, end

    def where(self, title: str, location: dict) -> Optional[Place]:
        """The event's place, from the address the widget prints.

        Not from `fullAddress.streetAddress`: New School of Music's concert at
        First Church prints "11 Garden St, Cambridge" while its structured
        street still says the school's own "25 Lowell Street".
        """
        if location.get("type") == ONLINE:
            return Place("Online", None, self.HOME.city if self.HOME else None, None)
        name = self.text(location.get("name"))
        if self.HOME and name in self.HOME_ROOMS:
            return self.HOME

        full = location.get("fullAddress") or {}
        printed = self.text(location.get("address") or full.get("formattedAddress"))
        parts = [p.strip() for p in printed.split(",")]
        # "11 Garden St, Cambridge, MA 02138, USA": the town precedes "MA <zip>"
        state_at = next((i for i, p in enumerate(parts) if STATE_ZIP.match(p)), None)
        if state_at is None or state_at < 1:
            return self.place(title, name, None, full.get("city"), str(full.get("postalCode") or ""))
        city = parts[state_at - 1]
        street = parts[state_at - 2] if state_at >= 2 else None
        structured_city = self.text(full.get("city"))
        if structured_city and structured_city.lower() != city.lower():
            # Two readings of the town disagree. Elsewhere ("Roxbury" vs
            # "Boston") that is a neighbourhood name and out of area anyway.
            if {city.title(), structured_city.title()} & set(LOCAL_CITIES):
                logger.warning(f"Skipping '{title}' - address says {city!r}, structured city {structured_city!r}")
            return None
        return self.place(title, name, street, city, parts[state_at])

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        title = self.text(item.get("title"))
        if not self.is_event(title):
            return None
        url = f"{self.site}{self.EVENT_PATH}{item['slug']}" if item.get("slug") else self.source_url
        if item.get("status") == CANCELED:
            logger.info(f"{self.source_name}: skipping canceled '{title}'")
            return None

        start, end = self.wall_clock(item)
        if start is None:
            scheduling = item.get("scheduling") or {}
            logger.warning(f"Skipping '{title}' - start {((scheduling.get('config') or {}).get('startDate'))!r} "
                           f"does not match the printed {scheduling.get('formatted')!r} ({url})")
            return None

        where = self.where(title, item.get("location") or {})
        if where is None:
            return None

        description = self.text(item.get("description")) or self.text(item.get("about"))
        if len(description) < 20:
            description = self.fallback_description(title, where)

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
            category=self.categorize(title),
            source_name=self.source_name,
            source_url=url,
            image_url=((item.get("mainImage") or {}).get("url")) or None,
        )
