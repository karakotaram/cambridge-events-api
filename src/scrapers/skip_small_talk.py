"""Scraper for Skip the Small Talk events using Playwright"""
import logging
import re
import json
from datetime import datetime
from typing import List, Optional

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class SkipSmallTalkScraper(BasePlaywrightScraper):
    """Scraper for Skip the Small Talk - conversation events"""

    def __init__(self):
        super().__init__(
            source_name="Skip the Small Talk",
            source_url="http://www.skipthesmalltalk.com/public-events?category=Boston"
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Skip the Small Talk"""
        events = []

        try:
            # Try Boston category first
            self.goto(self.source_url, wait_until="networkidle")
            self.page.wait_for_timeout(3000)

            soup = self.get_soup()
            events = self._parse_events(soup)

            # Also try Cambridge if exists
            try:
                self.goto("http://www.skipthesmalltalk.com/public-events?category=Cambridge", wait_until="networkidle")
                self.page.wait_for_timeout(2000)
                soup = self.get_soup()
                cambridge_events = self._parse_events(soup)

                # Add non-duplicates
                seen_urls = {e.source_url for e in events}
                for e in cambridge_events:
                    if e.source_url not in seen_urls:
                        events.append(e)
            except:
                pass

        except Exception as e:
            logger.error(f"Error scraping Skip the Small Talk: {e}")

        logger.info(f"Scraped {len(events)} events from Skip the Small Talk")
        return events

    def _parse_events(self, soup) -> List[EventCreate]:
        """Parse events from page"""
        events = []
        seen_urls = set()

        # Find event containers - Squarespace summary items
        event_items = soup.find_all('div', class_=re.compile(r'summary-item'))
        if not event_items:
            event_items = soup.find_all('article')

        for item in event_items:
            try:
                # Get link
                link = item.find('a', href=True)
                if not link:
                    continue

                url = link.get('href', '')
                if not url.startswith('http'):
                    url = f"http://www.skipthesmalltalk.com{url}"

                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # Get title - extract just the event type and date
                text = item.get_text()

                # Extract event type (Dating, Open to Everyone, LGBTQIA+, etc.)
                event_type_match = re.search(r'(Dating|Open to Everyone|LGBTQIA\+|Ages \d+-\d+|Women|Men)', text, re.I)
                event_type = event_type_match.group(1) if event_type_match else "Social"

                # Extract venue
                venue_match = re.search(r',\s*([^,]+),\s*\(map\)', text)
                venue = venue_match.group(1).strip() if venue_match else ""

                # Build a clean title
                title = f"Skip the Small Talk - {event_type}"
                if venue:
                    title += f" at {venue}"

                if not title or len(title) < 5:
                    continue

                # Parse date from metadata
                # Format: "Thursday, January 29, 2026, 6:30 pm"
                text = item.get_text()
                start_datetime = self._parse_date_time(text)

                # Get location from text
                venue_name = "Skip the Small Talk Event"
                location_match = re.search(r'(Boston|Cambridge|Somerville)', text, re.I)
                if location_match:
                    city = location_match.group(1).title()
                else:
                    city = "Boston"

                # Build a detailed description
                base_desc = f"{title}. Skip the Small Talk events help strangers really get to know each other using conversation methods grounded in psychology research. Come meet interesting people and have meaningful conversations in a fun, structured environment."

                # Try to get additional details from the page
                desc_elem = item.find(class_=re.compile(r'excerpt|description|summary'))
                if desc_elem:
                    extra = self.clean_text(desc_elem.get_text())
                    if extra and len(extra) > 20 and extra.lower() not in ['featured', 'sale']:
                        base_desc += f" {extra}"

                description = base_desc

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    venue_name=venue_name,
                    city=city,
                    state="MA",
                    category=EventCategory.COMMUNITY,
                    source_name=self.source_name,
                    source_url=url,
                )
                events.append(event)

            except Exception as e:
                logger.debug(f"Error parsing event: {e}")
                continue

        return events

    def _parse_date_time(self, text: str) -> datetime:
        """Parse date/time from text like 'Thursday, January 29, 2026, 6:30 pm'"""
        default = datetime.now().replace(hour=18, minute=30, second=0, microsecond=0)

        try:
            # Pattern: Day, Month DD, YYYY, HH:MM am/pm
            pattern = r'(\w+),\s+(\w+)\s+(\d{1,2}),\s+(\d{4}),?\s+(\d{1,2}):(\d{2})\s*(am|pm)'
            match = re.search(pattern, text, re.I)

            if match:
                _, month_str, day, year, hour, minute, ampm = match.groups()
                month = datetime.strptime(month_str, '%B').month
                hour = int(hour)
                minute = int(minute)
                if ampm.lower() == 'pm' and hour != 12:
                    hour += 12
                elif ampm.lower() == 'am' and hour == 12:
                    hour = 0

                return datetime(int(year), month, int(day), hour, minute)

            # Try simpler pattern
            pattern2 = r'(\w+)\s+(\d{1,2}),?\s+(\d{4})'
            match2 = re.search(pattern2, text)
            if match2:
                month_str, day, year = match2.groups()
                month = datetime.strptime(month_str, '%B').month
                return datetime(int(year), month, int(day), 18, 30)

        except Exception as e:
            logger.debug(f"Error parsing date: {e}")

        return default
