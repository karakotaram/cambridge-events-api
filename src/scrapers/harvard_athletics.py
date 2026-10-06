"""Scraper for Harvard Athletics (gocrimson.com) - Harvard Crimson home games.

Reads the Sidearm calendar service behind gocrimson.com/calendar. Three things
about that service shape this scraper:

- One call (`date=M/D/YYYY`) returns the seven days starting at that date, as
  day groups. A multi-day event — a tennis regional, a regatta — is repeated in
  every day group it spans, so items are deduplicated on their `id`.
- Each item states its start twice: an ISO `date` and the displayed `time`
  string. They disagree for some games (Rugby vs Navy: `date` 13:00, the site
  and `time` 11:00 AM), so the start is the date part of `date` plus the
  displayed `time`, which is what a reader of gocrimson.com sees.
- `time` is "TBA", "All Day" or empty for events with no set start. Those are
  published as all-day events on their date, never at a guessed hour.
"""
import logging
import re
from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional

import requests

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

API_URL = "https://gocrimson.com/services/responsive-calendar.ashx"

# Ten one-week windows: the home schedule is published about a season ahead,
# and ten weeks is past what readers plan for while staying a handful of calls.
WEEKS = 10

_NO_SET_TIME = {"", "tba", "tbd", "all day"}
_CLOCK = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?\s*m\b\.?", re.I)


class HarvardAthleticsScraper(BaseScraper):
    """Scrape Harvard Crimson home athletic events from gocrimson.com API"""

    def __init__(self):
        super().__init__(
            source_name="Harvard Athletics",
            source_url="https://gocrimson.com/calendar?vtype=list",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        """Fetch WEEKS consecutive one-week windows and keep the home games.

        The service needs a date to start from; today's is the only clock read,
        and it chooses what to fetch, never what to keep.
        """
        first = date.today()
        groups = []
        for week in range(WEEKS):
            day = first + timedelta(weeks=week)
            response = requests.get(
                API_URL,
                params={"type": "events", "sport": "0", "location": "",
                        "date": f"{day.month}/{day.day}/{day.year}", "year": ""},
                headers=self.get_browser_headers(),
                timeout=15,
            )
            response.raise_for_status()
            groups.extend(response.json() or [])
        return self.parse_groups(groups)

    def parse_groups(self, groups: Iterable[dict]) -> List[EventCreate]:
        items = {}
        for group in groups:
            for item in (group or {}).get("events") or []:
                key = item.get("id") or (item.get("date"), (item.get("sport") or {}).get("title"),
                                         (item.get("opponent") or {}).get("title"))
                items.setdefault(key, item)

        events = []
        for item in items.values():
            try:
                event = self._parse_event(item)
            except Exception as e:
                logger.warning(f"Failed to parse Harvard Athletics item {item.get('id')}: {e}")
                continue
            if event:
                events.append(event)
        logger.info(f"Found {len(events)} home events among {len(items)} distinct items")
        return events

    @staticmethod
    def parse_start(date_field: str, time_field: Optional[str]):
        """(start, all_day) from the item's date and its displayed time.

        Returns (None, False) when the time is present but unreadable, so the
        caller skips the game rather than guess.
        """
        try:
            day = datetime.strptime((date_field or "")[:10], "%Y-%m-%d")
        except ValueError:
            return None, False
        shown = " ".join((time_field or "").split())
        if shown.lower() in _NO_SET_TIME:
            return day, True
        if shown.lower() == "noon":
            return day.replace(hour=12), False
        m = _CLOCK.search(shown)
        if not m:
            return None, False
        hour, minute = int(m.group(1)), int(m.group(2) or 0)
        if not (1 <= hour <= 12 and minute < 60):
            return None, False
        if m.group(3).lower() == "p" and hour != 12:
            hour += 12
        elif m.group(3).lower() == "a" and hour == 12:
            hour = 0
        return day.replace(hour=hour, minute=minute), False

    def _parse_event(self, item: dict) -> Optional[EventCreate]:
        """Parse a single event from the API JSON, home games only"""
        # "H" is Harvard's home; "A" away and "N" neutral site. A game "at MIT"
        # is in Cambridge but is MIT's event, not Harvard's.
        if item.get("location_indicator") != "H":
            return None
        # Placeholders such as "Football vs FCS First Round" are marked home
        # but sited "TBD": they happen only if Harvard qualifies and hosts.
        if self.clean_text(item.get("location") or "").upper() in {"TBD", "TBA"}:
            return None

        # Status "O" means the game is over and has a result.
        if item.get("status") == "O":
            return None

        sport_name = self.clean_text((item.get("sport") or {}).get("title", ""))
        if not sport_name:
            return None
        opponent = item.get("opponent") or {}
        opponent_name = self.clean_text(opponent.get("title", "") or opponent.get("name", ""))
        tournament = item.get("tournament")
        tournament_name = self.clean_text(tournament.get("title", "") if isinstance(tournament, dict)
                                          else str(tournament or ""))

        if tournament_name and tournament_name == opponent_name:
            title = f"Harvard {sport_name}: {tournament_name}"
        elif opponent_name:
            title = f"Harvard {sport_name} vs {opponent_name}"
        else:
            title = f"Harvard {sport_name}"

        start, all_day = self.parse_start(item.get("date") or "", item.get("time"))
        if start is None:
            logger.warning(f"Skipping '{title}' - unreadable date/time "
                           f"{item.get('date')!r} / {item.get('time')!r}")
            return None

        noplay = self.clean_text(item.get("noplay_text") or "")
        if re.search(r"cancel|postpone", noplay, re.I):
            logger.info(f"Skipping '{title}' - {noplay}")
            return None

        parts = [title]
        if tournament_name and tournament_name != opponent_name:
            parts.append(f"Tournament: {tournament_name}")
        shown_time = self.clean_text(item.get("time") or "")
        if shown_time:
            parts.append(f"Time: {shown_time}")
        location = self.clean_text(item.get("location") or "")
        if location:
            parts.append(f"Location: {location}")
        description = " | ".join(parts)

        facility = item.get("facility")
        venue_name = None
        if isinstance(facility, dict):
            venue_name = self.clean_text(facility.get("title") or "")

        schedule = item.get("schedule") or {}
        event_url = schedule.get("url") if isinstance(schedule, dict) else None
        event_url = event_url or self.source_url
        if not event_url.startswith("http"):
            event_url = f"https://gocrimson.com{event_url}"

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            all_day=all_day,
            source_url=event_url,
            source_name=self.source_name,
            venue_name=venue_name or "Harvard University Athletics",
            city="Cambridge",
            state="MA",
            category=EventCategory.SPORTS,
            family_friendly=True,
        )
