"""Scraper for Mount Auburn Cemetery events"""
import logging
import re
import json
from datetime import datetime
from typing import List, Optional
import requests
from bs4 import BeautifulSoup

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class MountAuburnScraper(BaseScraper):
    """Scraper for Mount Auburn Cemetery events"""

    def __init__(self):
        super().__init__(
            source_name="Mount Auburn Cemetery",
            source_url="https://mountauburn.org/events/",
            use_selenium=False
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Mount Auburn Cemetery"""
        events = []

        try:
            response = requests.get(
                self.source_url,
                timeout=30,
                headers=self.get_browser_headers()
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Extract JSON-LD events (most reliable)
            json_ld_events = self._extract_json_ld_events(soup)
            if json_ld_events:
                events.extend(json_ld_events)
                logger.info(f"Extracted {len(json_ld_events)} events from JSON-LD")
            else:
                # Fallback to HTML parsing
                events = self._parse_html_events(soup)

        except Exception as e:
            logger.error(f"Error scraping Mount Auburn: {e}")

        logger.info(f"Scraped {len(events)} total events from Mount Auburn")
        return events

    def _extract_json_ld_events(self, soup: BeautifulSoup) -> List[EventCreate]:
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
                description = f"{title} at Mount Auburn Cemetery"

            url = data.get('url', self.source_url)

            # Get venue from location
            venue_name = "Mount Auburn Cemetery"
            street_address = "580 Mount Auburn Street"
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

            # Detect category
            category = self._detect_category(title, description)

            return EventCreate(
                title=title[:200],
                description=self.clean_text(description)[:2000],
                start_datetime=start_datetime,
                venue_name=venue_name,
                street_address=street_address,
                city="Cambridge",
                state="MA",
                zip_code="02138",
                category=category,
                source_name=self.source_name,
                source_url=url,
                image_url=image_url,
            )

        except Exception as e:
            logger.debug(f"Error parsing JSON-LD event: {e}")
            return None

    def _parse_html_events(self, soup: BeautifulSoup) -> List[EventCreate]:
        """Parse events from HTML (Tribe Events Calendar)"""
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
                datetime_elem = item.find('time')
                start_datetime = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)

                if datetime_elem:
                    datetime_attr = datetime_elem.get('datetime')
                    if datetime_attr:
                        try:
                            start_datetime = datetime.fromisoformat(datetime_attr.replace('Z', '+00:00'))
                        except:
                            pass

                # Get description
                desc_elem = item.find(class_=re.compile(r'tribe-events.*description|excerpt'))
                description = self.clean_text(desc_elem.get_text()) if desc_elem else f"{title} at Mount Auburn"

                # Get venue
                venue_elem = item.find(class_=re.compile(r'tribe-events-venue'))
                venue_name = self.clean_text(venue_elem.get_text()) if venue_elem else "Mount Auburn Cemetery"

                category = self._detect_category(title, description)

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    venue_name=venue_name,
                    street_address="580 Mount Auburn Street",
                    city="Cambridge",
                    state="MA",
                    zip_code="02138",
                    category=category,
                    source_name=self.source_name,
                    source_url=url,
                )
                events.append(event)

            except Exception as e:
                logger.debug(f"Error parsing Mount Auburn event: {e}")
                continue

        return events

    def _detect_category(self, title: str, description: str) -> EventCategory:
        """Detect event category"""
        text = f"{title} {description}".lower()

        if any(word in text for word in ['tour', 'walk', 'hike', 'explore']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['bird', 'nature', 'wildlife', 'garden', 'flora']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['lecture', 'talk', 'presentation', 'seminar']):
            return EventCategory.LECTURES
        elif any(word in text for word in ['art', 'exhibit', 'sculpture', 'gallery']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['concert', 'music', 'performance']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['workshop', 'class']):
            return EventCategory.COMMUNITY

        return EventCategory.COMMUNITY
