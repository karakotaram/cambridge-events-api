"""Custom scraper for Lamplighter Brewing events"""
import re
from datetime import datetime
from typing import List
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class LamplighterScraper(BaseScraper):
    """Custom scraper for Lamplighter Brewing events"""

    def __init__(self):
        super().__init__(
            source_name="Lamplighter Brewing",
            source_url="https://lamplighterbrewing.com/collections/events",
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
            full_url = event_url if event_url.startswith('http') else f"https://lamplighterbrewing.com{event_url}"
            self.driver.get(full_url)
            import time
            time.sleep(2)  # Wait for page load

            # Parse the page
            html = self.driver.page_source
            soup = self.parse_html(html)

            # Extract image - look for product/event image
            image_url = None

            # Try og:image first
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                image_url = og_image['content']

            # If no og:image, look for product image
            if not image_url:
                product_img = soup.find('img', class_=lambda x: x and 'product' in x.lower() if x else False)
                if product_img:
                    image_url = product_img.get('src') or product_img.get('data-src')

            # Normalize image URL
            if image_url and not image_url.startswith('http'):
                if image_url.startswith('//'):
                    image_url = f'https:{image_url}'
                else:
                    image_url = f"https://lamplighterbrewing.com{image_url}"

            # Find description paragraphs in the RTE (Rich Text Editor) div
            description_parts = []

            # Look for the RTE container which holds the description
            rte_div = soup.find('div', class_='rte')
            if rte_div:
                paragraphs = rte_div.find_all('p')
                for p in paragraphs:
                    text = self.clean_text(p.get_text())

                    # Skip very short text
                    if len(text) < 10:
                        continue

                    # Skip ticket/purchase links
                    if any(skip in text.lower() for skip in ['get tickets here', 'buy tickets', 'click here']):
                        continue

                    # Skip if it looks like metadata
                    if any(skip in text.lower() for skip in ['add to cart', 'sold out', 'quantity', 'select options']):
                        continue

                    description_parts.append(text)

            description = ""
            if description_parts:
                full_description = ' '.join(description_parts)
                description = full_description[:2000] if len(full_description) > 2000 else full_description

            return description, image_url
        except Exception as e:
            return "", None

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Lamplighter Brewing"""
        html = self.fetch_html(self.source_url)

        # Add extra wait for JavaScript to load
        if self.driver:
            import time
            time.sleep(5)  # Give JS time to render events
            html = self.driver.page_source

        soup = self.parse_html(html)

        events = []

        # Find all links to product pages (events)
        event_links = soup.find_all('a', href=re.compile(r'/products/'))

        # Filter out gift cards and other non-event products
        event_links = [link for link in event_links if 'gift' not in link.get('href', '').lower()]

        # Limit to reasonable number
        event_links = event_links[:30]

        for link in event_links:
            try:
                # Get the full link text which contains title, date, time, location
                full_text = self.clean_text(link.get_text())

                if len(full_text) < 10:
                    continue

                # Extract event URL
                event_url = link.get('href', '')
                if not event_url:
                    continue

                # Parse the combined text: "Event NameDate, YearTime - TimeLamplighter CX"
                # Example: "Jeopardy Bar LeagueNovember 17, 20257 pm - 9 pmLamplighter CX"

                # Look for month names
                months = ['January', 'February', 'March', 'April', 'May', 'June',
                         'July', 'August', 'September', 'October', 'November', 'December']

                month_pattern = '|'.join(months)
                date_match = re.search(f'({month_pattern})\\s+(\\d{{1,2}}),?\\s+(\\d{{4}})', full_text)

                if not date_match:
                    continue

                date_str = date_match.group()  # e.g., "November 17, 2025"

                # Extract title (everything before the date)
                title_end_pos = date_match.start()
                title = full_text[:title_end_pos].strip()

                if len(title) < 3:
                    continue

                # Skip private events
                if any(keyword in title.lower() or keyword in full_text.lower() for keyword in
                       ['private party', 'private event', 'closed to public', 'invite only']):
                    continue

                # Extract time (after date, before location)
                remaining_text = full_text[date_match.end():]
                time_pattern = r'(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s*-\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm))'
                time_match = re.search(time_pattern, remaining_text, re.IGNORECASE)

                if time_match:
                    time_str = time_match.group()
                    # Use start time for datetime
                    start_time = time_match.group(1)
                    datetime_str = f"{date_str} {start_time}"
                else:
                    datetime_str = date_str

                # Parse the datetime
                try:
                    start_datetime = date_parser.parse(datetime_str, fuzzy=True)
                except:
                    continue

                # Extract location (after time or date)
                venue_name = "Lamplighter Brewing"
                if time_match:
                    location_text = remaining_text[time_match.end():].strip()
                else:
                    location_text = remaining_text.strip()

                # Lamplighter has two locations:
                # - Lamplighter CX: 110 N First St, Cambridge, MA 02141
                # - Lamplighter Brewing (Broadway): 284 Broadway, Cambridge, MA 02139
                city = "Cambridge"
                state = "MA"

                if location_text:
                    location_lower = location_text.lower()
                    # Check for CX location indicators
                    if 'cx' in location_lower or 'cambridge crossing' in location_lower or '110' in location_text or 'first st' in location_lower:
                        venue_name = "Lamplighter CX"
                        street_address = "110 N First St"
                        zip_code = "02141"
                    # Check for Broadway location indicators
                    elif '284' in location_text or 'broadway' in location_lower:
                        venue_name = "Lamplighter Brewing"
                        street_address = "284 Broadway"
                        zip_code = "02139"
                    else:
                        # Default to CX location (newer, more common for events)
                        venue_name = "Lamplighter CX"
                        street_address = "110 N First St"
                        zip_code = "02141"
                else:
                    # Default to CX location
                    venue_name = "Lamplighter CX"
                    street_address = "110 N First St"
                    zip_code = "02141"

                # Fetch description and image from detail page
                description, image_url = self.fetch_event_details(event_url)

                # Fallback if description fetch failed
                if not description or len(description) < 20:
                    description = f"{title} at {venue_name} in Cambridge, MA"

                # Extract cost if available
                cost = None
                cost_match = re.search(r'\$\d+(?:\.\d{2})?', full_text)
                if cost_match:
                    cost = cost_match.group()

                # Categorize events
                category = self.categorize_event(title, description)

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    source_url=f"https://lamplighterbrewing.com{event_url}" if event_url.startswith('/') else event_url,
                    source_name=self.source_name,
                    venue_name=venue_name[:200],
                    street_address=street_address,
                    city=city,
                    state="MA",
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
