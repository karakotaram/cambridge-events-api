"""Custom scraper for Porter Square Books events"""
import re
from datetime import datetime
from typing import List
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class PorterSquareBooksScraper(BaseScraper):
    """Custom scraper for Porter Square Books events"""

    def __init__(self):
        super().__init__(
            source_name="Porter Square Books",
            source_url="https://portersquarebooks.com/events/calendar",
            use_selenium=True  # JavaScript-rendered content
        )

    def fetch_event_description(self, event_url: str) -> str:
        """Fetch the full description from an event detail page"""
        try:
            if not self.driver:
                return ""

            # Navigate to event page
            full_url = event_url if event_url.startswith('http') else f"https://portersquarebooks.com{event_url}"
            self.driver.get(full_url)
            import time
            time.sleep(2)  # Wait for page load

            # Parse the page
            html = self.driver.page_source
            soup = self.parse_html(html)

            # Find description paragraphs in main content
            description_parts = []

            # Find main content area
            main_content = soup.find('main') or soup.find('article') or soup.find(class_='content')

            if main_content:
                # Get all paragraph tags
                paragraphs = main_content.find_all('p')
                for p in paragraphs:
                    text = self.clean_text(p.get_text())
                    # Filter out short navigation/footer text and common non-description content
                    if len(text) > 30 and not any(skip in text.lower() for skip in [
                        'view all events', 'buy the book', 'add to cart', 'ticket',
                        'online ticket sales', 'sign up', 'register'
                    ]):
                        description_parts.append(text)

            if description_parts:
                # Take first 4 paragraphs for a fuller description
                full_description = ' '.join(description_parts[:4])

                # Remove common addenda that aren't part of the main description
                import re

                # List of patterns that indicate the start of location/booking details to remove
                patterns_to_remove = [
                    r'this event (?:will )?takes? place at',
                    r'we offer validated parking',
                    r'\*please note that you will not receive',
                    r'please check your spam folder',
                ]

                # Find the earliest match among all patterns
                earliest_match = None
                for pattern in patterns_to_remove:
                    match = re.search(pattern, full_description, re.IGNORECASE)
                    if match:
                        if earliest_match is None or match.start() < earliest_match.start():
                            earliest_match = match

                if earliest_match:
                    full_description = full_description[:earliest_match.start()].strip()

                return full_description[:2000] if len(full_description) > 2000 else full_description

            return ""
        except Exception as e:
            return ""

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Porter Square Books"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        import time
        import logging

        logger = logging.getLogger(__name__)

        html = self.fetch_html(self.source_url)

        # Click list view button to show event articles
        if self.driver:
            try:
                time.sleep(3)  # Wait for initial page load

                # Click the list view button
                list_btn = self.driver.find_element(By.CSS_SELECTOR, 'a.events-views-nav__list')
                list_btn.click()

                # Wait for event-list articles to appear
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, "article.event-list"))
                )
                time.sleep(2)

            except Exception as e:
                logger.warning(f"Error switching to list view: {e}")

            html = self.driver.page_source

        soup = self.parse_html(html)

        events = []

        # Find all event articles
        event_articles = soup.find_all('article', class_='event-list')

        # Limit to reasonable number (30 upcoming events)
        event_articles = event_articles[:30]

        for article in event_articles:
            try:
                # Extract title
                title_elem = article.find('h3', class_='event-list__title')
                if not title_elem:
                    continue

                title_link = title_elem.find('a')
                if not title_link:
                    continue

                title = self.clean_text(title_link.get_text())
                if len(title) < 3:
                    continue

                # Extract event URL
                event_url = title_link.get('href', '')
                if event_url and not event_url.startswith('http'):
                    event_url = f"https://portersquarebooks.com{event_url}"

                # Extract date
                date_text_elem = article.find('div', class_='event-list__details--item')
                if not date_text_elem:
                    continue

                date_text = self.clean_text(date_text_elem.get_text())
                date_text = date_text.replace('Date:', '').strip()

                # Extract time
                time_elem = None
                for detail_item in article.find_all('div', class_='event-list__details--item'):
                    if 'Time:' in detail_item.get_text():
                        time_elem = detail_item
                        break

                time_text = ""
                if time_elem:
                    time_text = self.clean_text(time_elem.get_text())
                    time_text = time_text.replace('Time:', '').strip()

                # Combine date and time for parsing
                datetime_str = f"{date_text} {time_text.split('-')[0].strip()}"

                # Parse the datetime
                try:
                    start_datetime = date_parser.parse(datetime_str, fuzzy=True)
                except:
                    continue

                # Extract location
                location_elem = article.find('div', class_='event-details__location--location')
                if not location_elem:
                    location_elem = article.find('address')

                venue_name = "Porter Square Books"  # Default
                street_address = "25 White St"  # Default Cambridge location
                city = "Cambridge"
                state = "MA"
                zip_code = "02140"

                if location_elem:
                    address_text = self.clean_text(location_elem.get_text())

                    # Skip events not in Cambridge (Boston location, etc.)
                    if "360 Newbury" in address_text or ("Boston" in address_text and "Cambridge" not in address_text):
                        continue

                    # Check if it's a different Cambridge location
                    if "1815 Massachusetts Avenue" in address_text or "Cambridge Edition" in address_text:
                        venue_name = "Porter Square Books - Cambridge"
                        street_address = "1815 Massachusetts Avenue"
                        zip_code = "02140"
                    elif "25 White" in address_text:
                        venue_name = "Porter Square Books - Cambridge"
                        street_address = "25 White St"
                        city = "Cambridge"
                        zip_code = "02140"

                # Extract cost info (if available)
                cost = None
                body_text = article.get_text()
                if 'free' in body_text.lower():
                    cost = "Free"
                elif 'ticketed' in body_text.lower() or '$' in body_text:
                    # Try to extract dollar amount
                    cost_match = re.search(r'\$(\d+(?:\.\d{2})?)', body_text)
                    if cost_match:
                        cost = f"${cost_match.group(1)}"

                # Fetch description from detail page (list view has only short previews)
                description = ""
                if event_url:
                    # Always fetch from detail page for full descriptions
                    detail_desc = self.fetch_event_description(event_url)
                    if detail_desc and len(detail_desc) > 20:
                        description = detail_desc
                    else:
                        # Fallback to list view preview if detail fetch fails
                        body_elem = article.find('div', class_='event-list__body')
                        if body_elem:
                            description = self.clean_text(body_elem.get_text())

                # Final fallback
                if not description or len(description) < 20:
                    description = f"{title} at {venue_name}"

                # Categorize events
                category = self.categorize_event(title, description)

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    source_url=event_url or self.source_url,
                    source_name=self.source_name,
                    venue_name=venue_name[:200],
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

    def categorize_event(self, title: str, description: str) -> EventCategory:
        """Categorize event based on keywords"""
        text = f"{title} {description}".lower()

        # Check specific categories
        if any(word in text for word in ['trivia', 'quiz', 'jeopardy', 'bingo']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['concert', 'music', 'band', 'dj', 'live music', 'musical']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['book', 'author', 'reading', 'poetry', 'writer', 'novel', 'memoir', 'pencils up']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['comedy', 'stand-up', 'comedian']):
            return EventCategory.THEATER
        elif any(word in text for word in ['cooking', 'chef', 'food', 'recipe']):
            return EventCategory.FOOD_DRINK
        elif any(word in text for word in ['art', 'paint', 'craft', 'exhibit']):
            return EventCategory.ARTS_CULTURE
        else:
            return EventCategory.ARTS_CULTURE  # Default to arts & culture for book events
