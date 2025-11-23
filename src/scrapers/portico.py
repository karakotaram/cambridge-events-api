"""Custom scraper for Portico Brewing events"""
import re
from datetime import datetime
from typing import List
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class PorticoScraper(BaseScraper):
    """Custom scraper for Portico Brewing events"""

    def __init__(self):
        super().__init__(
            source_name="Portico Brewing",
            source_url="https://porticobrewing.com/upcoming-events",
            use_selenium=True  # JavaScript-rendered content
        )

    def fetch_event_details(self, event_url: str) -> tuple:
        """Fetch the full description and image from an event detail page
        Returns (description, image_url)
        """
        try:
            if not self.driver:
                return "", None

            # Navigate to event page
            full_url = event_url if event_url.startswith('http') else f"https://porticobrewing.com{event_url}"
            self.driver.get(full_url)
            import time
            time.sleep(2)  # Wait for page load

            # Parse the page
            html = self.driver.page_source
            soup = self.parse_html(html)

            # Extract image
            image_url = None

            # Try og:image first (Squarespace usually has good og:image)
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                image_url = og_image['content']

            # If no og:image, look for event image
            if not image_url:
                event_img = soup.find('img', class_=lambda x: x and 'event' in x.lower() if x else False)
                if event_img:
                    image_url = event_img.get('src') or event_img.get('data-src')

            # Normalize image URL
            if image_url and not image_url.startswith('http'):
                if image_url.startswith('//'):
                    image_url = f'https:{image_url}'
                else:
                    image_url = f"https://porticobrewing.com{image_url}"

            # Find description paragraphs
            description_parts = []

            # Look for paragraphs in content divs
            content_divs = soup.find_all(class_='sqs-html-content')
            for div in content_divs:
                paragraphs = div.find_all('p')
                for p in paragraphs:
                    text = self.clean_text(p.get_text())

                    # Skip very short text
                    if len(text) < 10:
                        continue

                    # Skip template text and metadata
                    text_lower = text.lower()
                    if any(skip_phrase in text_lower for skip_phrase in [
                        'view event',
                        'click here',
                        'learn more',
                        'buy tickets'
                    ]):
                        continue

                    # Skip if it's just price/time info
                    if len(text) < 50 and ('$' in text or 'AM' in text or 'PM' in text or ':' in text):
                        continue

                    # Only include substantial paragraphs
                    if len(text) > 20:
                        description_parts.append(text)

            description = ""
            if description_parts:
                full_description = ' '.join(description_parts)

                # Remove footer text that appears at the end
                footer_markers = [
                    '101 South St. Somerville',
                    '101 South St., Somerville',
                    'info@porticobrewing.com',
                    'Environmental Handprint',
                    'Event Inquiries'
                ]

                # Find the earliest footer marker and truncate there
                earliest_pos = len(full_description)
                for marker in footer_markers:
                    pos = full_description.find(marker)
                    if pos != -1 and pos < earliest_pos:
                        earliest_pos = pos

                if earliest_pos < len(full_description):
                    full_description = full_description[:earliest_pos].strip()

                description = full_description[:2000] if len(full_description) > 2000 else full_description

            return description, image_url
        except Exception as e:
            return "", None

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Portico Brewing"""
        html = self.fetch_html(self.source_url)

        # Add extra wait for JavaScript to load
        if self.driver:
            import time
            time.sleep(5)  # Give JS time to render events
            html = self.driver.page_source

        soup = self.parse_html(html)

        events = []

        # Find all event blocks (Squarespace uses .eventlist-event for event items)
        event_items = soup.find_all(class_='eventlist-event')

        # Limit to reasonable number
        event_items = event_items[:30]

        for item in event_items:
            try:
                # Extract title - can be h1, h2, or h3
                title_elem = item.find(class_='eventlist-title')
                if not title_elem:
                    continue

                title = self.clean_text(title_elem.get_text())
                if len(title) < 3:
                    continue

                # Skip private events
                full_text = self.clean_text(item.get_text()).lower()
                private_keywords = ['private party', 'private event', 'closed to public',
                                   'invite only', 'members only', 'by invitation']
                if any(keyword in full_text for keyword in private_keywords):
                    continue

                # Extract date
                date_elem = item.find(class_='event-date')
                if not date_elem:
                    continue

                # Build datetime string
                date_text = self.clean_text(date_elem.get_text())
                datetime_str = date_text

                # Extract time from the full text (format: "6:00 PM 8:00 PM" or "6:00 PM - 8:00 PM")
                time_match = re.search(r'(\d{1,2}:\d{2}\s*[AP]M)', full_text, re.IGNORECASE)
                if time_match:
                    start_time = time_match.group(1)
                    datetime_str = f"{date_text} {start_time}"

                # Parse the datetime
                try:
                    start_datetime = date_parser.parse(datetime_str, fuzzy=True)
                except:
                    continue

                # Extract event URL
                event_url = ""
                link = item.find('a', href=True)
                if link:
                    event_url = link['href']

                # Fetch description and image from detail page if available
                description = ""
                image_url = None
                if event_url:
                    description, image_url = self.fetch_event_details(event_url)

                # Fallback description from list page
                if not description or len(description) < 20:
                    desc_elem = item.find(class_='eventlist-description')
                    if desc_elem:
                        description = self.clean_text(desc_elem.get_text())
                        # Remove "View Event →" text
                        description = re.sub(r'View Event\s*→', '', description)

                # Final fallback
                if not description or len(description) < 20:
                    description = f"{title} at Portico Brewing in Somerville, MA"

                # Extract cost if available (but skip for trivia/prize events)
                cost = None
                text_lower = f"{title} {description}".lower()
                is_trivia = any(word in text_lower for word in ['trivia', 'quiz', 'jeopardy', 'bingo'])

                # Only extract cost if it's not a trivia event (where $ amounts are prizes)
                if not is_trivia:
                    cost_match = re.search(r'\$\d+(?:\.\d{2})?', full_text)
                    if cost_match:
                        cost = cost_match.group()

                # Portico Brewing location
                venue_name = "Portico Brewing"
                street_address = "101 South St"
                city = "Somerville"
                state = "MA"
                zip_code = "02143"

                # Build source URL
                if event_url:
                    if event_url.startswith('/'):
                        event_url = f"https://porticobrewing.com{event_url}"
                else:
                    event_url = self.source_url

                # Categorize events
                category = self.categorize_event(title, description)

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    source_url=event_url,
                    source_name=self.source_name,
                    venue_name=venue_name[:200],
                    street_address=street_address,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    category=category,
                    cost=cost,
                    image_url=image_url
                )
                events.append(event)

            except Exception as e:
                # Log error but continue processing other events
                continue

        return events

    def categorize_event(self, title: str, description: str) -> EventCategory:
        """Categorize event based on keywords"""
        text = f"{title} {description}".lower()

        # Check trivia first to ensure it takes priority
        if any(word in text for word in ['trivia', 'quiz', 'jeopardy', 'bingo']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['concert', 'music', 'band', 'dj', 'live music']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['yoga', 'fitness', 'workout']):
            return EventCategory.SPORTS
        elif any(word in text for word in ['comedy', 'stand-up', 'comedian']):
            return EventCategory.THEATER
        elif any(word in text for word in ['cooking', 'chef', 'food', 'tasting', 'dinner']):
            return EventCategory.FOOD_DRINK
        elif any(word in text for word in ['art', 'paint', 'craft']):
            return EventCategory.ARTS_CULTURE
        else:
            return EventCategory.COMMUNITY
