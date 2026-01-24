"""Scraper for Harvard Square Business Association events via ICS feed"""
import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional
import requests

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class HarvardSquareScraper(BaseScraper):
    """Scraper for Harvard Square Business Association ICS calendar feed"""

    def __init__(self):
        super().__init__(
            source_name="Harvard Square",
            source_url="https://www.harvardsquare.com/events/?ical=1",
            use_selenium=False
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Harvard Square ICS feed"""
        events = []

        try:
            # Fetch ICS feed
            response = requests.get(
                self.source_url,
                timeout=30,
                headers=self.get_browser_headers()
            )
            response.raise_for_status()
            ics_content = response.text

            # Parse ICS content
            events = self._parse_ics(ics_content)
            logger.info(f"Parsed {len(events)} events from Harvard Square ICS feed")

        except Exception as e:
            logger.error(f"Error fetching Harvard Square ICS feed: {e}")

        return events

    def _parse_ics(self, ics_content: str) -> List[EventCreate]:
        """Parse ICS content into EventCreate objects"""
        events = []
        now = datetime.now()

        # Split into VEVENT blocks
        vevent_pattern = re.compile(r'BEGIN:VEVENT(.*?)END:VEVENT', re.DOTALL)
        vevent_matches = vevent_pattern.findall(ics_content)

        for vevent in vevent_matches:
            try:
                event = self._parse_vevent(vevent)
                if event:
                    # Only include future events (within next 60 days)
                    if event.start_datetime and event.start_datetime >= now:
                        if event.start_datetime <= now + timedelta(days=60):
                            events.append(event)
            except Exception as e:
                logger.warning(f"Error parsing VEVENT: {e}")
                continue

        return events

    def _parse_vevent(self, vevent_content: str) -> Optional[EventCreate]:
        """Parse a single VEVENT block"""

        def get_ics_value(content: str, field: str) -> Optional[str]:
            """Extract value for an ICS field, handling multi-line values"""
            # ICS fields can have parameters like DTSTART;VALUE=DATE:20260120
            pattern = rf'^{field}[;:]([^\r\n]*(?:\r?\n[ \t][^\r\n]*)*)'
            match = re.search(pattern, content, re.MULTILINE | re.IGNORECASE)
            if match:
                value = match.group(1)
                # Handle line continuations (lines starting with space/tab)
                value = re.sub(r'\r?\n[ \t]', '', value)
                # Remove any leading parameters (e.g., VALUE=DATE:)
                if ':' in value and not value.startswith('http'):
                    value = value.split(':', 1)[-1]
                return value.strip()
            return None

        def parse_ics_datetime(dt_str: str) -> Optional[datetime]:
            """Parse ICS datetime format"""
            if not dt_str:
                return None

            # Remove any timezone suffix
            dt_str = dt_str.replace('Z', '').strip()

            # Try various formats
            formats = [
                '%Y%m%dT%H%M%S',  # 20260120T190000
                '%Y%m%d',         # 20260120 (all-day event)
            ]

            for fmt in formats:
                try:
                    return datetime.strptime(dt_str, fmt)
                except ValueError:
                    continue

            return None

        # Extract fields
        summary = get_ics_value(vevent_content, 'SUMMARY')
        description = get_ics_value(vevent_content, 'DESCRIPTION')
        location = get_ics_value(vevent_content, 'LOCATION')
        url = get_ics_value(vevent_content, 'URL')
        dtstart = get_ics_value(vevent_content, 'DTSTART')
        dtend = get_ics_value(vevent_content, 'DTEND')

        if not summary:
            return None

        # Parse dates
        start_datetime = parse_ics_datetime(dtstart)
        end_datetime = parse_ics_datetime(dtend)

        if not start_datetime:
            logger.warning(f"Could not parse start date for event: {summary}")
            return None

        # Clean up description
        if description:
            # Unescape ICS special characters
            description = description.replace('\\n', '\n')
            description = description.replace('\\,', ',')
            description = description.replace('\\;', ';')
            description = description.replace('\\\\', '\\')
            # Remove HTML if present
            description = re.sub(r'<[^>]+>', '', description)
            description = self.clean_text(description)

        # Clean up title - unescape ICS special characters
        title = summary.replace('\\,', ',').replace('\\;', ';').replace('\\n', ' ').replace('\\\\', '\\')
        title = self.clean_text(title)
        if not title or len(title) < 3:
            return None

        # Parse location
        venue_name = None
        street_address = None
        if location:
            location = location.replace('\\,', ',')
            location = location.replace('\\n', ', ')
            parts = [p.strip() for p in location.split(',')]
            if parts:
                venue_name = parts[0]
                if len(parts) > 1:
                    # Try to extract street address
                    street_address = ', '.join(parts[1:3])

        # Build source URL
        if url:
            url = url.replace('\\', '')
            if not url.startswith('http'):
                url = f"https://{url}"
        else:
            url = "https://www.harvardsquare.com/events/"

        # Detect category from title/description/venue
        category = self._detect_category(title, description or "", venue_name)

        # Create event
        event = EventCreate(
            title=title[:200],
            description=(description or f"{title} - Event in Harvard Square")[:2000],
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            venue_name=venue_name,
            street_address=street_address,
            city="Cambridge",
            state="MA",
            category=category,
            source_name=self.source_name,
            source_url=url,
        )

        return event

    def _detect_category(self, title: str, description: str, venue_name: str = None) -> EventCategory:
        """Detect event category from title, description, and venue"""
        text = f"{title} {description}".lower()
        venue = (venue_name or "").lower()

        # Music venues - categorize as music regardless of description
        music_venues = ['passim', 'sinclair', 'regattabar', 'scullers', 'club passim', 'the sinclair']
        if any(v in venue for v in music_venues):
            return EventCategory.MUSIC

        # Comedy venues
        comedy_venues = ['comedy studio', 'improv']
        if any(v in venue for v in comedy_venues):
            return EventCategory.THEATER

        # Theater venues
        theater_venues = ['a.r.t.', 'american repertory', 'loeb drama', 'brattle theatre', 'oberon']
        if any(v in venue for v in theater_venues):
            return EventCategory.THEATER

        if any(word in text for word in ['concert', 'music', 'jazz', 'band', 'singer', 'orchestra', 'symphony', 'folk', 'blues']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['theater', 'theatre', 'play', 'musical', 'drama', 'comedy show', 'improv', 'comedy']):
            return EventCategory.THEATER
        elif any(word in text for word in ['art', 'exhibition', 'gallery', 'museum', 'painting', 'sculpture']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['lecture', 'talk', 'author', 'reading', 'book', 'discussion', 'seminar']):
            return EventCategory.LECTURES
        elif any(word in text for word in ['food', 'drink', 'tasting', 'beer', 'wine', 'restaurant', 'dining']):
            return EventCategory.FOOD_DRINK
        elif any(word in text for word in ['sports', 'fitness', 'run', 'race', 'yoga', 'workout']):
            return EventCategory.SPORTS
        elif any(word in text for word in ['community', 'meeting', 'volunteer', 'fundraiser', 'charity']):
            return EventCategory.COMMUNITY

        return EventCategory.OTHER
