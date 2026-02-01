"""Scraper for The Rockwell events in Somerville"""
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


class RockwellScraper(BaseScraper):
    """Scraper for The Rockwell - Somerville venue for comedy, music, and events"""

    def __init__(self):
        super().__init__(
            source_name="The Rockwell",
            source_url="https://www.therockwell.org/calendar/",
            use_selenium=False
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from The Rockwell calendar"""
        events = []
        seen_urls = set()

        try:
            response = requests.get(
                self.source_url,
                timeout=30,
                headers=self.get_browser_headers()
            )
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Try to extract JSON-LD data first (most reliable)
            json_ld_events = self._extract_json_ld_events(soup)
            if json_ld_events:
                events.extend(json_ld_events)
                logger.info(f"Extracted {len(json_ld_events)} events from JSON-LD")

            # Also parse HTML for any events not in JSON-LD
            html_events = self._parse_html_events(soup, seen_urls)

            # Add HTML events that aren't duplicates
            for event in html_events:
                if event.source_url not in seen_urls:
                    events.append(event)
                    seen_urls.add(event.source_url)

        except Exception as e:
            logger.error(f"Error scraping The Rockwell: {e}")

        logger.info(f"Scraped {len(events)} total events from The Rockwell")
        return events

    def _extract_json_ld_events(self, soup: BeautifulSoup) -> List[EventCreate]:
        """Extract events from JSON-LD structured data"""
        events = []

        for script in soup.find_all('script', type='application/ld+json'):
            try:
                data = json.loads(script.string)

                # Handle both single events and arrays
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

            # Parse dates
            start_str = data.get('startDate')
            if not start_str:
                return None

            start_datetime = datetime.fromisoformat(start_str.replace('Z', '+00:00'))

            # Get description
            description = data.get('description', '')
            if not description:
                description = f"{title} at The Rockwell"

            # Get URL
            url = data.get('url', self.source_url)

            # Get location
            location = data.get('location', {})
            venue_name = "The Rockwell"
            if isinstance(location, dict):
                venue_name = location.get('name', 'The Rockwell')

            # Get price
            cost = None
            offers = data.get('offers')
            if offers:
                if isinstance(offers, list) and offers:
                    price = offers[0].get('price')
                    if price:
                        cost = f"${price}"
                elif isinstance(offers, dict):
                    price = offers.get('price')
                    if price:
                        cost = f"${price}"

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
                street_address="255 Elm Street",
                city="Somerville",
                state="MA",
                zip_code="02144",
                category=category,
                cost=cost,
                source_name=self.source_name,
                source_url=url,
                image_url=image_url,
            )

        except Exception as e:
            logger.debug(f"Error parsing JSON-LD event: {e}")
            return None

    def _parse_html_events(self, soup: BeautifulSoup, seen_urls: set) -> List[EventCreate]:
        """Parse events from HTML structure"""
        events = []

        # Find event links in the calendar
        event_links = soup.find_all('a', href=re.compile(r'/event/'))

        for link in event_links:
            try:
                url = link.get('href', '')
                if not url or url in seen_urls:
                    continue

                # Make absolute URL
                if not url.startswith('http'):
                    url = f"https://www.therockwell.org{url}"

                title = self.clean_text(link.get_text())
                if not title or len(title) < 3:
                    continue

                seen_urls.add(url)

                # Try to get more details from the event page
                event = self._scrape_event_page(url, title)
                if event:
                    events.append(event)

            except Exception as e:
                logger.debug(f"Error parsing HTML event: {e}")
                continue

        return events

    def _scrape_event_page(self, url: str, title: str) -> Optional[EventCreate]:
        """Scrape individual event page for details"""
        try:
            response = requests.get(url, timeout=30, headers=self.get_browser_headers())
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'html.parser')

            # Check for JSON-LD on event page
            for script in soup.find_all('script', type='application/ld+json'):
                try:
                    data = json.loads(script.string)
                    if isinstance(data, dict) and data.get('@type') == 'Event':
                        event = self._parse_json_ld_event(data)
                        if event:
                            return event
                except:
                    continue

            # Fallback to HTML parsing
            # Find date/time
            date_elem = soup.find('span', class_=re.compile(r'tribe-event-date'))
            time_elem = soup.find('span', class_=re.compile(r'tribe-event-time'))

            # Default to today if no date found
            start_datetime = datetime.now().replace(hour=19, minute=0, second=0, microsecond=0)

            # Find description
            desc_elem = soup.find('div', class_=re.compile(r'tribe-events-content|entry-content'))
            description = self.clean_text(desc_elem.get_text()) if desc_elem else f"{title} at The Rockwell"

            category = self._detect_category(title, description)

            return EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start_datetime,
                venue_name="The Rockwell",
                street_address="255 Elm Street",
                city="Somerville",
                state="MA",
                zip_code="02144",
                category=category,
                source_name=self.source_name,
                source_url=url,
            )

        except Exception as e:
            logger.debug(f"Error scraping event page {url}: {e}")
            return None

    def _detect_category(self, title: str, description: str) -> EventCategory:
        """Detect event category from title and description"""
        text = f"{title} {description}".lower()

        if any(word in text for word in ['comedy', 'standup', 'stand-up', 'improv', 'comedian', 'funny']):
            return EventCategory.THEATER
        elif any(word in text for word in ['concert', 'music', 'band', 'singer', 'dj', 'live music', 'jazz', 'rock']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['trivia', 'game', 'bingo', 'quiz']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['drag', 'cabaret', 'burlesque', 'show', 'performance']):
            return EventCategory.THEATER
        elif any(word in text for word in ['film', 'movie', 'screening']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['food', 'drink', 'tasting', 'brunch']):
            return EventCategory.FOOD_DRINK

        return EventCategory.OTHER
