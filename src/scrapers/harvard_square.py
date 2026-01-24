"""Scraper for Harvard Square Business Association events"""
import logging
import re
import time
from datetime import datetime, timedelta
from typing import List, Optional
import requests
from bs4 import BeautifulSoup

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class HarvardSquareScraper(BaseScraper):
    """Scraper for Harvard Square Business Association events page"""

    def __init__(self):
        super().__init__(
            source_name="Harvard Square",
            source_url="https://www.harvardsquare.com/events/",
            use_selenium=False
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Harvard Square for the next 30 days"""
        events = []
        seen_urls = set()  # Track URLs to avoid duplicates

        # Scrape events for the next 30 days
        current_date = datetime.now()
        for day_offset in range(30):
            target_date = current_date + timedelta(days=day_offset)
            date_str = target_date.strftime("%Y-%m-%d")
            url = f"https://www.harvardsquare.com/events/{date_str}/"

            try:
                day_events = self._scrape_day(url, target_date, seen_urls)
                events.extend(day_events)

                if day_offset % 7 == 0:
                    logger.info(f"Scraped {len(events)} events through {date_str}")

                # Rate limiting
                time.sleep(0.5)

            except Exception as e:
                logger.warning(f"Error scraping {date_str}: {e}")
                continue

        logger.info(f"Scraped {len(events)} total events from Harvard Square")
        return events

    def _scrape_day(self, url: str, target_date: datetime, seen_urls: set) -> List[EventCreate]:
        """Scrape events for a single day"""
        events = []

        try:
            response = requests.get(url, timeout=30, headers=self.get_browser_headers())
            if response.status_code == 404:
                return []
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')

            # Find event containers - they're div.type-tribe_events
            event_divs = soup.find_all('div', class_=lambda x: x and 'type-tribe_events' in str(x))

            for event_div in event_divs:
                try:
                    # Find title - h3.tribe-events-list-event-title
                    title_elem = event_div.find('h3', class_='tribe-events-list-event-title')
                    if not title_elem:
                        continue

                    link = title_elem.find('a')
                    if not link:
                        continue

                    title = self.clean_text(link.get_text())
                    event_url = link.get('href', '')

                    if not title or len(title) < 3 or not event_url:
                        continue

                    # Skip if we've seen this URL
                    if event_url in seen_urls:
                        continue
                    seen_urls.add(event_url)

                    # Find time from span.tribe-event-date-start or div.time-details
                    event_time = None
                    time_details = event_div.find('div', class_='time-details')
                    if time_details:
                        time_text = self.clean_text(time_details.get_text())
                        event_time = self._parse_time(time_text)

                    # Build datetime
                    if event_time:
                        start_datetime = target_date.replace(
                            hour=event_time[0],
                            minute=event_time[1],
                            second=0,
                            microsecond=0
                        )
                    else:
                        # Default to noon for all-day events
                        start_datetime = target_date.replace(hour=12, minute=0, second=0, microsecond=0)

                    # Find venue from div.tribe-events-venue-details
                    venue_name = None
                    venue_div = event_div.find('div', class_='tribe-events-venue-details')
                    if venue_div:
                        venue_link = venue_div.find('a')
                        if venue_link and 'Google Map' not in venue_link.get_text():
                            venue_name = self.clean_text(venue_link.get_text())
                        else:
                            # Try first link that's not Google Map
                            for link in venue_div.find_all('a'):
                                if 'Google Map' not in link.get_text():
                                    venue_name = self.clean_text(link.get_text())
                                    break

                    # Find description from div.tribe-events-list-event-description
                    description = None
                    desc_div = event_div.find('div', class_='tribe-events-list-event-description')
                    if desc_div:
                        # Get text from p tags
                        p_tags = desc_div.find_all('p')
                        if p_tags:
                            description = self.clean_text(' '.join(p.get_text() for p in p_tags))
                        else:
                            description = self.clean_text(desc_div.get_text())

                    if not description:
                        description = f"{title} - Event in Harvard Square"

                    # Detect category
                    category = self._detect_category(title, description, venue_name)

                    event = EventCreate(
                        title=title[:200],
                        description=description[:2000],
                        start_datetime=start_datetime,
                        venue_name=venue_name,
                        city="Cambridge",
                        state="MA",
                        category=category,
                        source_name=self.source_name,
                        source_url=event_url,
                    )
                    events.append(event)

                except Exception as e:
                    logger.debug(f"Error parsing event: {e}")
                    continue

        except Exception as e:
            logger.debug(f"Error fetching {url}: {e}")

        return events

    def _parse_time(self, time_text: str) -> Optional[tuple]:
        """Parse time text like '7:00 PM' or '@ 5:00 pm' into (hour, minute) tuple"""
        if not time_text:
            return None

        # First try to match "@ 5:00 pm" or "@ 5 pm" style (Harvard Square format)
        match = re.search(r'@\s*(\d{1,2}):?(\d{2})?\s*(am|pm)', time_text, re.I)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2) or 0)
            am_pm = match.group(3).lower()

            if am_pm == 'pm' and hour != 12:
                hour += 12
            elif am_pm == 'am' and hour == 12:
                hour = 0

            return (hour, minute)

        # Try to match standalone time like "7:00 PM" (must have am/pm to avoid matching dates)
        match = re.search(r'\b(\d{1,2}):(\d{2})\s*(am|pm)\b', time_text, re.I)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            am_pm = match.group(3).lower()

            if am_pm == 'pm' and hour != 12:
                hour += 12
            elif am_pm == 'am' and hour == 12:
                hour = 0

            return (hour, minute)

        return None

    def _detect_category(self, title: str, description: str, venue_name: str = None) -> EventCategory:
        """Detect event category from title, description, and venue"""
        text = f"{title} {description}".lower()
        venue = (venue_name or "").lower()

        # Music venues - categorize as music regardless of description
        music_venues = ['passim', 'sinclair', 'regattabar', 'scullers', 'club passim', 'the sinclair']
        if any(v in venue for v in music_venues):
            return EventCategory.MUSIC

        # Comedy venues
        comedy_venues = ['comedy studio', 'improv']
        if any(v in venue for v in comedy_venues):
            return EventCategory.THEATER

        # Theater venues
        theater_venues = ['a.r.t.', 'american repertory', 'loeb drama', 'brattle theatre', 'oberon']
        if any(v in venue for v in theater_venues):
            return EventCategory.THEATER

        if any(word in text for word in ['concert', 'music', 'jazz', 'band', 'singer', 'orchestra', 'symphony', 'folk', 'blues']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['theater', 'theatre', 'play', 'musical', 'drama', 'comedy show', 'improv', 'comedy']):
            return EventCategory.THEATER
        elif any(word in text for word in ['art', 'exhibition', 'gallery', 'museum', 'painting', 'sculpture']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['lecture', 'talk', 'author', 'reading', 'book', 'discussion', 'seminar']):
            return EventCategory.LECTURES
        elif any(word in text for word in ['food', 'drink', 'tasting', 'beer', 'wine', 'restaurant', 'dining']):
            return EventCategory.FOOD_DRINK
        elif any(word in text for word in ['sports', 'fitness', 'run', 'race', 'yoga', 'workout']):
            return EventCategory.SPORTS
        elif any(word in text for word in ['community', 'meeting', 'volunteer', 'fundraiser', 'charity']):
            return EventCategory.COMMUNITY

        return EventCategory.OTHER
