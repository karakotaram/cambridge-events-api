"""Custom scraper for bostonshows.org"""
import re
from datetime import datetime, timedelta
from typing import List
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class BostonShowsScraper(BaseScraper):
    """Custom scraper for bostonshows.org - Cambridge and Somerville shows only"""

    # Venues to exclude (already scraped directly)
    EXCLUDED_VENUES = [
        'middle east',
        'sonia',
        'lily pad',
        'lilypad'
    ]

    def __init__(self):
        super().__init__(
            source_name="BostonShows.org",
            source_url="https://bostonshows.org/",
            use_selenium=False  # Static HTML
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from bostonshows.org"""
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        events = []

        # Find all date-events containers
        date_containers = soup.find_all('div', class_='date-events')

        for container in date_containers:
            # Get the date from data-date attribute
            date_str = container.get('data-date')
            if not date_str:
                continue

            try:
                event_date = date_parser.parse(date_str)
            except:
                continue

            # Find all event rows within this date container
            event_rows = container.find_all('tr', class_='event')

            for row in event_rows:
                try:
                    event = self._parse_event_row(row, event_date)
                    if event:
                        events.append(event)
                except Exception as e:
                    # Log error but continue processing
                    continue

        return events

    def _parse_date_header(self, date_text: str) -> datetime:
        """Parse date from header like 'Friday, November 29'"""
        try:
            # Get current year
            current_year = datetime.now().year
            current_month = datetime.now().month

            # Parse the date
            parsed_date = date_parser.parse(f"{date_text} {current_year}", fuzzy=True)

            # If the parsed month is before current month, assume next year
            if parsed_date.month < current_month:
                parsed_date = date_parser.parse(f"{date_text} {current_year + 1}", fuzzy=True)

            return parsed_date
        except:
            return datetime.now()

    def _parse_event_row(self, row, event_date: datetime) -> EventCreate:
        """Parse a single event row"""
        # Get city from data-city attribute
        city = row.get('data-city', '').strip()

        # Filter: only Cambridge and Somerville
        if city not in ['Cambridge', 'Somerville']:
            return None

        # Get event time
        time_cell = row.find('td', class_='event-start')
        if not time_cell:
            return None

        time_text = self.clean_text(time_cell.get_text())

        # Get event details
        details_cell = row.find('td', class_='event-details')
        if not details_cell:
            return None

        # Get title and URL
        title_link = details_cell.find('a')
        if not title_link:
            return None

        title = self.clean_text(title_link.get_text())
        event_url = title_link.get('href', '')
        if event_url and not event_url.startswith('http'):
            event_url = f"https://bostonshows.org/{event_url}"

        # Get venue information
        venue_name = None
        venue_neighborhood = None
        venue_link = details_cell.find_all('a')
        if len(venue_link) > 1:
            venue_name = self.clean_text(venue_link[1].get_text())
            # Extract neighborhood from parentheses
            venue_text = self.clean_text(details_cell.get_text())
            neighborhood_match = re.search(r'\((.*?)\)', venue_text)
            if neighborhood_match:
                venue_neighborhood = neighborhood_match.group(1)

        # Filter: exclude specific venues
        if venue_name:
            venue_lower = venue_name.lower()
            if any(excluded in venue_lower for excluded in self.EXCLUDED_VENUES):
                return None

        # Parse start datetime
        start_datetime = self._combine_date_time(event_date, time_text)

        # Build description
        description = title
        if venue_neighborhood:
            description = f"{title} at {venue_name} in {venue_neighborhood}"

        # Determine venue address (use generic Cambridge/Somerville address)
        street_address = ""
        state = "MA"
        zip_code = "02139" if city == "Cambridge" else "02144"

        event = EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start_datetime,
            source_url=event_url if event_url else self.source_url,
            source_name=self.source_name,
            venue_name=venue_name if venue_name else "Unknown Venue",
            street_address=street_address,
            city=city,
            state=state,
            zip_code=zip_code,
            category=EventCategory.MUSIC
        )

        return event

    def _combine_date_time(self, date: datetime, time_str: str) -> datetime:
        """Combine date and time string into datetime"""
        try:
            # Parse time like "11:30am" or "8:00pm"
            time_match = re.search(r'(\d{1,2}):(\d{2})\s*(am|pm)', time_str, re.IGNORECASE)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2))
                am_pm = time_match.group(3).lower()

                # Convert to 24-hour format
                if am_pm == 'pm' and hour != 12:
                    hour += 12
                elif am_pm == 'am' and hour == 12:
                    hour = 0

                # Combine date and time
                return date.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except:
            pass

        # Default to 8pm if time parsing fails
        return date.replace(hour=20, minute=0, second=0, microsecond=0)
