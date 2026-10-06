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

Productions are often entered as one recurring event ("weekly on Thu, Fri, Sat
until Mar 29") rather than one event per performance, so RRULEs are expanded,
with EXDATE exclusions and RECURRENCE-ID overrides (a moved matinee) applied.
Reading only DTSTART listed the opening night of such a run and nothing else.

The feed holds the calendar's whole history back to 2009. Which part of it is
current is decided relative to the feed's own DTSTAMP — the moment Google
generated it — not the scraper's clock, so a saved feed parses the same way on
any day.
"""
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import pytz
from dateutil.rrule import rrulestr

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

# The slice of the feed that is current, relative to the feed's DTSTAMP. The
# one-day lookback keeps last night's show while today's run is still going.
LOOKBACK_DAYS = 1
WINDOW_DAYS = 365

# Internal business, not public programming.
SKIP_PATTERNS = (
    r"steering", r"\bt@f\b", r"committee", r"board meeting",
    r"work ?(day|party|session)", r"strike", r"load[- ]?in", r"tech rehearsal",
    r"\brehearsal\b", r"production meeting",
)

Property = Tuple[str, dict, str]          # (NAME, {PARAM: value}, value)


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
        return self.parse_feed(raw)

    def parse_feed(self, raw: str, as_of: Optional[datetime] = None) -> List[EventCreate]:
        """Every public performance in the feed's current window.

        `as_of` defaults to the feed's own generation time (DTSTAMP).
        """
        components = [self._component(block) for block in raw.split("BEGIN:VEVENT")[1:]]

        if as_of is None:
            as_of = self._feed_time(components)
            if as_of is None:
                logger.error("Theatre@First feed has no DTSTAMP - cannot tell which events are current")
                return []
        window = (as_of - timedelta(days=LOOKBACK_DAYS), as_of + timedelta(days=WINDOW_DAYS))

        # Occurrences of a recurring event that a separate VEVENT replaces
        # (same UID, RECURRENCE-ID naming the original slot).
        overridden = set()
        for props in components:
            rid = self._first(props, "RECURRENCE-ID")
            if rid:
                original, _ = self._when(rid)
                if original is not None:
                    overridden.add((self._value(props, "UID"), original))

        events: List[EventCreate] = []
        seen = set()
        for props in components:
            summary = self._value(props, "SUMMARY").strip()
            if len(summary) < 3 or self._is_internal(summary):
                continue
            if self._value(props, "STATUS").upper() == "CANCELLED":
                continue

            start, all_day = self._when(self._first(props, "DTSTART"))
            if start is None:
                # Never guess — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
                logger.warning(f"Skipping '{summary}' - no parseable DTSTART")
                continue

            end, _ = self._when(self._first(props, "DTEND"))
            duration = end - start if end is not None and end > start else None

            venue_name, street = self._location(self._value(props, "LOCATION"))
            description = self._clean_ical_text(self._value(props, "DESCRIPTION"))
            if len(description) < 20:
                description = f"{summary} presented by {VENUE} at {venue_name}, Somerville."

            for occurrence in self._occurrences(props, summary, start, window, overridden):
                key = (summary, occurrence)
                if key in seen:
                    continue
                seen.add(key)

                events.append(EventCreate(
                    title=summary[:200],
                    description=description[:2000],
                    start_datetime=occurrence,
                    end_datetime=occurrence + duration if duration else None,
                    all_day=all_day,
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

    # ------------------------------------------------------------------ #
    # Recurrence
    # ------------------------------------------------------------------ #

    def _occurrences(self, props: List[Property], summary: str, start: datetime,
                     window: Tuple[datetime, datetime], overridden: set) -> List[datetime]:
        """The starts of one VEVENT that fall inside the window.

        Expansion is bounded by the window, which comes from the feed's
        DTSTAMP, so even a rule with neither COUNT nor UNTIL is finite and
        independent of the scraper's clock.
        """
        lo, hi = window
        rule = self._first(props, "RRULE")
        if rule is None or self._first(props, "RECURRENCE-ID"):
            return [start] if lo <= start <= hi else []

        try:
            series = rrulestr(self._local_rule(rule[2]), dtstart=start)
            candidates = series.between(lo, hi, inc=True)
        except (ValueError, TypeError) as e:
            # The opening performance is still real; the rest cannot be read.
            logger.warning(f"Could not expand '{summary}' RRULE {rule[2]!r}: {e}")
            return [start] if lo <= start <= hi else []

        excluded = set()
        for _, params, value in self._all(props, "EXDATE"):
            for part in value.split(","):
                when, _ = self._parse_ical_datetime(part, params)
                if when is not None:
                    excluded.add(when)

        uid = self._value(props, "UID")
        return [o for o in candidates if o not in excluded and (uid, o) not in overridden]

    @staticmethod
    def _local_rule(rule: str) -> str:
        """Restate a UTC UNTIL as naive Eastern.

        The series is expanded in naive Eastern wall clock, so an 8 PM run
        stays at 8 PM across a DST change. dateutil refuses a UTC UNTIL
        against a naive DTSTART, so UNTIL is converted into the same terms.
        """
        def convert(match):
            utc = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
            return "UNTIL=" + to_eastern_naive(utc).strftime("%Y%m%dT%H%M%S")
        return re.sub(r"UNTIL=(\d{8}T\d{6})Z", convert, rule)

    # ------------------------------------------------------------------ #
    # iCalendar parsing
    # ------------------------------------------------------------------ #

    @staticmethod
    def _component(block: str) -> List[Property]:
        """Parse one VEVENT into (NAME, params, value) triples.

        Joins RFC 5545 folded lines, stops at END:VEVENT, and drops nested
        VALARMs, whose own DESCRIPTION/UID lines would otherwise overwrite the
        event's. Properties may repeat (EXDATE), so this is a list, not a dict.
        """
        block = block.split("END:VEVENT", 1)[0]
        block = re.sub(r"BEGIN:VALARM.*?END:VALARM", "", block, flags=re.DOTALL)

        lines: List[str] = []
        for line in block.splitlines():
            if line.startswith((" ", "\t")):        # continuation
                if lines:
                    lines[-1] += line[1:]
                continue
            lines.append(line)

        props: List[Property] = []
        for line in lines:
            if ":" not in line:
                continue
            head, value = line.split(":", 1)
            name, *raw_params = head.split(";")
            params = {}
            for p in raw_params:
                if "=" in p:
                    k, v = p.split("=", 1)
                    params[k.strip().upper()] = v.strip()
            props.append((name.strip().upper(), params, value.strip()))
        return props

    @staticmethod
    def _first(props: List[Property], name: str) -> Optional[Property]:
        return next((p for p in props if p[0] == name), None)

    @staticmethod
    def _all(props: List[Property], name: str) -> List[Property]:
        return [p for p in props if p[0] == name]

    @classmethod
    def _value(cls, props: List[Property], name: str) -> str:
        prop = cls._first(props, name)
        return prop[2] if prop else ""

    @classmethod
    def _feed_time(cls, components: List[List[Property]]) -> Optional[datetime]:
        """When Google generated the feed: the latest DTSTAMP, naive Eastern."""
        stamps = [cls._when(p)[0] for props in components for p in cls._all(props, "DTSTAMP")]
        stamps = [s for s in stamps if s is not None]
        return max(stamps) if stamps else None

    @classmethod
    def _when(cls, prop: Optional[Property]) -> Tuple[Optional[datetime], bool]:
        """Parse a date-valued property, honouring its TZID/VALUE parameters."""
        if prop is None:
            return None, False
        return cls._parse_ical_datetime(prop[2], prop[1])

    @staticmethod
    def _parse_ical_datetime(value: Optional[str], params: Optional[dict] = None
                             ) -> Tuple[Optional[datetime], bool]:
        """Parse a DATE or DATE-TIME into (naive Eastern, is_date_only).

        A trailing Z means UTC; a TZID parameter names the zone; neither means
        floating time, which for a Somerville theatre is Eastern. A date-only
        value is an all-day event and becomes midnight with the flag set.
        """
        if not value:
            return None, False
        value = value.strip()
        params = params or {}
        try:
            if params.get("VALUE") == "DATE" or re.fullmatch(r"\d{8}", value):
                return datetime.strptime(value[:8], "%Y%m%d"), True
            if value.endswith("Z"):
                utc = datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
                return to_eastern_naive(utc), False
            local = datetime.strptime(value, "%Y%m%dT%H%M%S")
            tzid = params.get("TZID")
            if tzid and tzid != "America/New_York":
                local = to_eastern_naive(pytz.timezone(tzid).localize(local))
            return local, False
        except (ValueError, pytz.UnknownTimeZoneError):
            return None, False

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
