"""Custom scraper for Sanders Theatre events at Harvard"""
import logging
import json
import re
from datetime import datetime
from typing import List, Optional
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

# Curated event descriptions - Localist truncates descriptions on listing pages
EVENT_DESCRIPTIONS = {
    "norton lectures": "The Charles Eliot Norton Lectures are Harvard's most prestigious annual lecture series in poetry, broadly defined to include music, drama, and visual arts. Past lecturers have included T.S. Eliot, Igor Stravinsky, Leonard Bernstein, and Jorge Luis Borges.",
    "steve mcqueen": "Oscar-winning filmmaker and visual artist Steve McQueen presents his Norton Lecture series 'Pulse', exploring the most basic structural elements of film—light and sound—through immersive installations and screenings.",
    "small axe": "Part of the Norton Lectures with Steve McQueen, 'Bass' features an immersive installation comprised of the most basic structural elements of film—light and sound. The event includes a special performance by Grammy-winning artist Meshell Ndegeocello.",
    "tina fey": "Emmy Award-winning writers Robert Carlock '95 and Tina Fey discuss their legendary creative partnership in 'Creative Mischief and the Art of Being Funny Together'. The duo has collaborated on acclaimed series including 30 Rock, Unbreakable Kimmy Schmidt, and Girls5eva.",
    "robert carlock": "Emmy Award-winning writers Robert Carlock '95 and Tina Fey discuss their legendary creative partnership in 'Creative Mischief and the Art of Being Funny Together'. The duo has collaborated on acclaimed series including 30 Rock, Unbreakable Kimmy Schmidt, and Girls5eva.",
    "free speech": "A timely conversation examining how colleges navigate free speech challenges. Moderated by distinguished scholars, this discussion explores the balance between open discourse and inclusive campus environments.",
    "choral concert": "Harvard's renowned choral ensembles perform in the historic Sanders Theatre, known for its exceptional acoustics. The concert showcases the rich tradition of choral music at Harvard.",
    "windborne": "A special collaboration featuring Harvard's choral ensembles and the acclaimed folk group Windborne, blending classical choral traditions with American folk music in Sanders Theatre's exceptional acoustic space.",
    "montage concert": "A joint concert featuring the Harvard Wind Ensemble and University Band, showcasing the diverse talents of Harvard's instrumental musicians in the acoustically renowned Sanders Theatre.",
    "music 110": "The Harvard-Radcliffe Orchestra presents works from the Music 110 curriculum, featuring masterworks by renowned composers performed in the historic Sanders Theatre.",
    "mahler": "A performance of Gustav Mahler's beloved Rückert-Lieder and Symphony No. 4, showcasing the emotional depth and orchestral brilliance of the late Romantic period.",
}


class SandersTheatreScraper(BaseScraper):
    """Custom scraper for Sanders Theatre events"""

    def __init__(self):
        super().__init__(
            source_name="Sanders Theatre",
            source_url="https://calendar.college.harvard.edu/sanders_theatre",
            use_selenium=True  # Page uses JavaScript to load events
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Sanders Theatre calendar"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        import time

        html = self.fetch_html(self.source_url)

        if self.driver:
            try:
                # Wait for page content to load
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.TAG_NAME, "script"))
                )
                time.sleep(3)
                html = self.driver.page_source
            except Exception as e:
                logger.warning(f"Timeout waiting for page: {e}")

        soup = self.parse_html(html)
        events = []
        seen_events = set()

        # Look for JSON-LD schema data
        json_ld_scripts = soup.find_all('script', type='application/ld+json')
        logger.info(f"Found {len(json_ld_scripts)} JSON-LD scripts")

        for script in json_ld_scripts:
            try:
                data = json.loads(script.string)

                # Handle single event or array of events
                if isinstance(data, list):
                    for item in data:
                        if item.get('@type') == 'Event':
                            event = self._parse_json_ld_event(item, seen_events)
                            if event:
                                events.append(event)
                elif isinstance(data, dict):
                    if data.get('@type') == 'Event':
                        event = self._parse_json_ld_event(data, seen_events)
                        if event:
                            events.append(event)
                    elif '@graph' in data:
                        for item in data['@graph']:
                            if item.get('@type') == 'Event':
                                event = self._parse_json_ld_event(item, seen_events)
                                if event:
                                    events.append(event)

            except json.JSONDecodeError as e:
                logger.warning(f"Failed to parse JSON-LD: {e}")
                continue

        # Also try to parse event cards from HTML
        event_cards = soup.find_all(['div', 'article'], class_=lambda x: x and ('event' in x.lower() or 'card' in x.lower()) if x else False)
        logger.info(f"Found {len(event_cards)} event card elements")

        for card in event_cards:
            try:
                event = self._parse_event_card(card, seen_events)
                if event:
                    events.append(event)
            except Exception as e:
                logger.warning(f"Error parsing event card: {e}")
                continue

        return events

    def _parse_json_ld_event(self, data: dict, seen_events: set) -> EventCreate:
        """Parse a JSON-LD Event object"""
        try:
            title = data.get('name', '')
            if not title or len(title) < 3:
                return None

            # Parse start date
            start_date_str = data.get('startDate', '')
            if not start_date_str:
                return None

            try:
                start_datetime = date_parser.parse(start_date_str)
            except Exception as e:
                logger.warning(f"Failed to parse date '{start_date_str}': {e}")
                return None

            # Create unique key
            event_key = f"{title}_{start_datetime.date()}"
            if event_key in seen_events:
                return None
            seen_events.add(event_key)

            # Get description - check curated descriptions first
            description = None
            title_lower = title.lower()
            for key, desc in EVENT_DESCRIPTIONS.items():
                if key in title_lower:
                    description = desc
                    break

            # Fallback to scraped description if no curated match
            if not description:
                description = data.get('description', '') or f"{title} at Sanders Theatre"
                description = self.clean_text(description)

            # Get URL
            event_url = data.get('url', self.source_url)

            # Get image
            image_url = None
            image_data = data.get('image')
            if isinstance(image_data, str):
                image_url = image_data
            elif isinstance(image_data, dict):
                image_url = image_data.get('url')
            elif isinstance(image_data, list) and image_data:
                image_url = image_data[0] if isinstance(image_data[0], str) else image_data[0].get('url')

            # Get price/cost
            cost = None
            offers = data.get('offers')
            if offers:
                if isinstance(offers, dict):
                    price = offers.get('price')
                    if price:
                        cost = f"${price}" if not str(price).startswith('$') else str(price)
                elif isinstance(offers, list) and offers:
                    price = offers[0].get('price')
                    if price:
                        cost = f"${price}" if not str(price).startswith('$') else str(price)

            # Get location details
            venue_name = "Sanders Theatre"
            location = data.get('location')
            if isinstance(location, dict):
                loc_name = location.get('name', '')
                if loc_name and 'Sanders' not in loc_name:
                    venue_name = f"Sanders Theatre - {loc_name}"

            category = self.categorize_event(title, description)

            return EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start_datetime,
                source_url=event_url,
                source_name=self.source_name,
                venue_name=venue_name[:200],
                street_address="45 Quincy Street",
                city="Cambridge",
                state="MA",
                zip_code="02138",
                category=category,
                cost=cost,
                image_url=image_url
            )

        except Exception as e:
            logger.warning(f"Error parsing JSON-LD event: {e}")
            return None

    def _parse_event_card(self, card, seen_events: set) -> EventCreate:
        """Parse an event from an HTML card element"""
        try:
            # Get title - look for various title patterns
            title_elem = card.find(['h2', 'h3', 'h4', 'a'], class_=lambda x: x and ('title' in x.lower() or 'name' in x.lower()) if x else False)
            if not title_elem:
                title_elem = card.find(['h2', 'h3', 'h4'])
            if not title_elem:
                title_elem = card.find('a', class_='em-card_title')

            if not title_elem:
                return None

            title = self.clean_text(title_elem.get_text())
            if not title or len(title) < 3:
                return None

            # Get URL
            event_url = self.source_url
            link = title_elem if title_elem.name == 'a' else card.find('a', href=True)
            if link and link.get('href'):
                event_url = link.get('href')
                if not event_url.startswith('http'):
                    event_url = f"https://calendar.college.harvard.edu{event_url}"

            # Get date/time - check multiple patterns
            date_elem = card.find(['time', 'span', 'div'], class_=lambda x: x and ('date' in x.lower() or 'time' in x.lower()) if x else False)
            date_str = None

            if date_elem:
                date_str = date_elem.get('datetime') or date_elem.get_text()

            if not date_str:
                # Try to find date in any text
                card_text = card.get_text()
                # Try full date with year
                date_match = re.search(
                    r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s*\d{4}',
                    card_text,
                    re.IGNORECASE
                )
                # Try date without year
                if not date_match:
                    date_match = re.search(
                        r'(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s*(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}',
                        card_text,
                        re.IGNORECASE
                    )
                if date_match:
                    date_str = date_match.group()

            if not date_str:
                return None

            try:
                start_datetime = date_parser.parse(date_str, fuzzy=True)
                # Ensure year is correct
                if start_datetime.year < datetime.now().year:
                    start_datetime = start_datetime.replace(year=datetime.now().year)
                    if start_datetime < datetime.now():
                        start_datetime = start_datetime.replace(year=datetime.now().year + 1)
            except Exception as e:
                return None

            # Skip past events
            if start_datetime < datetime.now():
                return None

            # Check for duplicates
            event_key = f"{title}_{start_datetime.date()}"
            if event_key in seen_events:
                return None
            seen_events.add(event_key)

            # Get description - check curated descriptions first
            description = None
            title_lower = title.lower()
            for key, desc in EVENT_DESCRIPTIONS.items():
                if key in title_lower:
                    description = desc
                    break

            # Fallback to scraped description
            if not description:
                desc_elem = card.find(['p', 'div'], class_=lambda x: x and ('desc' in x.lower() or 'summary' in x.lower() or 'excerpt' in x.lower()) if x else False)
                if desc_elem:
                    description = self.clean_text(desc_elem.get_text())

            # Also check for em-card_description class (Localist)
            if not description:
                desc_elem = card.find(class_='em-card_description')
                if desc_elem:
                    description = self.clean_text(desc_elem.get_text())

            if not description:
                description = f"{title} at Sanders Theatre"

            # Get image - check multiple sources
            image_url = None
            img = card.find('img')
            if img:
                # Check various image attributes
                image_url = img.get('src') or img.get('data-src') or img.get('data-lazy-src')

            # Also check for background image in style
            if not image_url:
                style_elem = card.find(style=lambda x: x and 'background-image' in x if x else False)
                if style_elem:
                    style = style_elem.get('style', '')
                    url_match = re.search(r'url\(["\']?([^"\']+)["\']?\)', style)
                    if url_match:
                        image_url = url_match.group(1)

            # Clean up image URL
            if image_url:
                if image_url.startswith('//'):
                    image_url = f"https:{image_url}"
                elif not image_url.startswith('http'):
                    image_url = f"https://calendar.college.harvard.edu{image_url}"
                # Skip placeholder images
                if 'data:image' in image_url or 'placeholder' in image_url.lower():
                    image_url = None

            category = self.categorize_event(title, description)

            return EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start_datetime,
                source_url=event_url,
                source_name=self.source_name,
                venue_name="Sanders Theatre",
                street_address="45 Quincy Street",
                city="Cambridge",
                state="MA",
                zip_code="02138",
                category=category,
                image_url=image_url
            )

        except Exception as e:
            logger.warning(f"Error parsing event card: {e}")
            return None

    def categorize_event(self, title: str, description: str) -> EventCategory:
        """Categorize event based on keywords"""
        text = f"{title} {description}".lower()

        if any(word in text for word in ['concert', 'music', 'orchestra', 'symphony', 'choir', 'choral', 'jazz', 'band']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['lecture', 'talk', 'speaker', 'discussion', 'forum', 'symposium']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['theater', 'theatre', 'play', 'performance', 'dance', 'ballet']):
            return EventCategory.THEATER
        elif any(word in text for word in ['ceremony', 'graduation', 'commencement']):
            return EventCategory.COMMUNITY
        else:
            return EventCategory.ARTS_CULTURE
