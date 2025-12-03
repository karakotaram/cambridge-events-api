"""Custom scraper for Aeronaut Brewing events"""
import logging
import re
import hashlib
from datetime import datetime
from typing import List, Optional
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class AeronautScraper(BaseScraper):
    """Custom scraper for Aeronaut Brewing events"""

    def __init__(self):
        super().__init__(
            source_name="Aeronaut Brewing",
            source_url="https://www.aeronautbrewing.com/events/",
            use_selenium=True  # Site requires JavaScript and has Cloudflare
        )

    def setup_selenium(self):
        """Override to add anti-detection options for Cloudflare"""
        if self.driver is None:
            from selenium.webdriver.chrome.options import Options
            from selenium import webdriver

            options = Options()
            options.add_argument('--headless=new')
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--disable-blink-features=AutomationControlled')
            options.add_argument('--user-agent=Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')
            options.add_experimental_option('excludeSwitches', ['enable-automation'])
            options.add_experimental_option('useAutomationExtension', False)

            self.driver = webdriver.Chrome(options=options)

            # Hide webdriver property
            self.driver.execute_cdp_cmd('Page.addScriptToEvaluateOnNewDocument', {
                'source': "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            })

            logger.info(f"Selenium WebDriver initialized for {self.source_name}")

    def generate_event_id(self, title: str, date_str: str) -> str:
        """Generate a consistent event ID based on title and date"""
        unique_string = f"aeronaut-{title}-{date_str}".lower()
        return hashlib.md5(unique_string.encode()).hexdigest()[:16]

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Aeronaut Brewing"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        import time

        html = self.fetch_html(self.source_url)

        # Wait for event elements to load
        if self.driver:
            try:
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.CLASS_NAME, "single-event-details"))
                )
                time.sleep(3)  # Additional wait for all content
            except Exception as e:
                logger.warning(f"Timeout waiting for events to load: {e}")
            html = self.driver.page_source

        soup = self.parse_html(html)
        events = []
        seen_ids = set()

        # Find all event containers
        event_containers = soup.find_all('div', class_=lambda c: c and 'single-event-details' in c if c else False)
        logger.info(f"Found {len(event_containers)} event containers")

        for container in event_containers:
            try:
                # Skip closed/closure events
                container_classes = container.get('class', [])
                if 'closed' in container_classes:
                    continue

                # Extract title
                title_elem = container.find('h3', class_='event-title')
                if not title_elem:
                    continue
                title = self.clean_text(title_elem.get_text())
                if len(title) < 3:
                    continue

                # Extract date/time
                datetime_elem = container.find('div', class_='event-datetime')
                if not datetime_elem:
                    continue
                datetime_text = self.clean_text(datetime_elem.get_text())

                # Parse date - format: "Wed, December 3 at 7PM" or "Sun, December 7 at 1PM"
                # Remove location suffix like "SomervilleBrewery"
                datetime_text = re.sub(r'(Somerville|Allston|Brewery|Cannery)+$', '', datetime_text).strip()

                try:
                    # Add current year if not present
                    if not re.search(r'\d{4}', datetime_text):
                        current_year = datetime.now().year
                        # Check if the date has passed this year
                        datetime_text_with_year = f"{datetime_text} {current_year}"
                        parsed_date = date_parser.parse(datetime_text_with_year, fuzzy=True)
                        # If date is in the past, assume next year
                        if parsed_date < datetime.now():
                            datetime_text_with_year = f"{datetime_text} {current_year + 1}"
                            parsed_date = date_parser.parse(datetime_text_with_year, fuzzy=True)
                        start_datetime = parsed_date
                    else:
                        start_datetime = date_parser.parse(datetime_text, fuzzy=True)
                except Exception as e:
                    logger.warning(f"Failed to parse date '{datetime_text}': {e}")
                    continue

                # Generate event ID
                event_id = self.generate_event_id(title, start_datetime.strftime('%Y-%m-%d'))
                if event_id in seen_ids:
                    continue
                seen_ids.add(event_id)

                # Extract description
                desc_elem = container.find('div', class_='event-description')
                description = self.clean_text(desc_elem.get_text()) if desc_elem else ""

                # Extract image URL from background-image style
                image_url = None
                image_wrap = container.find('div', class_='image-wrap')
                if image_wrap:
                    style = image_wrap.get('style', '')
                    match = re.search(r"url\(['\"]?([^'\"]+)['\"]?\)", style)
                    if match:
                        image_url = match.group(1)

                # Extract event URL (prefer external links)
                event_url = self.source_url
                links_div = container.find('div', class_='links')
                if links_div:
                    # Look for "More info" or "Tickets" link
                    for link in links_div.find_all('a', href=True):
                        href = link.get('href', '')
                        if href and href.startswith('http'):
                            event_url = href
                            break

                # Determine event type from classes
                event_type = "community"  # default
                for cls in ['ticketed', 'meetup', 'community', 'music', 'trivia']:
                    if cls in container_classes:
                        event_type = cls
                        break

                # Check if ticketed
                ticketed_elem = container.find('span', class_='ticketed-event')
                is_ticketed = ticketed_elem and ticketed_elem.get_text().strip()

                # Set venue based on location in datetime text
                venue_name = "Aeronaut Brewing Co."
                street_address = "14 Tyler St"
                city = "Somerville"
                zip_code = "02143"

                # Check for Allston location
                full_text = datetime_elem.get_text().lower() if datetime_elem else ""
                if 'allston' in full_text or 'cannery' in full_text:
                    venue_name = "Aeronaut Cannery"
                    street_address = "199 Rantoul St"
                    city = "Beverly"
                    zip_code = "01915"

                # Categorize event
                category = self.categorize_event(title, description, event_type)

                # Build description if too short
                if not description or len(description) < 20:
                    description = f"{title} at {venue_name}"
                    if is_ticketed:
                        description += " (ticketed event)"

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    source_url=event_url,
                    source_name=self.source_name,
                    venue_name=venue_name,
                    street_address=street_address,
                    city=city,
                    state="MA",
                    zip_code=zip_code,
                    category=category,
                    image_url=image_url
                )
                events.append(event)

            except Exception as e:
                logger.warning(f"Failed to parse event: {e}")
                continue

        return events

    def categorize_event(self, title: str, description: str, event_type: str) -> EventCategory:
        """Categorize event based on keywords and type"""
        text = f"{title} {description}".lower()

        # Check event type first
        if event_type == 'music':
            return EventCategory.MUSIC

        # Check keywords
        if any(word in text for word in ['trivia', 'quiz', 'jeopardy', 'bingo']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['concert', 'music', 'band', 'dj', 'live music', 'jazz', 'open mic']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['comedy', 'stand-up', 'comedian', 'drag show', 'drag night']):
            return EventCategory.THEATER
        elif any(word in text for word in ['game', 'd&d', 'dungeons', 'mahjong', 'board game', 'dragons']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['yoga', 'fitness', 'run', 'workout']):
            return EventCategory.SPORTS
        elif any(word in text for word in ['market', 'craft', 'fair', 'vendor']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['astronomy', 'science', 'talk', 'lecture', 'museum']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['tasting', 'beer', 'food', 'dinner']):
            return EventCategory.FOOD_DRINK
        else:
            return EventCategory.COMMUNITY
