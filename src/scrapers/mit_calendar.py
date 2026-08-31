"""Scraper for MIT Events Calendar using Playwright"""
import logging
import re
import json
from datetime import datetime
from typing import List, Optional

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class MITCalendarScraper(BasePlaywrightScraper):
    """Scraper for MIT Events Calendar (calendar.mit.edu)"""

    def __init__(self):
        super().__init__(
            source_name="MIT Events",
            source_url="https://calendar.mit.edu/"
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from MIT calendar"""
        events = []

        try:
            # Navigate to the calendar
            self.goto(self.source_url, wait_until="networkidle")

            # Wait for events to load
            self.page.wait_for_timeout(2000)

            # Try to get JSON-LD data first
            soup = self.get_soup()
            json_ld_events = self._extract_json_ld_events(soup)

            if json_ld_events:
                events.extend(json_ld_events)
                logger.info(f"Found {len(json_ld_events)} events from JSON-LD")
            else:
                # Parse from HTML
                html_events = self._parse_html_events()
                events.extend(html_events)

            # Try to load more events by clicking "Show all events" if present
            try:
                show_all = self.page.query_selector('a:has-text("Show all events")')
                if show_all:
                    show_all.click()
                    self.page.wait_for_timeout(3000)

                    # Parse additional events
                    soup = self.get_soup()
                    more_events = self._extract_json_ld_events(soup)
                    if more_events:
                        # Add only new events
                        existing_urls = {e.source_url for e in events}
                        for event in more_events:
                            if event.source_url not in existing_urls:
                                events.append(event)
            except Exception as e:
                logger.debug(f"Could not load more events: {e}")

        except Exception as e:
            logger.error(f"Error scraping MIT Calendar: {e}")

        logger.info(f"Scraped {len(events)} total events from MIT Calendar")
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
                elif isinstance(data, dict) and data.get('@type') == 'Event':
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

            # Parse ISO datetime
            try:
                start_datetime = datetime.fromisoformat(start_str.replace('Z', '+00:00'))
            except:
                return None

            description = data.get('description', '')
            if not description:
                description = f"{title} - MIT Event"

            url = data.get('url', self.source_url)

            # Get location
            venue_name = "MIT"
            street_address = "77 Massachusetts Ave"
            city = "Cambridge"

            location = data.get('location', {})
            if isinstance(location, dict):
                venue_name = location.get('name', venue_name)
                address = location.get('address', {})
                if isinstance(address, dict):
                    street_address = address.get('streetAddress', street_address)
                    city = address.get('addressLocality', city)
                elif isinstance(address, str):
                    street_address = address

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
                city=city,
                state="MA",
                category=category,
                source_name=self.source_name,
                source_url=url,
                image_url=image_url,
            )

        except Exception as e:
            logger.debug(f"Error parsing JSON-LD event: {e}")
            return None

    def _parse_html_events(self) -> List[EventCreate]:
        """Parse events from HTML when JSON-LD not available"""
        events = []

        try:
            # Find event cards
            event_cards = self.query_selector_all('.em-card, .em-event-card, [class*="event-card"]')

            for card in event_cards:
                try:
                    # Get title
                    title_elem = card.query_selector('h2, h3, .em-card_title, [class*="title"]')
                    if not title_elem:
                        continue

                    title = self.clean_text(title_elem.text_content())
                    if not title or len(title) < 3:
                        continue

                    # Get link
                    link = card.query_selector('a')
                    url = link.get_attribute('href') if link else self.source_url
                    if url and not url.startswith('http'):
                        url = f"https://calendar.mit.edu{url}"

                    # Get date/time. Skip the event rather than guess a date -
                    # a made-up date lands the event on the wrong day of the calendar.
                    date_elem = card.query_selector('.em-list_dates__container, [class*="date"], time')
                    datetime_attr = date_elem.get_attribute('datetime') if date_elem else None
                    if not datetime_attr:
                        logger.warning(f"Skipping '{title}' - no date on listing")
                        continue
                    try:
                        start_datetime = datetime.fromisoformat(datetime_attr.replace('Z', '+00:00'))
                    except ValueError:
                        logger.warning(f"Skipping '{title}' - unparseable date {datetime_attr!r}")
                        continue

                    # Get location
                    location_elem = card.query_selector('[class*="location"], [class*="venue"]')
                    venue_name = self.clean_text(location_elem.text_content()) if location_elem else "MIT"

                    category = self._detect_category(title, "")

                    event = EventCreate(
                        title=title[:200],
                        description=f"{title} - MIT Event",
                        start_datetime=start_datetime,
                        venue_name=venue_name,
                        city="Cambridge",
                        state="MA",
                        category=category,
                        source_name=self.source_name,
                        source_url=url,
                    )
                    events.append(event)

                except Exception as e:
                    logger.debug(f"Error parsing event card: {e}")
                    continue

        except Exception as e:
            logger.error(f"Error parsing HTML events: {e}")

        return events

    def _detect_category(self, title: str, description: str) -> EventCategory:
        """Detect event category"""
        text = f"{title} {description}".lower()

        if any(word in text for word in ['concert', 'music', 'recital', 'jazz', 'symphony', 'orchestra', 'choir', 'performance']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['lecture', 'talk', 'seminar', 'symposium', 'colloquium', 'speaker', 'presentation']):
            return EventCategory.LECTURES
        elif any(word in text for word in ['exhibit', 'exhibition', 'gallery', 'art', 'museum', 'display']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['theater', 'theatre', 'play', 'drama', 'comedy', 'improv']):
            return EventCategory.THEATER
        elif any(word in text for word in ['workshop', 'class', 'training', 'hands-on']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['film', 'movie', 'screening', 'cinema']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['sport', 'game', 'match', 'athletic', 'fitness']):
            return EventCategory.SPORTS

        return EventCategory.OTHER
