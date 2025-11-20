"""Custom scraper for Cambridge.gov events"""
import re
from datetime import datetime, timedelta
from typing import List
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class CambridgeGovScraper(BaseScraper):
    """Custom scraper for City of Cambridge events"""

    def __init__(self):
        super().__init__(
            source_name="City of Cambridge",
            source_url="https://www.cambridgema.gov/citycalendar",
            use_selenium=True  # May need JavaScript rendering
        )

    def fetch_event_description(self, event_url: str) -> str:
        """Fetch the full description from an event detail page"""
        try:
            if not self.driver:
                return ""

            # Navigate to event page
            self.driver.get(event_url)
            import time
            time.sleep(2)

            # Parse the page
            html = self.driver.page_source
            soup = self.parse_html(html)

            # Look for description in meta tags first
            meta_desc = soup.find('meta', {'property': 'og:description'})
            if meta_desc and meta_desc.get('content'):
                desc_text = meta_desc.get('content')
                if len(desc_text) > 20 and 'Thursday,' not in desc_text and 'Monday,' not in desc_text:
                    return self.clean_text(desc_text)[:2000]

            # Look for paragraphs in main content
            main_content = soup.find('main') or soup.find('article')
            if main_content:
                paragraphs = main_content.find_all('p')
                description_parts = []

                for p in paragraphs:
                    text = self.clean_text(p.get_text())
                    # Skip short text and government boilerplate
                    if len(text) > 50 and not any(skip in text.lower() for skip in [
                        'official website', '.gov website', 'secure .gov',
                        'quick links', 'contact', 'calendar'
                    ]):
                        description_parts.append(text)

                if description_parts:
                    full_description = ' '.join(description_parts[:3])
                    return full_description[:2000] if len(full_description) > 2000 else full_description

            return ""
        except Exception as e:
            return ""

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Cambridge.gov city calendar for the current month"""
        events = []
        seen_urls = set()  # Track URLs to avoid duplicates

        # Get events for the whole current month
        today = datetime.now()
        # Start from beginning of current month
        start_date = today.replace(day=1)
        # End at end of current month
        if today.month == 12:
            end_date = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_date = today.replace(month=today.month + 1, day=1) - timedelta(days=1)

        # Scrape each day of the month
        current_date = start_date
        while current_date <= end_date:
            # Format: YYYYMMDDTHHMMSS
            date_str = current_date.strftime("%Y%m%dT000000")
            day_url = f"{self.source_url}?start={date_str}&view=Day&page=1&resultsperpage=50"

            try:
                html = self.fetch_html(day_url)
                soup = self.parse_html(html)

                # Find event links for this day
                event_links = soup.find_all('a', href=lambda x: x and '/citycalendar/view.aspx?guid=' in x if x else False)

                for link in event_links:
                    try:
                        # Extract title from link text
                        title = self.clean_text(link.get_text())
                        if len(title) < 5:
                            continue

                        # Extract event URL
                        event_url = link.get('href', '')
                        if event_url.startswith('/'):
                            event_url = f"https://www.cambridgema.gov{event_url}"
                        elif not event_url.startswith('http'):
                            event_url = self.source_url

                        # Skip if we've already seen this URL
                        if event_url in seen_urls:
                            continue
                        seen_urls.add(event_url)

                        # Use current_date as the start date
                        start_datetime = current_date

                        # Try to find more specific time in parent
                        parent = link.find_parent()
                        if parent:
                            # Look for time information
                            for element in parent.find_all_previous(['strong', 'b', 'p', 'div'], limit=5):
                                text = self.clean_text(element.get_text())
                                if 'AM' in text or 'PM' in text:
                                    try:
                                        parsed_datetime = date_parser.parse(f"{current_date.date()} {text}", fuzzy=True)
                                        start_datetime = parsed_datetime
                                        break
                                    except:
                                        pass

                        # Fetch description from detail page
                        description = self.fetch_event_description(event_url)
                        if not description or len(description) < 20:
                            description = title

                        # Extract location from parent if available
                        venue_name = None
                        street_address = None
                        if parent:
                            for element in parent.find_all_next(['em', 'i', 'p'], limit=10):
                                text = self.clean_text(element.get_text())
                                if text and len(text) < 150 and any(word in text.lower() for word in ['street', 'avenue', 'road', 'blvd', 'drive', 'square', 'place']):
                                    parts = text.split(',')
                                    if len(parts) >= 1:
                                        venue_name = parts[0].strip()[:200]
                                    if len(parts) >= 2:
                                        street_address = ', '.join(parts[1:]).strip()[:200]
                                    break

                        # Determine category based on content
                        category = self.categorize_event(title, description)

                        event = EventCreate(
                            title=title[:200],
                            description=description[:2000],
                            start_datetime=start_datetime,
                            source_url=event_url,
                            source_name=self.source_name,
                            venue_name=venue_name,
                            street_address=street_address,
                            city="Cambridge",
                            state="MA",
                            category=category
                        )
                        events.append(event)

                    except Exception as e:
                        continue

            except Exception as e:
                pass

            # Move to next day
            current_date += timedelta(days=1)

        return events

    def categorize_event(self, title: str, description: str) -> EventCategory:
        """Categorize event based on keywords"""
        text = f"{title} {description}".lower()

        # Check trivia first to ensure it takes priority
        if any(word in text for word in ['trivia', 'quiz', 'jeopardy', 'bingo']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['concert', 'music', 'band', 'orchestra']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['art', 'gallery', 'exhibit', 'museum']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['theater', 'play', 'performance', 'drama']):
            return EventCategory.THEATER
        elif any(word in text for word in ['lecture', 'talk', 'presentation', 'seminar']):
            return EventCategory.LECTURES
        elif any(word in text for word in ['sport', 'game', 'tournament', 'fitness']):
            return EventCategory.SPORTS
        elif any(word in text for word in ['food', 'restaurant', 'dining', 'drink']):
            return EventCategory.FOOD_DRINK
        elif any(word in text for word in ['community', 'meeting', 'council', 'public']):
            return EventCategory.COMMUNITY
        else:
            return EventCategory.OTHER
