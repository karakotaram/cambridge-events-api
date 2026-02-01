"""Scraper for Cambridge Public Library events using Playwright"""
import logging
import re
import json
from datetime import datetime
from typing import List, Optional

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class CambridgeLibraryScraper(BasePlaywrightScraper):
    """Scraper for Cambridge Public Library events"""

    def __init__(self):
        super().__init__(
            source_name="Cambridge Public Library",
            source_url="https://www.cambridgema.gov/Departments/cambridgepubliclibrary/calendar"
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Cambridge Public Library"""
        events = []
        seen_urls = set()

        try:
            self.goto(self.source_url, wait_until="networkidle")
            self.page.wait_for_timeout(2000)

            # Scrape multiple days by navigating forward
            for day_offset in range(7):  # Get events for next 7 days
                soup = self.get_soup()
                page_events = self._parse_events(soup, seen_urls)
                events.extend(page_events)

                # Try to go to next day
                try:
                    next_btn = self.page.query_selector('a:has-text("Next")')
                    if next_btn and day_offset < 6:
                        next_btn.click()
                        self.page.wait_for_timeout(1000)
                except:
                    break

        except Exception as e:
            logger.error(f"Error scraping Cambridge Library: {e}")

        logger.info(f"Scraped {len(events)} events from Cambridge Public Library")
        return events

    def _parse_events(self, soup, seen_urls: set) -> List[EventCreate]:
        """Parse events from page"""
        events = []

        # Find event containers - look for event listings
        event_items = soup.find_all('div', class_=re.compile(r'event|program|item'))
        if not event_items:
            # Try finding by structure - events usually have h3/h4 headers
            event_items = soup.find_all(['article', 'section'])

        # Also look for links to event detail pages
        event_links = soup.find_all('a', href=re.compile(r'/calendar/.*guid'))

        for link in event_links:
            try:
                url = link.get('href', '')
                if not url.startswith('http'):
                    url = f"https://www.cambridgema.gov{url}"

                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # Get the parent container for more context
                parent = link.find_parent(['div', 'article', 'section', 'li'])
                if not parent:
                    parent = link

                title = self.clean_text(link.get_text())
                if not title or len(title) < 3:
                    continue

                # Parse date/time from surrounding text
                text = parent.get_text() if parent else ""
                start_datetime = self._parse_date_time(text)

                # Get location
                location_match = re.search(r'at\s+([\w\s]+Library)', text, re.I)
                venue_name = location_match.group(1) if location_match else "Cambridge Public Library"

                # Get description
                desc_elem = parent.find('p') if parent else None
                description = self.clean_text(desc_elem.get_text()) if desc_elem else f"{title} at Cambridge Public Library"

                category = self._detect_category(title, description)

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    venue_name=venue_name,
                    street_address="449 Broadway",
                    city="Cambridge",
                    state="MA",
                    zip_code="02138",
                    category=category,
                    source_name=self.source_name,
                    source_url=url,
                    family_friendly=True,
                )
                events.append(event)

            except Exception as e:
                logger.debug(f"Error parsing library event: {e}")
                continue

        return events

    def _parse_date_time(self, text: str) -> datetime:
        """Parse date/time from text"""
        default = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0)

        try:
            # Look for time pattern like "9:30 AM" or "2:00 PM"
            time_match = re.search(r'(\d{1,2}):(\d{2})\s*(AM|PM)', text, re.I)

            # Look for date in URL format like 20260131
            date_match = re.search(r'(\d{4})(\d{2})(\d{2})', text)

            if date_match:
                year, month, day = date_match.groups()
                event_date = datetime(int(year), int(month), int(day))
            else:
                # Try written date format
                pattern = r'(\w+)\s+(\d{1,2}),?\s+(\d{4})'
                written_match = re.search(pattern, text)
                if written_match:
                    month_str, day, year = written_match.groups()
                    try:
                        month = datetime.strptime(month_str, '%B').month
                    except:
                        month = datetime.strptime(month_str[:3], '%b').month
                    event_date = datetime(int(year), month, int(day))
                else:
                    event_date = default

            # Add time if found
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2))
                if time_match.group(3).upper() == 'PM' and hour != 12:
                    hour += 12
                elif time_match.group(3).upper() == 'AM' and hour == 12:
                    hour = 0
                event_date = event_date.replace(hour=hour, minute=minute)

            return event_date

        except Exception as e:
            logger.debug(f"Error parsing date from '{text}': {e}")

        return default

    def _detect_category(self, title: str, description: str) -> EventCategory:
        """Detect event category"""
        text = f"{title} {description}".lower()

        if any(word in text for word in ['story time', 'children', 'kids', 'family', 'toddler', 'baby']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['book club', 'reading', 'author', 'writing']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['craft', 'maker', 'sewing', '3d print', 'workshop']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['lecture', 'talk', 'seminar', 'discussion']):
            return EventCategory.LECTURES
        elif any(word in text for word in ['concert', 'music', 'performance']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['film', 'movie', 'screening']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['exercise', 'yoga', 'fitness', 'health']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['tech', 'computer', 'digital', 'coding']):
            return EventCategory.LECTURES

        return EventCategory.COMMUNITY
