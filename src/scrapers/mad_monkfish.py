"""Scraper for The Mad Monkfish jazz events"""
import logging
import re
from datetime import datetime
from typing import List, Optional
import requests
from bs4 import BeautifulSoup

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class MadMonkfishScraper(BaseScraper):
    """Scraper for The Mad Monkfish jazz schedule in Cambridge"""

    def __init__(self):
        super().__init__(
            source_name="The Mad Monkfish",
            source_url="https://www.themadmonkfish.com/jazz-schedule/",
            use_selenium=False
        )
        self.base_url = "https://www.themadmonkfish.com"

    def scrape_events(self) -> List[EventCreate]:
        """Scrape jazz events from The Mad Monkfish"""
        events = []
        page = 1
        max_pages = 5  # Limit pagination

        while page <= max_pages:
            try:
                url = self.source_url if page == 1 else f"{self.source_url}?p={page}"
                response = requests.get(url, timeout=30, headers=self.get_browser_headers())
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')

                page_events = self._parse_events(soup)
                if not page_events:
                    break  # No more events

                events.extend(page_events)
                logger.info(f"Scraped {len(page_events)} events from page {page}")

                # Check for "Load More" or next page
                load_more = soup.find('a', string=re.compile(r'Load More|Next', re.I))
                if not load_more:
                    break

                page += 1

            except Exception as e:
                logger.error(f"Error scraping Mad Monkfish page {page}: {e}")
                break

        logger.info(f"Scraped {len(events)} total events from The Mad Monkfish")
        return events

    def _parse_events(self, soup: BeautifulSoup) -> List[EventCreate]:
        """Parse events from page HTML"""
        events = []

        # Find all event links - they contain date and performer info
        # Format: "1/30 Midnight at the Mad Monkfish w/Mikayla Shirley 12-1am"
        event_links = soup.find_all('a', href=re.compile(r'/event/|/jazz-schedule/'))

        for link in event_links:
            try:
                text = self.clean_text(link.get_text())
                if not text or len(text) < 5:
                    continue

                # Skip navigation links
                if text.lower() in ['load more events', 'jazz schedule', 'home', 'menu', 'reservations']:
                    continue

                url = link.get('href', '')
                if not url.startswith('http'):
                    url = f"{self.base_url}{url}"

                # Parse the event text
                event = self._parse_event_text(text, url)
                if event:
                    events.append(event)

            except Exception as e:
                logger.debug(f"Error parsing event link: {e}")
                continue

        return events

    def _parse_event_text(self, text: str, url: str) -> Optional[EventCreate]:
        """Parse event details from link text like '1/30 Artist Name 7pm'"""
        try:
            # Try to extract date pattern (M/D or MM/DD)
            date_match = re.search(r'(\d{1,2})/(\d{1,2})', text)
            if not date_match:
                return None

            month = int(date_match.group(1))
            day = int(date_match.group(2))

            # Determine year (assume current year, or next if date has passed)
            now = datetime.now()
            year = now.year
            try:
                event_date = datetime(year, month, day)
                if event_date < now - timedelta(days=7):  # More than a week ago
                    event_date = datetime(year + 1, month, day)
            except ValueError:
                return None

            # Extract time
            time_match = re.search(r'(\d{1,2})(?::(\d{2}))?\s*(am|pm)', text, re.I)
            hour = 19  # Default to 7pm
            minute = 0

            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2) or 0)
                if time_match.group(3).lower() == 'pm' and hour != 12:
                    hour += 12
                elif time_match.group(3).lower() == 'am' and hour == 12:
                    hour = 0

            start_datetime = event_date.replace(hour=hour, minute=minute, second=0, microsecond=0)

            # Extract title (remove date and time from text)
            title = text
            title = re.sub(r'\d{1,2}/\d{1,2}\s*', '', title)  # Remove date
            title = re.sub(r'\d{1,2}(?::\d{2})?\s*(?:am|pm)(?:\s*-\s*\d{1,2}(?::\d{2})?\s*(?:am|pm))?', '', title, flags=re.I)  # Remove time
            title = self.clean_text(title)

            if not title or len(title) < 3:
                title = "Live Jazz at The Mad Monkfish"

            description = f"{title} - Live jazz in the Jazz Baroness Room at The Mad Monkfish"

            return EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start_datetime,
                venue_name="The Mad Monkfish - Jazz Baroness Room",
                street_address="524 Massachusetts Ave",
                city="Cambridge",
                state="MA",
                zip_code="02139",
                category=EventCategory.MUSIC,
                source_name=self.source_name,
                source_url=url,
            )

        except Exception as e:
            logger.debug(f"Error parsing event text '{text}': {e}")
            return None


# Need to import timedelta
from datetime import timedelta
