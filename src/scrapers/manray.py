"""ManRay, Central Square — club nights from the venue's monthly calendar.

ManRay's site runs The Events Calendar, but posts nothing to it; its nights are
kept in a Google Calendar and shown on /monthly-calendar/ by the Simple
Calendar plugin, which writes every occurrence into the page as a grid cell
with its start and end as epoch seconds (`data-event-start`, `data-event-end`).

The page holds the month it opens on. The following month comes from the
plugin's own grid request (admin-ajax `simcal_default_calendar_draw_grid`), for
the month after the one the page reports in `data-calendar-current` — the
page's month, not the clock's.

**Times.** The WordPress site is set to America/Halifax, an hour ahead of
Eastern, so the clock it prints is wrong: HEROES shows "10:00 pm" for a night
whose description says "Doors 9pm", and Karaoke Noir "9:00 pm" for "Doors 8p".
The epoch is the true instant and is what is used, converted to Eastern; the
plugin's `itemprop="startDate"` stamp must name the same instant or the night
is skipped.

A night spanning midnight is drawn in both days' cells; occurrences are
deduplicated by title and start.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

SITE = "https://manrayclub.com"


class ManRayScraper(BaseFeedScraper):
    HOME = Place("ManRay", "40 Prospect Street", "Cambridge", "02139")
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="ManRay", source_url=f"{SITE}/monthly-calendar/")

    def fetch_page(self) -> str:
        response = requests.get(self.source_url, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.text

    def fetch_month(self, calendar_id: str, year: int, month: int) -> str:
        response = requests.post(
            f"{SITE}/wp-admin/admin-ajax.php",
            data={"action": "simcal_default_calendar_draw_grid", "month": month, "year": year, "id": calendar_id},
            headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("success"):
            raise ValueError(f"{self.source_name}: calendar refused month {year}-{month:02d}")
        return payload.get("data") or ""

    def scrape_events(self) -> List[EventCreate]:
        page = self.fetch_page()
        soup = BeautifulSoup(page, "html.parser")
        calendar = soup.find(attrs={"data-calendar-id": True, "data-timezone": True})
        if calendar is None:
            raise ValueError(f"{self.source_name}: no Simple Calendar grid on {self.source_url}")

        events = self.parse_grid(page)
        following = self.next_month(calendar, soup)
        if following:
            try:
                events += self.parse_grid(self.fetch_month(calendar["data-calendar-id"], *following))
            except Exception as e:
                logger.warning(f"{self.source_name}: could not read {following[0]}-{following[1]:02d}: {e}")

        unique, seen = [], set()
        for event in events:
            key = (event.title, event.start_datetime)
            if key not in seen:
                seen.add(key)
                unique.append(event)
        if not unique:
            raise ValueError(f"{self.source_name}: the calendar grid held no nights")
        logger.info(f"Scraped {len(unique)} events from {self.source_name}")
        return unique

    def next_month(self, calendar, soup) -> Optional[Tuple[int, int]]:
        """The month after the one the page shows.

        `data-calendar-current` is midnight on the 1st in the calendar's own
        zone (`data-timezone`, America/Halifax) — still the previous evening in
        Eastern — so it is read in that zone, and must agree with the grid's
        `simcal-month-N` class.
        """
        try:
            zone = ZoneInfo(calendar.get("data-timezone") or "UTC")
            current = calendar.find(attrs={"data-calendar-current": True})
            shown = datetime.fromtimestamp(int(current["data-calendar-current"]), tz=zone)
        except (KeyError, TypeError, ValueError):
            return None
        grid = soup.find("tbody", class_=f"simcal-month-{shown.month}")
        if grid is None:
            logger.warning(f"{self.source_name}: calendar reports {shown:%Y-%m} but draws another month; "
                           f"not reading the next one")
            return None
        return (shown.year + 1, 1) if shown.month == 12 else (shown.year, shown.month + 1)

    # -- one grid --------------------------------------------------------- #

    @staticmethod
    def instant(epoch) -> Optional[datetime]:
        try:
            return datetime.fromtimestamp(int(epoch), tz=timezone.utc)
        except (TypeError, ValueError):
            return None

    def parse_grid(self, markup: str) -> List[EventCreate]:
        soup = BeautifulSoup(markup, "html.parser")
        events = []
        for item in soup.select("li.simcal-event"):
            event = self.parse_item(item)
            if event:
                events.append(event)
        return events

    def parse_item(self, item) -> Optional[EventCreate]:
        title_tag = item.select_one(".simcal-event-title")
        title = self.clean_text(title_tag.get_text(" ")) if title_tag else ""
        if not self.is_event(title):
            return None

        start_tag = item.select_one(".simcal-event-start-date[data-event-start]")
        start = self.instant(start_tag["data-event-start"]) if start_tag else None
        stamp = start_tag.get("content") if start_tag else None
        try:
            stamped = datetime.fromisoformat(stamp) if stamp else None
        except ValueError:
            stamped = None
        if start is None or stamped is None or stamped.tzinfo is None or stamped != start:
            logger.warning(f"Skipping '{title}' - no parseable date (data-event-start="
                           f"{start_tag.get('data-event-start') if start_tag else None!r}, content={stamp!r}) "
                           f"({self.source_url})")
            return None
        end_tag = item.select_one(".simcal-event-end-time[data-event-end]")
        end = self.instant(end_tag["data-event-end"]) if end_tag else None
        if end is not None and end <= start:
            end = None

        address_tag = item.select_one(".simcal-event-address")
        address = self.clean_text(address_tag.get_text(" ")) if address_tag else ""
        if address and "40 prospect" not in address.lower():
            logger.info(f"{self.source_name}: skipping '{title}' - held at {address!r}, not ManRay")
            return None

        body = item.select_one(".simcal-event-description")
        description = self.clean_text(body.get_text(" ")) if body else ""
        if len(description) < 20:
            description = f"{title} at ManRay, Central Square, Cambridge."
        # The youngest age admitted ("$15 for 21+ $20 for 19+" admits 19+).
        age = next((a for a in ("18+", "19+", "21+") if a in description), None)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=to_eastern_naive(start),
            end_datetime=to_eastern_naive(end) if end else None,
            venue_name=self.HOME.venue,
            street_address=self.HOME.street,
            city=self.HOME.city,
            state="MA",
            zip_code=self.HOME.zip_code,
            category=self.categorize(title),
            age_restrictions=age,
            source_name=self.source_name,
            source_url=self.source_url,
        )
