"""Scraper for MIT Music and Theater Arts events using Playwright"""
import logging
import re
import json
from datetime import datetime
from typing import List, Optional

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class MITMusicTheaterScraper(BasePlaywrightScraper):
    """Scraper for MIT Music and Theater Arts (mta.mit.edu)"""

    def __init__(self):
        super().__init__(
            source_name="MIT Music & Theater",
            source_url="https://mta.mit.edu/events"
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from MIT Music and Theater Arts"""
        events = []

        try:
            self.goto(self.source_url, wait_until="networkidle")
            self.page.wait_for_timeout(2000)

            soup = self.get_soup()

            # First try JSON-LD
            json_ld_events = self._extract_json_ld_events(soup)
            if json_ld_events:
                events.extend(json_ld_events)
            else:
                # Parse HTML
                events = self._parse_html_events(soup)

        except Exception as e:
            logger.error(f"Error scraping MIT Music & Theater: {e}")

        logger.info(f"Scraped {len(events)} events from MIT Music & Theater")
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
                description = f"{title} - MIT Music and Theater Arts"

            url = data.get('url', self.source_url)

            venue_name = "MIT"
            location = data.get('location', {})
            if isinstance(location, dict):
                venue_name = location.get('name', venue_name)

            image_url = data.get('image')
            if isinstance(image_url, list) and image_url:
                image_url = image_url[0]

            category = self._detect_category(title, description)

            return EventCreate(
                title=title[:200],
                description=self.clean_text(description)[:2000],
                start_datetime=start_datetime,
                venue_name=venue_name,
                street_address="77 Massachusetts Ave",
                city="Cambridge",
                state="MA",
                zip_code="02139",
                category=category,
                source_name=self.source_name,
                source_url=url,
                image_url=image_url,
            )

        except Exception as e:
            logger.debug(f"Error parsing JSON-LD event: {e}")
            return None

    def _parse_html_events(self, soup) -> List[EventCreate]:
        """Parse events from HTML structure"""
        events = []
        seen_urls = set()

        # MTA uses views-row divs
        event_items = soup.find_all('div', class_=re.compile(r'^views-row'))

        for item in event_items:
            try:
                # Find title from views-field-title
                title_field = item.find('div', class_=re.compile(r'views-field-title'))
                if not title_field:
                    continue

                link = title_field.find('a')
                if not link:
                    continue

                title = self.clean_text(link.get_text())
                url = link.get('href')

                if not title or len(title) < 3:
                    continue

                # Make URL absolute
                if url and not url.startswith('http'):
                    url = f"https://mta.mit.edu{url}"
                else:
                    url = self.source_url

                # Skip duplicates
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # Get date from views-field-field-event-text-date
                date_field = item.find('div', class_=re.compile(r'views-field-field-event-text-date|views-field-field-event-date'))
                if date_field:
                    date_text = date_field.get_text()
                else:
                    date_text = item.get_text()
                start_datetime = self._parse_date_time(date_text)

                description = f"{title} - MIT Music and Theater Arts"

                # Get image
                img = item.find('img')
                image_url = img.get('src') if img else None

                category = self._detect_category(title, description)

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    venue_name="MIT",
                    street_address="77 Massachusetts Ave",
                    city="Cambridge",
                    state="MA",
                    zip_code="02139",
                    category=category,
                    source_name=self.source_name,
                    source_url=url,
                    image_url=image_url,
                )
                events.append(event)

            except Exception as e:
                logger.debug(f"Error parsing MTA event: {e}")
                continue

        return events

    def _parse_date_time(self, text: str) -> datetime:
        """Parse date/time from text like 'February 11, 2026 | 05:00 pm'"""
        # Default to now with 7pm
        default = datetime.now().replace(hour=19, minute=0, second=0, microsecond=0)

        try:
            # Look for pattern: Month Day, Year | HH:MM am/pm
            pattern = r'(\w+)\s+(\d{1,2}),?\s+(\d{4})\s*\|\s*(\d{1,2}):(\d{2})\s*(am|pm)'
            match = re.search(pattern, text, re.I)

            if match:
                month_str, day, year, hour, minute, ampm = match.groups()
                month = datetime.strptime(month_str, '%B').month
                hour = int(hour)
                minute = int(minute)
                if ampm.lower() == 'pm' and hour != 12:
                    hour += 12
                elif ampm.lower() == 'am' and hour == 12:
                    hour = 0

                return datetime(int(year), month, int(day), hour, minute)

            # Try simpler pattern without time
            pattern2 = r'(\w+)\s+(\d{1,2}),?\s+(\d{4})'
            match2 = re.search(pattern2, text)
            if match2:
                month_str, day, year = match2.groups()
                month = datetime.strptime(month_str, '%B').month
                return datetime(int(year), month, int(day), 19, 0)

        except Exception as e:
            logger.debug(f"Error parsing date from '{text}': {e}")

        return default

    def _detect_category(self, title: str, description: str) -> EventCategory:
        """Detect event category from title and description"""
        text = f"{title} {description}".lower()

        if any(word in text for word in ['concert', 'recital', 'symphony', 'orchestra', 'choir', 'ensemble', 'jazz', 'music']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['theater', 'theatre', 'play', 'drama', 'performance', 'dance']):
            return EventCategory.THEATER
        elif any(word in text for word in ['lecture', 'talk', 'seminar', 'masterclass', 'workshop']):
            return EventCategory.LECTURES
        elif any(word in text for word in ['exhibit', 'exhibition', 'gallery', 'art', 'display']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['film', 'movie', 'screening', 'cinema']):
            return EventCategory.ARTS_CULTURE

        return EventCategory.ARTS_CULTURE  # Default for arts venue
