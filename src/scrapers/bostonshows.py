"""Custom scraper for bostonshows.org

An aggregator: the homepage lists a year of shows across Greater Boston as one
`div.date-events[data-date]` per day, each holding `tr.event` rows. A row
carries its city and venue as attributes (`data-city`, `data-venue`) and its
start as visible text ("8:00pm").

The venue is read from `data-venue`. It used to be read from the second `<a>`
in the row, but venues without a page on the site (Davis Square Plaza, the
library branches) are plain text, and those rows were published as
"Unknown Venue".
"""
import logging
import re
from datetime import datetime
from typing import List, Optional

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

TIME = re.compile(r'(\d{1,2}):(\d{2})\s*(am|pm)', re.IGNORECASE)


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
        return self.parse_listing(self.parse_html(html))

    def parse_listing(self, soup) -> List[EventCreate]:
        events = []
        for container in soup.find_all('div', class_='date-events'):
            # data-date is ISO ("2026-10-06"); anything else is not trusted
            try:
                event_date = datetime.strptime(container.get('data-date', ''), '%Y-%m-%d')
            except ValueError:
                logger.warning(f"Skipping a day with unreadable data-date {container.get('data-date')!r}")
                continue

            for row in container.find_all('tr', class_='event'):
                try:
                    event = self._parse_event_row(row, event_date)
                except Exception as e:
                    logger.warning(f"Failed to parse a BostonShows row: {e}")
                    continue
                if event:
                    events.append(event)

        return events

    def _venue(self, details_cell, row) -> tuple:
        """(venue, neighborhood). The row attribute is authoritative; the
        visible "at <venue> (<neighborhood>)" text is the fallback."""
        venue = self.clean_text(row.get('data-venue', '')) or None
        neighborhood = None
        venue_div = details_cell.find('div', class_='event-venue')
        if venue_div:
            text = self.clean_text(venue_div.get_text(' '))
            match = re.search(r'\(([^()]*)\)\s*$', text)
            if match:
                neighborhood = match.group(1).strip() or None
                text = text[:match.start()].strip()
            if not venue:
                venue = re.sub(r'^at\s+', '', text).strip() or None
        return venue, neighborhood

    def _parse_event_row(self, row, event_date: datetime) -> Optional[EventCreate]:
        """Parse a single event row"""
        city = row.get('data-city', '').strip()
        if city not in ['Cambridge', 'Somerville']:
            return None

        time_cell = row.find('td', class_='event-start')
        details_cell = row.find('td', class_='event-details')
        if not time_cell or not details_cell:
            return None

        title_div = details_cell.find('div', class_='event-title') or details_cell
        title_link = title_div.find('a')
        if not title_link:
            return None

        title = self.clean_text(title_link.get_text())
        event_url = title_link.get('href', '')
        if event_url and not event_url.startswith('http'):
            event_url = f"https://bostonshows.org/{event_url.lstrip('/')}"

        venue_name, neighborhood = self._venue(details_cell, row)
        if venue_name and any(x in venue_name.lower() for x in self.EXCLUDED_VENUES):
            return None

        time_text = self.clean_text(time_cell.get_text())
        start_datetime = self._combine_date_time(event_date, time_text)
        if start_datetime is None:
            # Never guess a start. This used to default to 8 PM.
            logger.warning(f"Skipping '{title}' - no parseable time {time_text!r} ({event_url or self.source_url})")
            return None

        info_div = details_cell.find('div', class_='event-info')
        info = self.clean_text(info_div.get_text(' ')) if info_div else ''
        cost = self._cost(info)

        description = title
        if venue_name:
            description += f" at {venue_name}"
            if neighborhood:
                description += f" ({neighborhood})"
        if info:
            description += f". {info}"

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start_datetime,
            source_url=event_url if event_url else self.source_url,
            source_name=self.source_name,
            venue_name=venue_name,
            city=city,
            state="MA",
            zip_code="02139" if city == "Cambridge" else "02144",
            cost=cost,
            category=EventCategory.MUSIC
        )

    @staticmethod
    def _cost(info: str) -> Optional[str]:
        """"$10–$15 / all ages" -> "$10–$15"; "free / 21+" -> "Free"."""
        for part in (p.strip() for p in info.split('/')):
            if part.startswith('$'):
                return part
            if part.lower() == 'free':
                return 'Free'
        return None

    @staticmethod
    def _combine_date_time(date: datetime, time_str: str) -> Optional[datetime]:
        """"8:00pm" on the row's date, or None if the text holds no clock time."""
        match = TIME.search(time_str or '')
        if not match:
            return None
        hour, minute = int(match.group(1)), int(match.group(2))
        if not (1 <= hour <= 12 and 0 <= minute <= 59):
            return None
        meridiem = match.group(3).lower()
        if meridiem == 'pm' and hour != 12:
            hour += 12
        elif meridiem == 'am' and hour == 12:
            hour = 0
        return date.replace(hour=hour, minute=minute, second=0, microsecond=0)
