"""Custom scraper for Harvard Book Store events"""
import logging
import re
from datetime import datetime
from typing import List
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class HarvardBookStoreScraper(BaseScraper):
    """Custom scraper for Harvard Book Store events"""

    def __init__(self):
        super().__init__(
            source_name="Harvard Book Store",
            source_url="https://www.harvard.com/events",
            use_selenium=False  # HTML is server-rendered, no need for Selenium
        )

    def get_browser_headers(self) -> dict:
        # harvard.com blocks the default Chrome UA (403) but accepts Safari.
        return {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }

    def fetch_event_description(self, event_url: str) -> str:
        """Fetch the full description from an event detail page"""
        import requests

        try:
            # Navigate to event page
            full_url = event_url if event_url.startswith('http') else f"https://www.harvard.com{event_url}"
            response = requests.get(full_url, timeout=30, headers=self.get_browser_headers())
            response.raise_for_status()

            # Parse the page
            soup = self.parse_html(response.text)

            # Find description paragraphs
            description_parts = []

            # Look for event description in common containers
            for selector in ['.field-name-field-event-description', '.event-description', '.field-type-text-with-summary']:
                desc_div = soup.find(class_=lambda c: c and selector.replace('.', '') in c if c else False)
                if desc_div:
                    paragraphs = desc_div.find_all('p')
                    for p in paragraphs:
                        text = self.clean_text(p.get_text())
                        if len(text) > 20:
                            description_parts.append(text)

            # If no specific description found, try general content areas
            if not description_parts:
                content_area = soup.find('div', class_='region-content')
                if content_area:
                    paragraphs = content_area.find_all('p')
                    for p in paragraphs:
                        text = self.clean_text(p.get_text())
                        # Filter out navigation/footer text
                        if len(text) > 30 and not any(skip in text.lower() for skip in ['view all events', 'buy the book', 'add to cart']):
                            description_parts.append(text)

            if description_parts:
                full_description = ' '.join(description_parts[:3])  # Take first 3 paragraphs
                return full_description[:2000] if len(full_description) > 2000 else full_description

            return ""
        except Exception as e:
            return ""

    def _fetch_page_items(self, page: int) -> list:
        """Fetch event item elements from a single page."""
        url = f"{self.source_url}?page={page}" if page > 0 else self.source_url
        html = self.fetch_html(url)
        soup = self.parse_html(html)
        view_content = soup.find(class_='view-content')
        if not view_content:
            return []
        return view_content.find_all('div', class_='views-row')

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Harvard Book Store (all pages)"""
        events = []

        # Fetch first page and detect pagination
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        # Determine total pages from pager
        max_page = 0
        pager = soup.find(class_='pager')
        if pager:
            last_li = pager.find('li', class_='pager-last')
            if last_li:
                last_link = last_li.find('a')
                if last_link:
                    href = last_link.get('href', '')
                    page_match = re.search(r'page=(\d+)', href)
                    if page_match:
                        max_page = int(page_match.group(1))

        # Collect event items from all pages
        event_items = []
        view_content = soup.find(class_='view-content')
        if view_content:
            event_items.extend(view_content.find_all('div', class_='views-row'))

        for page in range(1, max_page + 1):
            page_items = self._fetch_page_items(page)
            if not page_items:
                break
            event_items.extend(page_items)

        logger.info(f"Found {len(event_items)} event items across {max_page + 1} pages")

        for item in event_items:
            try:
                # Extract title
                title_elem = item.find('h2')
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
                    event_url = f"https://www.harvard.com{event_url}"

                # Extract date/time
                date_elem = item.find(class_='date-display-single')
                if not date_elem:
                    continue

                date_text = self.clean_text(date_elem.get_text())

                # Parse the datetime
                try:
                    start_datetime = date_parser.parse(date_text, fuzzy=True)
                except:
                    continue

                # Extract location
                location_elem = item.find(class_='location')
                venue_name = "Harvard Book Store"  # Default
                if location_elem:
                    location_text = self.clean_text(location_elem.get_text())
                    if location_text and location_text != "Harvard Book Store":
                        venue_name = location_text

                # Extract cost info
                cost = None
                cost_elem = item.find(class_='cost')
                if cost_elem:
                    cost_text = self.clean_text(cost_elem.get_text())
                    if 'free' in cost_text.lower():
                        cost = "Free"
                    elif 'ticketed' in cost_text.lower() or '$' in cost_text:
                        # Try to extract dollar amount
                        cost_match = re.search(r'\$(\d+(?:\.\d{2})?)', cost_text)
                        if cost_match:
                            cost = f"${cost_match.group(1)}"
                        else:
                            cost = "Ticketed"

                # Fetch description from detail page if available
                description = ""
                if event_url:
                    description = self.fetch_event_description(event_url)

                # Fallback description from subtitle
                if not description or len(description) < 20:
                    subtitle_elem = item.find(class_='subtitle')
                    if subtitle_elem:
                        description = self.clean_text(subtitle_elem.get_text())

                # Final fallback
                if not description or len(description) < 20:
                    description = f"{title} at {venue_name}"

                # Harvard Book Store location (default to Cambridge)
                street_address = "1256 Massachusetts Ave"
                city = "Cambridge"
                state = "MA"
                zip_code = "02138"

                # Check if event is at a different venue
                if "Brattle" in venue_name:
                    street_address = "40 Brattle St"
                    zip_code = "02138"

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

        # Check trivia first to ensure it takes priority
        if any(word in text for word in ['trivia', 'quiz', 'jeopardy', 'bingo']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['concert', 'music', 'band', 'dj', 'live music', 'musical']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['book', 'author', 'reading', 'poetry', 'writer', 'novel', 'memoir']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['comedy', 'stand-up', 'comedian']):
            return EventCategory.THEATER
        elif any(word in text for word in ['cooking', 'chef', 'food', 'recipe']):
            return EventCategory.FOOD_DRINK
        elif any(word in text for word in ['art', 'paint', 'craft', 'exhibit']):
            return EventCategory.ARTS_CULTURE
        else:
            return EventCategory.ARTS_CULTURE  # Default to arts & culture for book events
