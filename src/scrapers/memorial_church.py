"""Scraper for Harvard Memorial Church events using Playwright"""
import logging
import re
import json
from datetime import datetime
from typing import List, Optional

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class MemorialChurchScraper(BasePlaywrightScraper):
    """Scraper for Harvard Memorial Church calendar"""

    def __init__(self):
        super().__init__(
            source_name="Harvard Memorial Church",
            source_url="https://memorialchurch.harvard.edu/calendar"
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Memorial Church calendar"""
        events = []
        seen_urls = set()

        try:
            self.goto(self.source_url, wait_until="networkidle")
            self.page.wait_for_timeout(2000)

            # Scrape first page
            page_events = self._parse_page_events()
            for e in page_events:
                if e.source_url not in seen_urls:
                    events.append(e)
                    seen_urls.add(e.source_url)

            # Try to get more pages (up to 3)
            for page_num in range(2, 4):
                try:
                    next_link = self.page.query_selector(f'a[href*="page={page_num}"]')
                    if not next_link:
                        break

                    next_link.click()
                    self.page.wait_for_timeout(2000)

                    page_events = self._parse_page_events()
                    for e in page_events:
                        if e.source_url not in seen_urls:
                            events.append(e)
                            seen_urls.add(e.source_url)

                except Exception as e:
                    logger.debug(f"Error loading page {page_num}: {e}")
                    break

        except Exception as e:
            logger.error(f"Error scraping Memorial Church: {e}")

        logger.info(f"Scraped {len(events)} events from Memorial Church")
        return events

    def _parse_page_events(self) -> List[EventCreate]:
        """Parse events from current page"""
        events = []
        soup = self.get_soup()

        # Find event items
        event_items = soup.find_all('article', class_=re.compile(r'node.*event'))
        if not event_items:
            event_items = soup.find_all('div', class_=re.compile(r'views-row|event-item'))
        if not event_items:
            # Try finding links that look like event links
            event_items = soup.find_all('a', href=re.compile(r'/event/'))

        for item in event_items:
            try:
                event = self._parse_event_item(item)
                if event:
                    events.append(event)
            except Exception as e:
                logger.debug(f"Error parsing event item: {e}")
                continue

        return events

    def _parse_event_item(self, item) -> Optional[EventCreate]:
        """Parse a single event item"""
        try:
            # Get title and link
            if item.name == 'a':
                title = self.clean_text(item.get_text())
                url = item.get('href', '')
            else:
                title_elem = item.find(['h2', 'h3', 'h4', 'a'])
                if not title_elem:
                    return None

                if title_elem.name == 'a':
                    title = self.clean_text(title_elem.get_text())
                    url = title_elem.get('href', '')
                else:
                    link = title_elem.find('a') or item.find('a')
                    title = self.clean_text(title_elem.get_text())
                    url = link.get('href') if link else ''

            if not title or len(title) < 3:
                return None

            # Make URL absolute
            if url and not url.startswith('http'):
                url = f"https://memorialchurch.harvard.edu{url}"
            else:
                url = self.source_url

            # Parse date from text
            text = item.get_text() if hasattr(item, 'get_text') else str(item)
            start_datetime = self._parse_date_time(text)

            # Get location
            location_elem = item.find(class_=re.compile(r'location|venue'))
            location = self.clean_text(location_elem.get_text()) if location_elem else "Harvard Memorial Church"

            # Get description
            desc_elem = item.find(class_=re.compile(r'body|summary|description|teaser'))
            description = self.clean_text(desc_elem.get_text()) if desc_elem else f"{title} at Harvard Memorial Church"

            category = self._detect_category(title, description)

            return EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start_datetime,
                venue_name="Harvard Memorial Church",
                street_address="1 Harvard Yard",
                city="Cambridge",
                state="MA",
                zip_code="02138",
                category=category,
                source_name=self.source_name,
                source_url=url,
            )

        except Exception as e:
            logger.debug(f"Error parsing event: {e}")
            return None

    def _parse_date_time(self, text: str) -> datetime:
        """Parse date/time from text like 'Feb. 1 - May. 17, 2026 9:30AM - 10:30AM EST'"""
        default = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)

        try:
            # Try pattern: Month. Day, Year
            # or Month Day, Year
            month_abbrs = {
                'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
                'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12
            }

            # Look for date pattern
            pattern = r'(\w{3})\.?\s+(\d{1,2})(?:,?\s+|-\s*\w{3}\.?\s+\d{1,2},?\s+)(\d{4})'
            match = re.search(pattern, text, re.I)

            if match:
                month_str, day, year = match.groups()
                month = month_abbrs.get(month_str.lower()[:3], 1)
                event_date = datetime(int(year), month, int(day))

                # Look for time
                time_pattern = r'(\d{1,2}):(\d{2})\s*(AM|PM)'
                time_match = re.search(time_pattern, text, re.I)

                if time_match:
                    hour, minute, ampm = time_match.groups()
                    hour = int(hour)
                    minute = int(minute)
                    if ampm.upper() == 'PM' and hour != 12:
                        hour += 12
                    elif ampm.upper() == 'AM' and hour == 12:
                        hour = 0
                    event_date = event_date.replace(hour=hour, minute=minute)
                else:
                    event_date = event_date.replace(hour=10, minute=0)

                return event_date

        except Exception as e:
            logger.debug(f"Error parsing date from '{text}': {e}")

        return default

    def _detect_category(self, title: str, description: str) -> EventCategory:
        """Detect event category"""
        text = f"{title} {description}".lower()

        if any(word in text for word in ['service', 'worship', 'prayer', 'sermon', 'sunday']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['concert', 'music', 'recital', 'choir', 'organ', 'choral']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['lecture', 'talk', 'discussion', 'forum', 'seminar']):
            return EventCategory.LECTURES
        elif any(word in text for word in ['meditation', 'reflection', 'contemplative']):
            return EventCategory.COMMUNITY

        return EventCategory.COMMUNITY
