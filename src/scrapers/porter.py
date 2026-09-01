"""Custom scraper for Porter Square Books events

The site is a Drupal build behind bot protection. Plain HTTP gets 403, and so
does Selenium — the automation fingerprint is detected. Playwright gets a clean
200, which is why this uses `BasePlaywrightScraper`; the previous Selenium
version was silently receiving an "Access Restricted" page and parsing zero
events out of it.

The month calendar is a FullCalendar grid of `a.fc-event` anchors. Each anchor
carries the detail URL with the date already in the path
(/event/2026-09-01/slug), and wraps a `<template>` holding the fully-populated
`article.event-teaser` with date, time, place, and image.

That `<template>` is the trap. Only the one teaser for the selected day exists
in the live DOM; the other 32 are inert template content that BeautifulSoup's
html.parser will not descend into, so `soup.find_all(class_="event-teaser")`
returns 33 nodes of which 32 look empty. Each template is re-parsed separately
here.

The previous selectors matched an older version of the page, and the scraper
quietly returned zero events for months.
"""
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

from dateutil import parser as date_parser

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

BASE = "https://portersquarebooks.com"
# The calendar is month-at-a-time; three covers the usual publishing horizon.
MONTHS_AHEAD = 3

# Porter Square Books has two locations; the Cambridge one is on Mass Ave.
DEFAULT_ADDRESS = "25 White St"


class PorterSquareBooksScraper(BasePlaywrightScraper):
    """Custom scraper for Porter Square Books events"""

    def __init__(self):
        super().__init__(
            source_name="Porter Square Books",
            source_url=f"{BASE}/events/calendar",
        )

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()

        for url in self._month_urls():
            try:
                self.goto(url)
                # The grid is rendered client-side after load.
                self.wait_for_stable_count("a.fc-event", timeout=25000)
                soup = self.get_soup()
            except Exception as e:
                logger.warning(f"Could not fetch {url}: {e}")
                continue

            anchors = soup.find_all("a", class_="fc-event", href=True)
            if not anchors:
                logger.warning(f"No calendar entries found at {url}")
                continue

            for anchor in anchors:
                event = self._parse_anchor(anchor)
                if event is None:
                    continue
                if event.source_url in seen:
                    continue
                seen.add(event.source_url)
                events.append(event)

        logger.info(f"Scraped {len(events)} events from Porter Square Books")
        return events

    def _month_urls(self) -> List[str]:
        urls = [self.source_url]
        now = datetime.now()
        for offset in range(1, MONTHS_AHEAD):
            month = now.month + offset
            year, month = now.year + (month - 1) // 12, (month - 1) % 12 + 1
            urls.append(f"{BASE}/events/calendar/{year}/{month:02d}")
        return urls

    def _parse_anchor(self, anchor) -> Optional[EventCreate]:
        path = anchor["href"]
        title_el = anchor.find(class_="fc-title")
        title = self.clean_text(title_el.get_text()) if title_el else ""
        teaser = self._teaser_from_template(anchor)

        if not title and teaser is not None:
            heading = teaser.find(class_="event-teaser__title")
            title = self.clean_text(heading.get_text()) if heading else ""
        if len(title) < 3:
            return None

        details = self._details(teaser) if teaser is not None else {}
        start = self._parse_start(details.get("date") or self._date_from_path(path),
                                  details.get("time"))
        if start is None:
            # Never guess a date — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{title}' - no parseable date "
                           f"(path={path} time={details.get('time')!r})")
            return None

        venue_name, street = self._location(teaser) if teaser is not None else (self.source_name, None)

        image_url = None
        if teaser is not None:
            image = teaser.find("img", src=True)
            if image:
                image_url = image["src"]
                if image_url.startswith("/"):
                    image_url = f"{BASE}{image_url}"

        tags = [self.clean_text(t.get_text())
                for t in (teaser.find_all(class_="event-tag__term") if teaser is not None else [])]

        description = f"{title} at {venue_name}."
        if tags:
            description += f" {', '.join(tags)}."

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            source_url=f"{BASE}{path}" if path.startswith("/") else path,
            source_name=self.source_name,
            venue_name=venue_name[:200],
            street_address=street[:200] if street else DEFAULT_ADDRESS,
            city="Cambridge",
            state="MA",
            category=self._categorize(title, tags),
            image_url=image_url,
        )

    @staticmethod
    def _teaser_from_template(anchor):
        """Re-parse the <template> an anchor wraps.

        html.parser exposes template content as an opaque node, so the teaser
        inside is invisible to a normal find() until it is parsed on its own.
        """
        template = anchor.find("template")
        if template is None:
            return None
        from bs4 import BeautifulSoup
        inner = BeautifulSoup(template.decode_contents(), "html.parser")
        return inner.find(class_="event-teaser") or inner

    @staticmethod
    def _date_from_path(path: str) -> Optional[str]:
        """/event/2026-09-01/slug -> 2026-09-01. The date is in the URL."""
        match = re.search(r"/event/(\d{4}-\d{2}-\d{2})/", path or "")
        return match.group(1) if match else None

    def _details(self, teaser) -> Dict[str, str]:
        """Read the <dt>label</dt><dd>value</dd> pairs into a dict."""
        found: Dict[str, str] = {}
        block = teaser.find(class_="event-teaser__details")
        if not block:
            return found
        labels = block.find_all("dt")
        values = block.find_all("dd")
        for label, value in zip(labels, values):
            key = self.clean_text(label.get_text()).rstrip(":").lower()
            found[key] = self.clean_text(value.get_text())
        return found

    def _location(self, teaser) -> tuple:
        node = teaser.find(class_="event-teaser__details-location")
        if not node:
            return self.source_name, None
        address = node.find("address")
        if not address:
            return self.clean_text(node.get_text())[:200] or self.source_name, None
        lines = [self.clean_text(line) for line in address.stripped_strings]
        venue = lines[0] if lines else self.source_name
        street = lines[1] if len(lines) > 1 else None
        return venue, street

    @staticmethod
    def _parse_start(date_text: Optional[str], time_text: Optional[str]) -> Optional[datetime]:
        """Combine "Tue, 9/1/2026" with "7:00pm".

        Times are often ranges ("10:00am - 10:30am"); take the start. Passing the
        whole range to dateutil fails outright, which silently dropped every
        story time and workshop from the calendar.
        """
        if not date_text:
            return None
        start_time = ""
        if time_text:
            start_time = re.split(r"\s*(?:-|–|—|to)\s*", time_text.strip())[0].strip()
        try:
            parsed = date_parser.parse(f"{date_text} {start_time}".strip(), fuzzy=True)
        except (ValueError, OverflowError):
            return None
        return parsed.replace(second=0, microsecond=0)

    @staticmethod
    def _categorize(title: str, tags: List[str]) -> EventCategory:
        text = f"{title} {' '.join(tags)}".lower()
        if any(w in text for w in ("story hour", "kids", "children", "storytime")):
            return EventCategory.ARTS_CULTURE
        if any(w in text for w in ("music", "concert")):
            return EventCategory.MUSIC
        if any(w in text for w in ("book club", "writers", "workshop", "class")):
            return EventCategory.LECTURES
        # An independent bookstore's calendar is overwhelmingly author events
        return EventCategory.LECTURES
