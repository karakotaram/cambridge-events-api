"""Scraper for Longy School of Music events using Playwright"""
import logging
import re
import json
from datetime import datetime
from typing import List, Optional

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class LongyScraper(BasePlaywrightScraper):
    """Scraper for Longy School of Music of Bard College"""

    def __init__(self):
        super().__init__(
            source_name="Longy School of Music",
            source_url="https://longy.edu/calendar/"
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Longy calendar"""
        events = []

        try:
            self.goto(self.source_url, wait_until="networkidle")
            self.page.wait_for_timeout(2000)

            soup = self.get_soup()

            # Extract JSON-LD events
            events = self._extract_json_ld_events(soup)

            # If no JSON-LD, parse HTML
            if not events:
                events = self._parse_html_events(soup)

        except Exception as e:
            logger.error(f"Error scraping Longy: {e}")

        logger.info(f"Scraped {len(events)} events from Longy")
        return events

    def _extract_json_ld_events(self, soup) -> List[EventCreate]:
        """Extract events from JSON-LD structured data"""
        events = []

        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)

                if isinstance(data, list):
                    for item in data:
                        if item.get('@type') == 'Event':
                            event = self._parse_json_ld_event(item)
                            if event:
                                events.append(event)
                elif data.get('@type') == 'Event':
                    event = self._parse_json_ld_event(data)
                    if event:
                        events.append(event)

            except (json.JSONDecodeError, TypeError) as e:
                logger.debug(f"Error parsing JSON-LD: {e}")
                continue

        return events

    def _parse_json_ld_event(self, data: dict) -> Optional[EventCreate]:
        """Parse a single JSON-LD event object"""
        try:
            title = data.get('name', '').strip()
            if not title:
                return None

            start_str = data.get('startDate')
            if not start_str:
                return None

            start_datetime = datetime.fromisoformat(start_str.replace('Z', '+00:00'))

            description = data.get('description', '')
            if not description:
                description = f"{title} at Longy School of Music"

            url = data.get('url', self.source_url)

            # Get venue from location
            venue_name = "Longy School of Music"
            street_address = "27 Garden Street"
            location = data.get('location', {})
            if isinstance(location, dict):
                venue_name = location.get('name', venue_name)
                address = location.get('address', {})
                if isinstance(address, dict):
                    street_address = address.get('streetAddress', street_address)

            # Get image
            image_url = data.get('image')
            if isinstance(image_url, list) and image_url:
                image_url = image_url[0]

            return EventCreate(
                title=title[:200],
                description=self.clean_text(description)[:2000],
                start_datetime=start_datetime,
                venue_name=venue_name,
                street_address=street_address,
                city="Cambridge",
                state="MA",
                zip_code="02138",
                category=EventCategory.MUSIC,
                source_name=self.source_name,
                source_url=url,
                image_url=image_url,
            )

        except Exception as e:
            logger.debug(f"Error parsing JSON-LD event: {e}")
            return None

    def _parse_html_events(self, soup) -> List[EventCreate]:
        """Parse events from HTML when JSON-LD not available"""
        events = []

        # Find event containers
        event_items = soup.find_all('article', class_=re.compile(r'tribe-events'))
        if not event_items:
            event_items = soup.find_all('div', class_=re.compile(r'tribe-events-calendar-list__event'))

        for item in event_items:
            try:
                # Get title
                title_elem = item.find(['h2', 'h3'], class_=re.compile(r'tribe-events'))
                if not title_elem:
                    continue

                link = title_elem.find('a')
                title = self.clean_text(link.get_text() if link else title_elem.get_text())
                url = link.get('href') if link else self.source_url

                if not title or len(title) < 3:
                    continue

                # Get date/time
                datetime_elem = item.find('time') or item.find(class_=re.compile(r'tribe-event-date'))
                start_datetime = datetime.now().replace(hour=19, minute=0, second=0, microsecond=0)

                if datetime_elem:
                    datetime_attr = datetime_elem.get('datetime')
                    if datetime_attr:
                        try:
                            start_datetime = datetime.fromisoformat(datetime_attr.replace('Z', '+00:00'))
                        except:
                            pass

                # Get description
                desc_elem = item.find(class_=re.compile(r'tribe-events.*description|excerpt'))
                description = self.clean_text(desc_elem.get_text()) if desc_elem else f"{title} at Longy"

                # Get venue
                venue_elem = item.find(class_=re.compile(r'tribe-events-venue'))
                venue_name = self.clean_text(venue_elem.get_text()) if venue_elem else "Longy School of Music"

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    venue_name=venue_name,
                    street_address="27 Garden Street",
                    city="Cambridge",
                    state="MA",
                    zip_code="02138",
                    category=EventCategory.MUSIC,
                    source_name=self.source_name,
                    source_url=url,
                )
                events.append(event)

            except Exception as e:
                logger.debug(f"Error parsing Longy event: {e}")
                continue

        return events
