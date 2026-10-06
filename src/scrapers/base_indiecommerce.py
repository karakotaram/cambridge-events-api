"""Shared scraper for bookstores on IndieCommerce (the ABA's Drupal platform)

Porter Square Books and Harvard Book Store both run it. Their month calendars
(`/events/calendar/YYYY/MM`) embed every event in `drupalSettings` as
FullCalendar JSON: an ISO start and end with the Eastern offset, an all-day
flag, the event URL, tags, and a `<template>` holding the teaser with the
printed date, time, and place.

The start comes from the ISO field and is kept only if the teaser prints the
same date and time. Months chain from the page's own `calander_view` (sic), so
no clock is read.

Both sites sit behind Cloudflare settings that refuse a browser announcing
itself as HeadlessChrome and serve an ordinary one, so these scrapers open a
visible window and run locally only (see BasePlaywrightScraper).
"""
import json
import logging
import re
from datetime import datetime
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_playwright_scraper import BasePlaywrightScraper

logger = logging.getLogger(__name__)

# Months to read, starting with the current one. The stores publish about three
# ahead, but on 2026-10-06 both served the first page of a session and then,
# after a day of testing, put later pages behind a Cloudflare check that did not
# clear on its own; nothing here clicks through one. One page a day is reliably
# served. Raise this if later pages turn out to be served on a normal schedule.
MONTHS = 1
# A pause between month pages, when there is more than one
MONTH_PAUSE_MS = 10_000

DATE_TEXT = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
TIME_TEXT = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])m", re.IGNORECASE)
CITY_LINE = re.compile(r"^(?P<city>[^,]+?)\s*,\s*(?P<state>[A-Z]{2})\s+(?P<zip>\d{5})")


class BaseIndieCommerceScraper(BasePlaywrightScraper):
    """One IndieCommerce store's event calendar."""

    # How many months to read, starting with the current one
    MONTHS = MONTHS

    # Location tags that name the store's own premises, and their address:
    # {tag: (venue, street, city, zip)}. Used only when an event has no place
    # block but carries one of these tags, which is how the store marks it.
    HOMES: dict = {}

    def __init__(self, source_name: str, base: str):
        super().__init__(source_name=source_name, source_url=f"{base}/events/calendar",
                         headless=False)
        self.base = base

    def scrape_events(self) -> List[EventCreate]:
        events, seen = [], set()
        url = self.source_url
        for month in range(self.MONTHS):
            if month:
                self.page.wait_for_timeout(MONTH_PAUSE_MS)
            self.goto(url)
            self.wait_past_challenge(timeout_s=45)
            html = self.get_html()
            for event in self.parse_calendar(html):
                key = (event.source_url, event.start_datetime)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
            following = self.next_month_path(html)
            if following is None:
                break
            url = f"{self.base}{following}"
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    @staticmethod
    def _settings(soup) -> dict:
        node = soup.find("script", attrs={"data-drupal-selector": "drupal-settings-json"})
        return json.loads(node.string) if node and node.string else {}

    def next_month_path(self, html: str) -> Optional[str]:
        """The month after the one this page shows, from the page's own state."""
        view = (self._settings(BeautifulSoup(html, "html.parser"))
                .get("indiecommerce_events", {}).get("calander_view", ""))
        match = re.fullmatch(r"(\d{4})-(\d{2})", view or "")
        if not match:
            return None
        year, month = int(match.group(1)), int(match.group(2))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
        return f"/events/calendar/{year}/{month:02d}"

    def parse_calendar(self, html: str) -> List[EventCreate]:
        """Every event the month's FullCalendar JSON carries."""
        settings = self._settings(BeautifulSoup(html, "html.parser"))
        try:
            options = settings["fullCalendarView"][0]["calendar_options"]
        except (KeyError, IndexError, TypeError):
            logger.warning(f"{self.source_name}: no calendar data on the page")
            return []
        if isinstance(options, str):
            options = json.loads(options)

        events = []
        for item in options.get("events", []):
            try:
                event = self._parse_item(item)
            except Exception as e:
                logger.warning(f"{self.source_name}: could not parse {item.get('url')!r}: {e}")
                continue
            if event:
                events.append(event)
        return events

    def _parse_item(self, item: dict) -> Optional[EventCreate]:
        title_html = BeautifulSoup(item.get("title") or "", "html.parser")
        teaser = self._teaser(title_html)
        heading = title_html.find("span")
        title = self.clean_text(heading.get_text() if heading else title_html.get_text())
        if len(title) < 3:
            return None
        if re.search(r"\bcancell?ed\b", title, re.IGNORECASE):
            return None

        start, all_day = self._start(item, teaser, title)
        if start is None:
            return None
        end = None
        if item.get("end") and not all_day:
            end = datetime.fromisoformat(item["end"])

        tags = [self.clean_text(t.get_text())
                for t in BeautifulSoup(item.get("des") or "", "html.parser").find_all("span")]
        venue, street, city, state, zip_code = self._place(teaser, tags)

        image_url = None
        image = teaser.find("img", src=True) if teaser else None
        if image:
            image_url = self._normalize_image_url(image["src"], self.base)

        path = item.get("url") or ""
        description = f"{title} at {venue}."
        if tags:
            description += f" {', '.join(tags)}."

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end if end and end > start else None,
            all_day=all_day,
            source_url=f"{self.base}{path}" if path.startswith("/") else (path or self.source_url),
            source_name=self.source_name,
            venue_name=venue[:150],
            street_address=street,
            city=city,
            state=state,
            zip_code=zip_code,
            cost="Free" if "Free" in tags else ("Ticketed" if "Ticketed" in tags else None),
            category=self._categorize(title, tags),
            image_url=image_url,
        )

    @staticmethod
    def _teaser(title_html) -> Optional[BeautifulSoup]:
        """Re-parse the <template>: html.parser does not descend into one."""
        template = title_html.find("template")
        if template is None:
            return None
        return BeautifulSoup(template.decode_contents(), "html.parser")

    def _details(self, teaser) -> dict:
        found = {}
        block = teaser.find(class_="event-teaser__details") if teaser else None
        if block:
            for label, value in zip(block.find_all("dt"), block.find_all("dd")):
                found[self.clean_text(label.get_text()).rstrip(":").lower()] = self.clean_text(value.get_text(" "))
        return found

    def _start(self, item: dict, teaser, title: str) -> Tuple[Optional[datetime], bool]:
        """The ISO start, if the printed date (and time) say the same."""
        raw = item.get("start") or ""
        try:
            start = datetime.fromisoformat(raw)
        except ValueError:
            logger.warning(f"Skipping '{title}' - unreadable start {raw!r}")
            return None, False
        all_day = bool(item.get("allDay")) or len(raw) == 10

        details = self._details(teaser)
        printed_date = DATE_TEXT.search(details.get("date", ""))
        if printed_date is None:
            logger.warning(f"Skipping '{title}' - no printed date to check {raw!r} against")
            return None, False
        month, day, year = (int(g) for g in printed_date.groups())
        if (start.year, start.month, start.day) != (year, month, day):
            logger.warning(f"Skipping '{title}' - start {raw!r} disagrees with printed date {details['date']!r}")
            return None, False

        if all_day:
            return start.replace(hour=0, minute=0, tzinfo=None), True
        printed_time = TIME_TEXT.search(details.get("time", ""))
        if printed_time is None:
            logger.warning(f"Skipping '{title}' - no printed time to check {raw!r} against")
            return None, False
        hour = int(printed_time.group(1)) % 12 + (12 if printed_time.group(3).lower() == "p" else 0)
        if (start.hour, start.minute) != (hour, int(printed_time.group(2))):
            logger.warning(f"Skipping '{title}' - start {raw!r} disagrees with printed time {details['time']!r}")
            return None, False
        return start, False

    def _place(self, teaser, tags: List[str]) -> Tuple[str, Optional[str], Optional[str], str, Optional[str]]:
        """(venue, street, city, state, zip) from either address shape."""
        node = teaser.find(class_="event-teaser__details-location") if teaser else None
        if node is None:
            home = next((self.HOMES[t] for t in tags if t in self.HOMES), None)
            if home:
                venue, street, city, zip_code = home
                return venue, street, city, "MA", zip_code
            return self.source_name, None, None, "MA", None

        structured = node.find(class_="address")
        if structured is not None:
            def part(cls):
                el = structured.find(class_=cls)
                return self.clean_text(el.get_text()) if el else None
            return (part("address-line1") or self.source_name, part("address-line2"),
                    part("locality"), part("administrative-area") or "MA",
                    (part("postal-code") or "")[:5] or None)

        address = node.find("address")
        lines = [self.clean_text(s) for s in (address or node).stripped_strings]
        place = CITY_LINE.match(lines[-1]) if lines else None
        if place is None or len(lines) < 3:
            return self.clean_text(node.get_text(" ")) or self.source_name, None, None, "MA", None
        # "Cambridge Edition" / "Boston Edition" name one of the store's own
        # branches; any line between it and the street is a host venue.
        names = [f"{self.source_name} {line}" if line.endswith("Edition") else line for line in lines[:-2]]
        venue = " – ".join(names) if names else self.source_name
        return venue, lines[-2], place["city"].strip(), place["state"], place["zip"]

    @staticmethod
    def _categorize(title: str, tags: List[str]) -> EventCategory:
        text = f"{title} {' '.join(tags)}".lower()
        if any(w in text for w in ("music", "concert")):
            return EventCategory.MUSIC
        if any(w in text for w in ("story hour", "storytime", "story time", "kids", "children")):
            return EventCategory.ARTS_CULTURE
        # A bookstore's calendar is overwhelmingly author talks and readings
        return EventCategory.LECTURES
