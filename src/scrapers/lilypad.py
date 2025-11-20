"""Custom scraper for The Lily Pad music venue"""
import re
from datetime import datetime
from typing import List
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class LilyPadScraper(BaseScraper):
    """Custom scraper for The Lily Pad events"""

    def __init__(self):
        super().__init__(
            source_name="The Lily Pad",
            source_url="https://www.lilypadinman.com/",
            use_selenium=True  # JavaScript-rendered content
        )

    def fetch_event_description(self, event_url: str) -> str:
        """Fetch the full description from an event detail page"""
        try:
            if not self.driver:
                return ""

            # Navigate to event page
            self.driver.get(event_url)
            import time
            time.sleep(2)  # Wait for page load

            # Parse the page
            html = self.driver.page_source
            soup = self.parse_html(html)

            # Find description in .sqs-html-content divs
            description_parts = []
            content_divs = soup.find_all(class_='sqs-html-content')

            for div in content_divs:
                paragraphs = div.find_all('p')
                for p in paragraphs:
                    text = self.clean_text(p.get_text())

                    # Skip template text and metadata
                    text_lower = text.lower()
                    if any(skip_phrase in text_lower for skip_phrase in [
                        'your custom text here',
                        'click here',
                        'learn more',
                        'buy tickets'
                    ]):
                        continue

                    # Skip if it's just price/time info (short with $ or AM/PM)
                    if len(text) < 50 and ('$' in text or 'AM' in text or 'PM' in text or ':' in text):
                        continue

                    # Only include substantial paragraphs
                    if len(text) > 30:
                        description_parts.append(text)

            if description_parts:
                full_description = ' '.join(description_parts)

                # Remove "Your Custom Text Here" if it appears at the start
                full_description = re.sub(r'^Your Custom Text Here\s*', '', full_description, flags=re.IGNORECASE)

                return full_description[:2000]

            return ""
        except Exception as e:
            return ""

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from The Lily Pad"""
        html = self.fetch_html(self.source_url)

        # Add extra wait for JavaScript to load
        if self.driver:
            import time
            time.sleep(3)  # Give JS time to render events
            html = self.driver.page_source

        soup = self.parse_html(html)

        events = []

        # Find all event containers
        event_elements = soup.find_all(class_='eventlist-event')

        # Debug: print how many elements were found
        if len(event_elements) == 0:
            # Try alternative selectors
            event_elements = soup.find_all('div', class_=re.compile(r'eventlist'))
            if len(event_elements) == 0:
                # Try finding by article or other tags
                event_elements = soup.find_all('article')

        # Only process upcoming events (not past events)
        # Filter to events marked as upcoming
        upcoming_elements = [e for e in event_elements if 'eventlist--upcoming' in str(e.get('class', []))]
        if upcoming_elements:
            event_elements = upcoming_elements

        # Limit to reasonable number to avoid excessive page loads
        # Process first 30 events
        event_elements = event_elements[:30]

        for element in event_elements:
            try:
                # Extract title and URL using the correct class
                title_link = element.find('a', class_='eventlist-title-link')
                if not title_link:
                    # Fallback: try any link
                    title_link = element.find('a', href=True)
                if not title_link:
                    continue

                title = self.clean_text(title_link.get_text())
                if len(title) < 3:
                    continue

                # Get all text from the element
                full_text = self.clean_text(element.get_text())

                # Skip private events
                full_text_lower = full_text.lower()
                private_keywords = ['private party', 'private event', 'closed to public',
                                   'invite only', 'members only', 'by invitation']
                if any(keyword in full_text_lower for keyword in private_keywords):
                    continue

                # Build full event URL
                event_url = title_link.get('href', '')
                if event_url.startswith('/'):
                    event_url = f"https://www.lilypadinman.com{event_url}"
                elif not event_url.startswith('http'):
                    event_url = self.source_url

                # Extract date from datetag element or from text
                date_text = None

                # Try to find the full date text like "Monday, November 17, 2025 7:00 PM"
                full_date_pattern = r'(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\s+\d{1,2}:\d{2}\s*(?:AM|PM)'
                full_date_match = re.search(full_date_pattern, full_text, re.IGNORECASE)

                if full_date_match:
                    date_text = full_date_match.group()
                else:
                    # Fallback: look for "Month DD" and time
                    date_pattern = r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{1,2}(?:,?\s+\d{4})?'
                    date_match = re.search(date_pattern, full_text, re.IGNORECASE)

                    time_pattern = r'\d{1,2}:\d{2}\s*(?:AM|PM|am|pm)'
                    time_match = re.search(time_pattern, full_text, re.IGNORECASE)

                    if date_match:
                        date_text = date_match.group()
                        if time_match:
                            date_text = f"{date_text} {time_match.group()}"

                if not date_text:
                    # Skip events without dates
                    continue

                # Parse the date
                try:
                    start_datetime = date_parser.parse(date_text, fuzzy=True)
                except:
                    # If parsing fails, skip this event
                    continue

                # Fetch real description from detail page
                description = self.fetch_event_description(event_url)

                # Fallback if description fetch failed
                if not description or len(description) < 20:
                    description = f"{title} - Live music at The Lily Pad in Inman Square, Cambridge"

                # Extract cost information
                cost = None
                cost_pattern = r'\$\d+(?:\s*/\s*\$\d+)?'
                cost_match = re.search(cost_pattern, full_text)
                if cost_match:
                    cost = cost_match.group()

                # The Lily Pad is located in Inman Square
                venue_name = "The Lily Pad"
                street_address = "1353 Cambridge St"
                city = "Cambridge"
                state = "MA"
                zip_code = "02139"

                # All Lily Pad events are music events
                category = EventCategory.MUSIC

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    source_url=event_url,
                    source_name=self.source_name,
                    venue_name=venue_name,
                    street_address=street_address,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    category=category,
                    cost=cost
                )
                events.append(event)

            except Exception as e:
                # Log error but continue processing other events
                continue

        return events
