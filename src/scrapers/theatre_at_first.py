"""Scraper for Theatre@First, a Somerville community theatre

Reads the public Google Calendar feed embedded on the venue's calendar page.
That is the only place individual performance times exist: the season page lists
runs ("Performances: September 26 - October 11, 2026") with no times, and the
homepage keeps showing the most recent production long after it closes — which
is why the previous scraper returned seven performances of a show that had run
in November 2025. `EventValidator` rejected all seven as too old, so the source
contributed nothing while appearing to work.

The feed carries the whole organisation's calendar, including internal
committee meetings, so it is filtered down to public programming.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory, to_eastern_naive

logger = logging.getLogger(__name__)

CALENDAR_ID = "ckof39gfrbnt72qpjph558qu3k@group.calendar.google.com"
ICAL_URL = (f"https://calendar.google.com/calendar/ical/"
            f"{CALENDAR_ID.replace('@', '%40')}/public/basic.ics")

VENUE = "Theatre@First"
# The company performs at Unity Somerville in Davis Square.
DEFAULT_VENUE = "Unity Somerville"
DEFAULT_ADDRESS = "6 William St"

WINDOW_DAYS = 365

# Internal business, not public programming.
SKIP_PATTERNS = (
    r"steering", r"\bt@f\b", r"committee", r"board meeting",
    r"work ?(day|party|session)", r"strike", r"load[- ]?in", r"tech rehearsal",
    r"\brehearsal\b", r"production meeting",
)


class TheatreAtFirstScraper(BaseScraper):
    """Scraper for Theatre@First events"""

    def __init__(self):
        super().__init__(
            source_name="Theatre at First",
            source_url="https://www.theatreatfirst.org/learn-more/calendar",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        try:
            raw = self.fetch_html(ICAL_URL)
        except Exception as e:
            logger.error(f"Could not fetch Theatre@First calendar feed: {e}")
            return []

        now = datetime.now()
        horizon = now + timedelta(days=WINDOW_DAYS)

        events: List[EventCreate] = []
        seen = set()
        for block in raw.split("BEGIN:VEVENT")[1:]:
            fields = self._unfold(block)

            summary = fields.get("SUMMARY", "").strip()
            if len(summary) < 3 or self._is_internal(summary):
                continue

            start = self._parse_ical_datetime(fields.get("DTSTART"))
            if start is None:
                # Never guess — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
                logger.warning(f"Skipping '{summary}' - no parseable DTSTART")
                continue
            if not now - timedelta(days=1) <= start <= horizon:
                continue

            key = (summary, start)
            if key in seen:
                continue
            seen.add(key)

            venue_name, street = self._location(fields.get("LOCATION", ""))
            description = self._clean_ical_text(fields.get("DESCRIPTION", ""))
            if len(description) < 20:
                description = f"{summary} presented by {VENUE} at {venue_name}, Somerville."

            events.append(EventCreate(
                title=summary[:200],
                description=description[:2000],
                start_datetime=start,
                end_datetime=self._parse_ical_datetime(fields.get("DTEND")),
                source_url=self.source_url,
                source_name=self.source_name,
                venue_name=venue_name[:200],
                street_address=street[:200] if street else DEFAULT_ADDRESS,
                city="Somerville",
                state="MA",
                zip_code="02144",
                category=EventCategory.THEATER,
            ))

        logger.info(f"Scraped {len(events)} events from {VENUE}")
        return events

    @staticmethod
    def _unfold(block: str) -> dict:
        """Parse one VEVENT into {NAME: value}, joining RFC 5545 folded lines.

        Property names may carry parameters (DTSTART;TZID=America/New_York), so
        the key is truncated at the first semicolon.
        """
        fields: dict = {}
        current = None
        for line in block.splitlines():
            if line.startswith((" ", "\t")):        # continuation
                if current:
                    fields[current] += line[1:]
                continue
            if ":" not in line:
                continue
            name, value = line.split(":", 1)
            current = name.split(";", 1)[0].strip().upper()
            fields[current] = value.strip()
        return fields

    @staticmethod
    def _parse_ical_datetime(value: Optional[str]) -> Optional[datetime]:
        """Parse DTSTART into naive Eastern.

        A trailing Z means UTC. The Event model would normalize that on
        construction anyway, but the scraper compares against a window first,
        and mixing aware and naive raises TypeError — so it converts here, via
        the same helper, rather than keeping two notions of time in one function.

        Date-only values (all-day events) become midnight.
        """
        if not value:
            return None
        value = value.strip()
        try:
            if value.endswith("Z"):
                utc = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                return to_eastern_naive(utc)
            if "T" in value:
                return datetime.strptime(value, "%Y%m%dT%H%M%S")
            return datetime.strptime(value[:8], "%Y%m%d")
        except ValueError:
            return None

    @staticmethod
    def _is_internal(summary: str) -> bool:
        text = summary.lower()
        return any(re.search(p, text) for p in SKIP_PATTERNS)

    @staticmethod
    def _clean_ical_text(value: str) -> str:
        text = re.sub(r"<[^>]+>", " ", value)
        text = text.replace("\\n", " ").replace("\\,", ",").replace("\\;", ";")
        return re.sub(r"\s+", " ", text).strip()

    def _location(self, value: str) -> tuple:
        """"Unity Somerville, 6 William St, Somerville, MA 02144, USA"."""
        text = self._clean_ical_text(value)
        if not text:
            return DEFAULT_VENUE, DEFAULT_ADDRESS
        parts = [p.strip() for p in text.split(",") if p.strip()]
        venue = parts[0] if parts else DEFAULT_VENUE
        street = parts[1] if len(parts) > 1 else None
        return venue, street
