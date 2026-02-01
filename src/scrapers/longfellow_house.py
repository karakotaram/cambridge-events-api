"""Scraper for Longfellow House National Historic Site events using Playwright"""
import logging
import re
import json
from datetime import datetime
from typing import List, Optional

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class LongfellowHouseScraper(BasePlaywrightScraper):
    """Scraper for Longfellow House - Washington's Headquarters NHS"""

    def __init__(self):
        super().__init__(
            source_name="Longfellow House",
            source_url="https://www.nps.gov/long/planyourvisit/calendar.htm"
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Longfellow House calendar"""
        events = []

        try:
            self.goto(self.source_url, wait_until="networkidle", timeout=60000)
            self.page.wait_for_timeout(5000)  # Wait for JS calendar to load

            soup = self.get_soup()

            # Try JSON-LD first
            json_ld_events = self._extract_json_ld_events(soup)
            if json_ld_events:
                events.extend(json_ld_events)
            else:
                # Parse HTML
                events = self._parse_html_events(soup)

        except Exception as e:
            logger.error(f"Error scraping Longfellow House: {e}")

        logger.info(f"Scraped {len(events)} events from Longfellow House")
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
                description = f"{title} at Longfellow House"

            url = data.get('url', self.source_url)

            image_url = data.get('image')
            if isinstance(image_url, list) and image_url:
                image_url = image_url[0]

            return EventCreate(
                title=title[:200],
                description=self.clean_text(description)[:2000],
                start_datetime=start_datetime,
                venue_name="Longfellow House",
                street_address="105 Brattle Street",
                city="Cambridge",
                state="MA",
                zip_code="02138",
                category=EventCategory.ARTS_CULTURE,
                source_name=self.source_name,
                source_url=url,
                image_url=image_url,
            )

        except Exception as e:
            logger.debug(f"Error parsing JSON-LD event: {e}")
            return None

    def _parse_html_events(self, soup) -> List[EventCreate]:
        """Parse events from HTML"""
        events = []
        seen_titles = set()

        # NPS calendar loads dynamically - look for event containers
        event_items = soup.find_all('div', class_=re.compile(r'event|calendar-item'))
        if not event_items:
            # Look for links to events
            event_links = soup.find_all('a', href=re.compile(r'event'))
            event_items = [link.parent for link in event_links]

        # Also search page text for event patterns
        text = soup.get_text()

        # Pattern: Look for dates followed by event names
        # NPS often uses format: "Month Day - Event Name"
        pattern = r'(\w+)\s+(\d{1,2})(?:,?\s+(\d{4}))?\s*[-–]\s*([^\n]+)'
        matches = re.findall(pattern, text)

        for match in matches:
            try:
                month_str, day, year, title = match

                # Parse month
                try:
                    month = datetime.strptime(month_str, '%B').month
                except:
                    try:
                        month = datetime.strptime(month_str, '%b').month
                    except:
                        continue

                # Default year
                if not year:
                    year = datetime.now().year
                    # If month is in the past, use next year
                    if month < datetime.now().month:
                        year += 1

                title = self.clean_text(title)
                if not title or len(title) < 5:
                    continue

                # Skip duplicates
                title_key = f"{title}_{month}_{day}"
                if title_key in seen_titles:
                    continue
                seen_titles.add(title_key)

                start_datetime = datetime(int(year), month, int(day), 10, 0)

                event = EventCreate(
                    title=title[:200],
                    description=f"{title} at Longfellow House - Washington's Headquarters National Historic Site",
                    start_datetime=start_datetime,
                    venue_name="Longfellow House",
                    street_address="105 Brattle Street",
                    city="Cambridge",
                    state="MA",
                    zip_code="02138",
                    category=EventCategory.ARTS_CULTURE,
                    source_name=self.source_name,
                    source_url=self.source_url,
                )
                events.append(event)

            except Exception as e:
                logger.debug(f"Error parsing event: {e}")
                continue

        return events
