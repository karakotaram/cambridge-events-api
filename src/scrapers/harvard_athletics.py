"""Scraper for Harvard Athletics (gocrimson.com) - Harvard Crimson home games"""
import json
import logging
from datetime import datetime, timedelta
from typing import List

import requests
from dateutil import parser as dateparse

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

API_URL = "https://gocrimson.com/services/responsive-calendar.ashx"


class HarvardAthleticsScraper(BaseScraper):
    """Scrape Harvard Crimson home athletic events from gocrimson.com API"""

    def __init__(self):
        super().__init__(
            source_name="Harvard Athletics",
            source_url="https://gocrimson.com/calendar?vtype=list",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        """Fetch events from the gocrimson calendar API, filtering to home games"""
        today = datetime.now()
        date_str = today.strftime("%-m/%-d/%Y")

        response = requests.get(
            API_URL,
            params={"type": "events", "sport": "0", "location": "", "date": date_str, "year": ""},
            headers=self.get_browser_headers(),
            timeout=15,
        )
        response.raise_for_status()
        date_groups = response.json()

        events = []
        total = 0
        for group in date_groups:
            group_events = group.get("events")
            if not group_events:
                continue
            for item in group_events:
                total += 1
                try:
                    event = self._parse_event(item)
                    if event:
                        events.append(event)
                except Exception as e:
                    logger.warning(f"Failed to parse event: {e}")

        logger.info(f"Found {len(events)} home events out of {total} total")
        return events

    def _parse_event(self, item: dict):
        """Parse a single event from the API JSON, home games only"""
        # Only home games are relevant to Cambridge
        if item.get("location_indicator") != "H":
            return None

        # Skip completed games (status "O" = over/final)
        if item.get("status") == "O":
            return None

        # Build title from sport + opponent
        sport = item.get("sport", {})
        sport_name = sport.get("title", "")
        opponent = item.get("opponent", {})
        opponent_name = opponent.get("title", "") or opponent.get("name", "")

        if not sport_name:
            return None

        if opponent_name:
            title = f"Harvard {sport_name} vs {opponent_name}"
        else:
            title = f"Harvard {sport_name}"

        # Parse date
        date_str = item.get("date") or item.get("date_utc")
        if not date_str:
            return None

        try:
            start_datetime = dateparse.parse(date_str)
        except (ValueError, TypeError):
            return None

        # Build description
        parts = [title]
        tournament = item.get("tournament")
        if tournament:
            parts.append(f"Tournament: {tournament}")
        time_str = item.get("time", "")
        if time_str:
            parts.append(f"Time: {time_str}")
        location = item.get("location", "")
        if location:
            parts.append(f"Location: {location}")
        description = " | ".join(parts)

        # Event URL from schedule link
        schedule = item.get("schedule", {})
        event_url = schedule.get("url", self.source_url)
        if event_url and not event_url.startswith("http"):
            event_url = f"https://gocrimson.com{event_url}"

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start_datetime,
            source_url=event_url,
            source_name=self.source_name,
            venue_name="Harvard University Athletics",
            city="Cambridge",
            state="MA",
            category=EventCategory.SPORTS,
            family_friendly=True,
        )
