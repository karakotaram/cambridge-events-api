"""Custom scraper for The Dance Complex"""
import logging
import re
from datetime import datetime, timedelta
from typing import List
from dateutil import parser as date_parser
import requests

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class DanceComplexScraper(BaseScraper):
    """Custom scraper for The Dance Complex events using iCal feed"""

    def __init__(self):
        super().__init__(
            source_name="The Dance Complex",
            source_url="https://www.dancecomplex.org/events/?ical=1",
            use_selenium=False  # Using iCal feed, no Selenium needed
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from The Dance Complex iCal feed"""
        try:
            response = requests.get(self.source_url, timeout=30, headers=self.get_browser_headers())
            response.raise_for_status()
            ical_content = response.text
        except Exception as e:
            logger.error(f"Failed to fetch iCal feed: {e}")
            return []

        events = []
        seen_urls = set()

        # Parse iCal content
        current_event = {}
        in_event = False
        current_field = None
        current_value = ""

        for line in ical_content.split('\n'):
            line = line.rstrip('\r')

            # Handle line continuations (lines starting with space or tab)
            if line.startswith(' ') or line.startswith('\t'):
                current_value += line[1:]
                continue

            # Save previous field if we have one
            if current_field and current_value:
                current_event[current_field] = current_value

            # Parse new field
            if ':' in line:
                field_part, value = line.split(':', 1)
                # Handle fields with parameters like DTSTART;TZID=America/New_York
                if ';' in field_part:
                    current_field = field_part.split(';')[0]
                else:
                    current_field = field_part
                current_value = value
            else:
                current_field = None
                current_value = ""

            if line == 'BEGIN:VEVENT':
                in_event = True
                current_event = {}
            elif line == 'END:VEVENT':
                in_event = False
                # Process the event
                try:
                    event = self._parse_ical_event(current_event, seen_urls)
                    if event:
                        events.append(event)
                except Exception as e:
                    logger.warning(f"Failed to parse event: {e}")
                current_event = {}

        # Limit to upcoming events (next 60 days) and cap at 30
        now = datetime.now()
        cutoff = now + timedelta(days=60)
        events = [e for e in events if e.start_datetime >= now and e.start_datetime <= cutoff]
        events = sorted(events, key=lambda e: e.start_datetime)[:30]

        return events

    def _parse_ical_event(self, event_data: dict, seen_urls: set) -> EventCreate:
        """Parse a single iCal VEVENT into an EventCreate"""
        title = event_data.get('SUMMARY', '').strip()
        if not title or len(title) < 3:
            return None

        # Parse start datetime
        dtstart = event_data.get('DTSTART', '')
        if not dtstart:
            return None

        try:
            # Handle different date formats
            if 'T' in dtstart:
                # DateTime format: 20251203T103000
                start_datetime = datetime.strptime(dtstart[:15], '%Y%m%dT%H%M%S')
            else:
                # Date only format: 20251203
                start_datetime = datetime.strptime(dtstart[:8], '%Y%m%d')
        except ValueError as e:
            logger.warning(f"Failed to parse date {dtstart}: {e}")
            return None

        # Get URL
        event_url = event_data.get('URL', 'https://www.dancecomplex.org/events/')
        if event_url in seen_urls:
            return None
        seen_urls.add(event_url)

        # Get description and clean it
        description = event_data.get('DESCRIPTION', '')
        if description:
            # Unescape iCal escapes
            description = description.replace('\\n', '\n').replace('\\,', ',').replace('\\;', ';')
            # Take first 500 chars for brevity
            description = self.clean_text(description[:500])

        if not description:
            description = f"{title} at The Dance Complex"

        # Get image URL
        image_url = None
        attach = event_data.get('ATTACH', '')
        if attach and ('jpeg' in attach.lower() or 'jpg' in attach.lower() or 'png' in attach.lower()):
            image_url = attach

        # Get location (room/studio)
        location = event_data.get('LOCATION', '')
        venue_name = "The Dance Complex"
        if location:
            venue_name = f"The Dance Complex - {location}"

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start_datetime,
            source_url=event_url,
            source_name=self.source_name,
            venue_name=venue_name[:200],
            street_address="536 Massachusetts Ave",
            city="Cambridge",
            state="MA",
            zip_code="02139",
            category=EventCategory.SPORTS,
            image_url=image_url
        )
