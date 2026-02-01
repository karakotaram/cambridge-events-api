"""Scraper for MIT Open Space Programming events using Playwright"""
import logging
import re
import json
from datetime import datetime
from typing import List, Optional

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class OpenSpaceMITScraper(BasePlaywrightScraper):
    """Scraper for MIT Open Space Programming events"""

    def __init__(self):
        super().__init__(
            source_name="MIT Open Space",
            source_url="https://www.openspace.mit.edu/calendar"
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from MIT Open Space"""
        events = []

        try:
            self.goto(self.source_url, wait_until="networkidle")
            self.page.wait_for_timeout(3000)

            # Scroll to load more events
            self.scroll_to_bottom(delay=1000)

            soup = self.get_soup()

            # Parse events from page
            events = self._parse_events(soup)

        except Exception as e:
            logger.error(f"Error scraping MIT Open Space: {e}")

        logger.info(f"Scraped {len(events)} events from MIT Open Space")
        return events

    def _parse_events(self, soup) -> List[EventCreate]:
        """Parse events from page"""
        events = []
        seen_urls = set()

        # Find event containers - Squarespace uses summary-item or event-item classes
        event_items = soup.find_all('div', class_=re.compile(r'summary-item|event-item|eventlist-event'))
        if not event_items:
            # Look for article elements
            event_items = soup.find_all('article')

        for item in event_items:
            try:
                # Get title and link
                title_elem = item.find(['h1', 'h2', 'h3', 'h4'])
                if not title_elem:
                    continue

                link = title_elem.find('a') or item.find('a')
                title = self.clean_text(title_elem.get_text())

                if not title or len(title) < 3:
                    continue

                # Skip cancelled events
                if 'cancelled' in title.lower() or 'rescheduled' in title.lower():
                    continue

                url = link.get('href') if link else self.source_url
                if url and not url.startswith('http'):
                    url = f"https://www.openspace.mit.edu{url}"

                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # Parse date/time
                text = item.get_text()
                start_datetime = self._parse_date_time(text)

                # Get description
                desc_elem = item.find(class_=re.compile(r'summary-excerpt|excerpt|description'))
                description = self.clean_text(desc_elem.get_text()) if desc_elem else f"{title} - MIT Open Space Programming"

                # Get image
                img = item.find('img')
                image_url = img.get('src') if img else None

                category = self._detect_category(title, description)

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    venue_name="MIT Open Space",
                    street_address="292 Main Street",
                    city="Cambridge",
                    state="MA",
                    zip_code="02142",
                    category=category,
                    source_name=self.source_name,
                    source_url=url,
                    image_url=image_url,
                )
                events.append(event)

            except Exception as e:
                logger.debug(f"Error parsing MIT Open Space event: {e}")
                continue

        return events

    def _parse_date_time(self, text: str) -> datetime:
        """Parse date/time from text like 'Monday, February 2, 2026 6:00 PM'"""
        default = datetime.now().replace(hour=18, minute=0, second=0, microsecond=0)

        try:
            # Pattern: Day, Month DD, YYYY
            pattern = r'(\w+),\s+(\w+)\s+(\d{1,2}),\s+(\d{4})'
            match = re.search(pattern, text)

            if match:
                _, month_str, day, year = match.groups()
                try:
                    month = datetime.strptime(month_str, '%B').month
                except:
                    month = datetime.strptime(month_str[:3], '%b').month

                event_date = datetime(int(year), month, int(day))

                # Look for time
                time_match = re.search(r'(\d{1,2}):(\d{2})\s*(AM|PM)', text, re.I)
                if time_match:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2))
                    if time_match.group(3).upper() == 'PM' and hour != 12:
                        hour += 12
                    elif time_match.group(3).upper() == 'AM' and hour == 12:
                        hour = 0
                    event_date = event_date.replace(hour=hour, minute=minute)
                else:
                    event_date = event_date.replace(hour=18, minute=0)

                return event_date

        except Exception as e:
            logger.debug(f"Error parsing date from '{text}': {e}")

        return default

    def _detect_category(self, title: str, description: str) -> EventCategory:
        """Detect event category"""
        text = f"{title} {description}".lower()

        if any(word in text for word in ['concert', 'music', 'band', 'jazz', 'performance']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['art', 'exhibit', 'gallery', 'opening']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['comedy', 'improv', 'standup']):
            return EventCategory.THEATER
        elif any(word in text for word in ['lecture', 'talk', 'speaker']):
            return EventCategory.LECTURES
        elif any(word in text for word in ['game', 'trivia', 'social']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['film', 'movie', 'screening']):
            return EventCategory.ARTS_CULTURE

        return EventCategory.COMMUNITY
