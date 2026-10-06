"""Scraper for Arts at the Armory, Somerville.

The site runs WordPress Events Manager, which publishes every upcoming event as
an iCalendar feed at /events.ics: start and end stamped TZID=America/New_York,
the full title (the listing truncates long ones), description, categories and
image, in one request. On 2026-10-06 it held exactly the 81 events the "All"
tab of /upcoming-events/ lists across seven ?pno= pages, at the same times.

The host blocked plain requests from GitHub's IP ranges in Dec 2025 while
letting a browser through. So if the plain request fails or returns something
other than a calendar, the feed is fetched from inside a browser session on the
listing page instead — same feed, same parser.
"""
import html
import logging
import re
from datetime import datetime
from typing import List, Optional

import pytz
import requests

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

ICS_URL = "https://artsatthearmory.org/events.ics"

# Events Manager categories mix event types with the two rooms (Cafe,
# Performance Hall). Only the event types say anything about the category.
CATEGORY_MAP = {
    "music": EventCategory.MUSIC,
    "comedy": EventCategory.THEATER,
    "theater": EventCategory.THEATER,
    "circus": EventCategory.THEATER,
    "dance": EventCategory.THEATER,
    "wrestling": EventCategory.SPORTS,
    "market": EventCategory.COMMUNITY,
    "community": EventCategory.COMMUNITY,
    "gaming": EventCategory.COMMUNITY,
    "literary art": EventCategory.ARTS_CULTURE,
    "film": EventCategory.ARTS_CULTURE,
    "exhibit": EventCategory.ARTS_CULTURE,
    "podcast": EventCategory.ARTS_CULTURE,
}


def unfold(block: str) -> dict:
    """One VEVENT as {NAME: (params, value)}, with RFC 5545 folded lines joined."""
    fields: dict = {}
    current = None
    for line in block.splitlines():
        if line.startswith((" ", "\t")):
            if current:
                params, value = fields[current]
                fields[current] = (params, value + line[1:])
            continue
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        key, _, params = name.partition(";")
        current = key.strip().upper()
        fields[current] = (params, value.rstrip("\r"))
    return fields


# Event descriptions embed the ticket button's tracking script as text:
#   $('#getTixButton').click(function() { fbq('track', 'Purchase', {...}); });
TRACKING_SCRIPT = re.compile(
    r"\$\(\s*['\"][^'\"]*['\"]\s*\)\.\w+\(\s*function\s*\(\)\s*\{.*?\n\s*\}\s*\)\s*;", re.S)


def ical_text(value: str) -> str:
    text = value.replace("\\n", "\n").replace("\\N", "\n")
    text = text.replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    text = TRACKING_SCRIPT.sub(" ", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\bGet tickets now!", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def ical_datetime(params: str, value: str) -> tuple[Optional[datetime], bool]:
    """(naive Eastern start, is_all_day) from a DTSTART/DTEND property."""
    value = (value or "").strip()
    try:
        if "VALUE=DATE" in params.upper() and "T" not in value:
            return datetime.strptime(value[:8], "%Y%m%d"), True
        if value.endswith("Z"):
            return to_eastern_naive(pytz.utc.localize(datetime.strptime(value, "%Y%m%dT%H%M%SZ"))), False
        naive = datetime.strptime(value, "%Y%m%dT%H%M%S")
    except ValueError:
        return None, False
    tzid = re.search(r"TZID=([^;:]+)", params)
    if tzid and tzid.group(1) != "America/New_York":
        try:
            return to_eastern_naive(pytz.timezone(tzid.group(1)).localize(naive)), False
        except pytz.UnknownTimeZoneError:
            return None, False
    return naive, False


class ArtsAtTheArmoryScraper(BaseScraper):
    """Arts at the Armory's Events Manager iCal feed."""

    def __init__(self):
        super().__init__(
            source_name="Arts at the Armory",
            source_url="https://artsatthearmory.org/upcoming-events/",
            use_selenium=False,
        )

    def fetch_calendar(self) -> str:
        try:
            response = requests.get(ICS_URL, timeout=30)
            if response.status_code == 200 and "BEGIN:VCALENDAR" in response.text[:200]:
                return response.text
            logger.warning(f"{self.source_name}: {ICS_URL} returned {response.status_code} "
                           f"without a calendar; fetching it through a browser")
        except requests.RequestException as e:
            logger.warning(f"{self.source_name}: {ICS_URL} failed ({e}); fetching it through a browser")
        return self.fetch_calendar_in_browser()

    def fetch_calendar_in_browser(self) -> str:
        self.use_selenium = True            # so run() shuts the driver down
        self.setup_selenium()
        self.driver.get(self.source_url)
        self.driver.set_script_timeout(60)
        text = self.driver.execute_async_script(
            "const done = arguments[arguments.length - 1];"
            "fetch(arguments[0], {credentials: 'same-origin'})"
            "  .then(r => r.ok ? r.text() : '').then(done, () => done(''));",
            ICS_URL)
        if "BEGIN:VCALENDAR" not in (text or "")[:200]:
            raise ValueError(f"{self.source_name}: no calendar at {ICS_URL}, by request or by browser")
        return text

    def scrape_events(self) -> List[EventCreate]:
        return self.parse_calendar(self.fetch_calendar())

    def parse_calendar(self, raw: str) -> List[EventCreate]:
        events = []
        for chunk in raw.split("BEGIN:VEVENT")[1:]:
            block = chunk.split("END:VEVENT", 1)[0]
            try:
                event = self.parse_vevent(unfold(block))
            except Exception as e:
                logger.warning(f"{self.source_name}: failed to parse a VEVENT: {e}")
                continue
            if event:
                events.append(event)
        return events

    def parse_vevent(self, fields: dict) -> Optional[EventCreate]:
        get = lambda name: fields.get(name, ("", ""))     # noqa: E731
        title = ical_text(get("SUMMARY")[1])
        url = get("URL")[1].strip() or self.source_url
        if len(title) < 3:
            return None
        if get("STATUS")[1].strip().upper() == "CANCELLED" or re.search(r"\bcancel+ed\b", title, re.I):
            logger.info(f"{self.source_name}: skipping cancelled '{title}'")
            return None

        start, all_day = ical_datetime(*get("DTSTART"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date ({url})")
            return None
        end, _ = ical_datetime(*get("DTEND"))
        if end is not None and (end <= start or all_day):
            end = None

        description = ical_text(get("DESCRIPTION")[1])
        if len(description) < 20:
            description = f"{title} at Arts at the Armory, Somerville."

        labels = [c.strip() for c in ical_text(get("CATEGORIES")[1]).split(",") if c.strip()]
        category = next((CATEGORY_MAP[c.lower()] for c in labels if c.lower() in CATEGORY_MAP),
                        None) or self.categorize_event(title, description)

        image = get("ATTACH")[1].strip()

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            source_url=url,
            source_name=self.source_name,
            venue_name="Arts at the Armory",
            street_address="191 Highland Ave",
            city="Somerville",
            state="MA",
            zip_code="02143",
            category=category,
            tags=labels,
            cost=self.cost(description),
            image_url=image if image.startswith("http") else None,
        )

    @staticmethod
    def cost(description: str) -> Optional[str]:
        text = description.lower()
        amount = re.search(r"\$\d+(?:\.\d{2})?", description)
        if amount:
            return amount.group()
        if re.search(r"\bfree (admission|event|entry|and open)|admission is free|\bfree!", text):
            return "Free"
        return None

    def categorize_event(self, title: str, description: str) -> EventCategory:
        """Categorize event based on keywords"""
        text = f"{title} {description}".lower()

        # Check specific categories
        if any(word in text for word in ['trivia', 'quiz', 'jeopardy', 'bingo']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['concert', 'music', 'band', 'dj', 'live music', 'musical', 'symphony', 'orchestra']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['theater', 'theatre', 'play', 'performance', 'show', 'acting', 'drama']):
            return EventCategory.THEATER
        elif any(word in text for word in ['comedy', 'stand-up', 'comedian', 'improv']):
            return EventCategory.THEATER
        elif any(word in text for word in ['dance', 'ballet', 'dancing']):
            return EventCategory.THEATER
        elif any(word in text for word in ['art', 'paint', 'craft', 'exhibit', 'gallery', 'sculpture']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['film', 'movie', 'cinema', 'screening']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['workshop', 'class', 'lesson', 'learn']):
            return EventCategory.ARTS_CULTURE
        else:
            return EventCategory.ARTS_CULTURE  # Default to arts & culture
