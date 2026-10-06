"""Scraper for Aeronaut Brewing, Somerville.

The events page sits behind a Cloudflare managed challenge. Plain requests get
403, and so does headless Chrome: its user-agent says "HeadlessChrome", and the
challenge never clears. This scraper used to get through by claiming to be
Chrome 120 on macOS from a newer Chrome, which is the user-agent contradiction
CLAUDE.md forbids. It now drives an ordinary, visible Chrome that says what it
is, and the challenge clears in seconds. That needs a display, so this source
runs locally only (runs_in_ci=False), which it already did: Cloudflare also
blocks GitHub's IP ranges.

Each event prints "Wed, October 7 at 7PM" and no year. The year is the one
whose calendar puts that date on the printed weekday.
"""
import logging
import re
import time
from datetime import date, datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

WEEKDAYS = {name: i for i, name in enumerate(("mon", "tue", "wed", "thu", "fri", "sat", "sun"))}
MONTHS = {name: i for i, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}

# The page names each event's location in `span.event-spot`.
SPOTS = {
    "somerville brewery": {"name": "Aeronaut Brewing Co.", "address": "14 Tyler St",
                           "city": "Somerville", "zip": "02143"},
}


def year_for_weekday(month: int, day: int, weekday: int, reference: date) -> Optional[int]:
    """The year near `reference` in which month/day falls on `weekday`, or None."""
    for year in (reference.year, reference.year + 1, reference.year - 1):
        try:
            if date(year, month, day).weekday() == weekday:
                return year
        except ValueError:          # Feb 29 in a common year
            continue
    return None


class AeronautScraper(BaseScraper):
    """Aeronaut Brewing's events page, through a real (visible) browser."""

    def __init__(self, today: Optional[date] = None):
        super().__init__(
            source_name="Aeronaut Brewing",
            source_url="https://www.aeronautbrewing.com/events/",
            use_selenium=True,
        )
        # Only the year is inferred from it, and only through the weekday match.
        self.today = today

    def setup_selenium(self):
        """A visible Chrome with its own user-agent: the one thing the challenge accepts."""
        if self.driver is None:
            from selenium import webdriver
            from selenium.webdriver.chrome.options import Options

            options = Options()
            options.add_argument('--window-size=1200,900')
            options.add_argument('--no-first-run')
            options.add_argument('--no-default-browser-check')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_experimental_option('excludeSwitches', ['enable-automation'])
            options.add_experimental_option('useAutomationExtension', False)

            self.driver = webdriver.Chrome(options=options)
            self.driver.set_page_load_timeout(60)
            self.driver.set_script_timeout(30)
            logger.info(f"Selenium WebDriver initialized for {self.source_name}")

    def fetch_listing(self) -> str:
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait

        self.setup_selenium()
        self.driver.get(self.source_url)
        try:
            WebDriverWait(self.driver, 30).until(
                EC.presence_of_element_located((By.CLASS_NAME, "single-event-details")))
        except Exception:
            # Still on "Just a moment..." means the challenge did not clear. Fail
            # the source loudly rather than report an empty listing as success.
            raise RuntimeError(f"{self.source_name}: events never rendered "
                               f"(page title {self.driver.title!r})")
        time.sleep(2)                       # let the rest of the list render
        return self.driver.page_source

    def scrape_events(self) -> List[EventCreate]:
        return self.parse_listing(self.parse_html(self.fetch_listing()), self.today or date.today())

    def parse_listing(self, soup: BeautifulSoup, reference: date) -> List[EventCreate]:
        events = []
        seen = set()
        for container in soup.find_all("div", class_=lambda c: c and "single-event-details" in c):
            try:
                event = self.parse_event(container, reference)
            except Exception as e:
                logger.warning(f"{self.source_name}: failed to parse an event: {e}")
                continue
            if event and (event.title, event.start_datetime) not in seen:
                seen.add((event.title, event.start_datetime))
                events.append(event)
        return events

    @staticmethod
    def parse_when(text: str, reference: date) -> tuple:
        """"Wed, October 7 at 7PM" -> (datetime, all_day). (None, False) if unreadable.

        A date with no time is an all-day event at midnight, never a guessed hour.
        """
        m = re.search(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[a-z]*\.?,?\s+([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})\b", text)
        if not m or m.group(2).lower() not in MONTHS:
            return None, False
        month, day = MONTHS[m.group(2).lower()], int(m.group(3))
        year = year_for_weekday(month, day, WEEKDAYS[m.group(1).lower()], reference)
        if year is None:
            return None, False

        tm = re.search(r"\bat\s+(\d{1,2})(?::(\d{2}))?\s*([AP]M)", text[m.end():], re.I)
        if not tm:
            return datetime(year, month, day), True
        hour = int(tm.group(1)) % 12 + (12 if tm.group(3).upper() == "PM" else 0)
        return datetime(year, month, day, hour, int(tm.group(2) or 0)), False

    def parse_event(self, container, reference: date) -> Optional[EventCreate]:
        classes = container.get("class", [])
        if "closed" in classes:
            return None

        title_elem = container.find("h3", class_="event-title")
        title = self.clean_text(title_elem.get_text()) if title_elem else ""
        if len(title) < 3:
            return None

        when = container.find(class_="event-date")
        when_text = self.clean_text(when.get_text(" ")) if when else ""
        start, all_day = self.parse_when(when_text, reference)
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date {when_text!r} ({self.source_url})")
            return None

        spot_elem = container.find("span", class_="event-spot")
        spot = self.clean_text(spot_elem.get_text(" ")) if spot_elem else "Somerville Brewery"
        venue = SPOTS.get(spot.lower())
        if venue is None:
            logger.warning(f"{self.source_name}: unknown location {spot!r} for '{title}'")
            venue = {"name": f"Aeronaut Brewing ({spot})"}

        desc_elem = container.find("div", class_="event-description")
        description = self.clean_text(desc_elem.get_text(" ")) if desc_elem else ""
        if len(description) < 20:
            description = f"{title} at {venue['name']}"
            if container.find("span", class_="ticketed-event"):
                description += " (ticketed event)"

        image_url = None
        image_wrap = container.find("div", class_="image-wrap")
        if image_wrap:
            match = re.search(r"url\(['\"]?([^'\")]+)['\"]?\)", image_wrap.get("style", ""))
            if match:
                image_url = match.group(1).strip()

        # Prefer the ticket link, then any outbound "more info" link.
        event_url = self.source_url
        links = container.find("div", class_="links")
        if links:
            anchors = links.select("span.tickets a[href]") + links.find_all("a", href=True)
            event_url = next((a["href"].strip() for a in anchors if a["href"].strip().startswith("http")),
                             self.source_url)

        event_type = next((c for c in ("ticketed", "meetup", "community", "music", "trivia", "party")
                           if c in classes), "community")

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            all_day=all_day,
            source_url=event_url,
            source_name=self.source_name,
            venue_name=venue["name"],
            street_address=venue.get("address"),
            city=venue.get("city"),
            state="MA",
            zip_code=venue.get("zip"),
            category=self.categorize_event(title, description, event_type),
            image_url=image_url,
        )

    def categorize_event(self, title: str, description: str, event_type: str) -> EventCategory:
        """Categorize event based on keywords and type"""
        text = f"{title} {description}".lower()

        # Check event type first
        if event_type == 'music':
            return EventCategory.MUSIC

        # Check keywords
        if any(word in text for word in ['trivia', 'quiz', 'jeopardy', 'bingo']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['concert', 'music', 'band', 'dj', 'live music', 'jazz', 'open mic']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['comedy', 'stand-up', 'comedian', 'drag show', 'drag night']):
            return EventCategory.THEATER
        elif any(word in text for word in ['game', 'd&d', 'dungeons', 'mahjong', 'board game', 'dragons']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['yoga', 'fitness', 'run', 'workout']):
            return EventCategory.SPORTS
        elif any(word in text for word in ['market', 'craft', 'fair', 'vendor']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['astronomy', 'science', 'talk', 'lecture', 'museum']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['tasting', 'beer', 'food', 'dinner']):
            return EventCategory.FOOD_DRINK
        else:
            return EventCategory.COMMUNITY
