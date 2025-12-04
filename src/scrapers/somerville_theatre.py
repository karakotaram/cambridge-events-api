"""Custom scraper for Somerville Theatre events"""
import logging
import re
import cloudscraper
from datetime import datetime
from typing import List, Optional
from dateutil import parser as date_parser
from bs4 import BeautifulSoup

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

# Curated event descriptions
EVENT_DESCRIPTIONS = {
    "slutcracker": "The Slutcracker is Boston's naughtiest holiday tradition! This burlesque retelling of The Nutcracker features dazzling costumes, provocative performances, and holiday cheer for adults. A beloved annual tradition at Somerville Theatre.",
    "altan": "Irish traditional music supergroup Altan brings their legendary sound to Somerville Theatre. Known for their masterful interpretation of traditional Irish music with Mairéad Ní Mhaonaigh's stunning fiddle work and vocals.",
    "lunasa": "Lúnasa is widely regarded as the finest traditional Irish instrumental band of recent times. Their music combines virtuoso musicianship with genre-bending creativity.",
    "natalie macmaster": "Celtic fiddle virtuoso Natalie MacMaster and her husband Donnell Leahy bring their electrifying family performance to the stage. A night of world-class Cape Breton fiddling and step dancing.",
    "asi wind": "Master magician Asi Wind presents an intimate evening of mind-bending magic. Known for his innovative card magic and mentalism, Asi Wind creates unforgettable experiences.",
    "patrick watson": "Canadian singer-songwriter Patrick Watson brings his ethereal voice and genre-defying sound. Known for haunting melodies and inventive arrangements that blend pop, classical, and experimental music.",
    "haley heynderickx": "Portland-based singer-songwriter Haley Heynderickx performs her intimate, folk-inflected songs. Her warm voice and intricate guitar work create a captivating live experience.",
    "the church": "Australian rock legends The Church perform their classic hits and new material. Known for their jangly guitars and atmospheric sound, The Church remains one of the most influential bands of the 1980s.",
}


class SomervilleTheatreScraper(BaseScraper):
    """Custom scraper for Somerville Theatre events using requests"""

    def __init__(self):
        super().__init__(
            source_name="Somerville Theatre",
            source_url="https://www.somervilletheatre.com/events/",
            use_selenium=False  # Using cloudscraper instead
        )
        # Use cloudscraper to bypass Cloudflare protection
        self.scraper = cloudscraper.create_scraper()

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Somerville Theatre"""
        events = []
        seen_events = set()

        try:
            # Fetch page with cloudscraper (bypasses Cloudflare protection)
            response = self.scraper.get(self.source_url, timeout=30)
            response.raise_for_status()
            html = response.text
            logger.info(f"Fetched page with cloudscraper, status {response.status_code}, {len(html)} chars")
        except Exception as e:
            logger.error(f"Failed to fetch page: {e}")
            return events

        soup = BeautifulSoup(html, 'html.parser')

        # Find all event containers - wp_theatre_event class
        event_divs = soup.find_all('div', class_='wp_theatre_event')
        logger.info(f"Found {len(event_divs)} wp_theatre_event elements")

        # Also look for event list items or article tags
        if not event_divs:
            event_divs = soup.find_all(['article', 'div'], class_=lambda x: x and ('event' in x.lower() or 'production' in x.lower()) if x else False)
            logger.info(f"Found {len(event_divs)} alternative event elements")

        for div in event_divs:
            try:
                event = self._parse_event_div(div, seen_events)
                if event:
                    events.append(event)
            except Exception as e:
                logger.warning(f"Error parsing event: {e}")
                continue

        return events

    def _parse_event_div(self, div, seen_events: set) -> Optional[EventCreate]:
        """Parse a single event div"""
        # Get title - look for title class or heading tags
        title_elem = div.find(class_='wp_theatre_event_title')
        if not title_elem:
            title_elem = div.find(['h2', 'h3', 'h4', 'strong'])
        if not title_elem:
            # Get first link text
            link = div.find('a')
            if link:
                title_elem = link

        if not title_elem:
            return None

        title = self.clean_text(title_elem.get_text())
        if not title or len(title) < 3:
            return None

        # Get event URL
        event_url = self.source_url
        link = div.find('a', href=True)
        if link:
            href = link.get('href', '')
            if href and 'ticketmaster' not in href.lower() and 'ticket' not in href.lower():
                event_url = href if href.startswith('http') else f"https://www.somervilletheatre.com{href}"

        # Parse date/time from the dedicated datetime div
        datetime_elem = div.find(class_='wp_theatre_event_datetime')

        if datetime_elem:
            datetime_text = datetime_elem.get_text()
        else:
            datetime_text = div.get_text()

        # The site concatenates date and time like "December 5, 20258:00 pm"
        # So we need a combined pattern that handles this
        combined_match = re.search(
            r'((?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s*\d{4})(\d{1,2}:\d{2}\s*(?:am|pm))',
            datetime_text,
            re.IGNORECASE
        )

        if combined_match:
            date_str = combined_match.group(1).strip()
            time_str = combined_match.group(2).strip()
        else:
            # Fallback: try separate patterns
            date_match = re.search(
                r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s*\d{4}',
                datetime_text,
                re.IGNORECASE
            )

            if not date_match:
                return None

            date_str = date_match.group()

            # Extract time after the date
            remaining_text = datetime_text[date_match.end():]
            time_match = re.search(r'(\d{1,2}:\d{2}\s*(?:am|pm))', remaining_text, re.IGNORECASE)
            time_str = time_match.group(1) if time_match else "8:00 pm"

        # Parse datetime
        try:
            datetime_str = f"{date_str} {time_str}"
            start_datetime = date_parser.parse(datetime_str, fuzzy=True)
        except Exception as e:
            logger.warning(f"Failed to parse date '{datetime_str}': {e}")
            return None

        # Skip past events
        if start_datetime < datetime.now():
            return None

        # Create unique key to avoid duplicates
        event_key = f"{title}_{start_datetime.date()}_{start_datetime.hour}"
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

        if not description:
            description = f"{title} live at Somerville Theatre in Davis Square."

        # Get image URL - site uses lazy loading with data URI placeholders
        # Find the real image URL (starts with https://)
        image_url = None
        all_imgs = div.find_all('img')
        for img in all_imgs:
            src = img.get('src', '')
            # Skip data URI placeholders
            if src.startswith('https://') and 'somervilletheatre.com' in src:
                image_url = src
                break
            # Also check data-lazy-src and data-src
            lazy_src = img.get('data-lazy-src') or img.get('data-src')
            if lazy_src and lazy_src.startswith('https://'):
                image_url = lazy_src
                break

        # Also look for background images
        if not image_url:
            style_elem = div.find(style=lambda x: x and 'background' in x if x else False)
            if style_elem:
                style = style_elem.get('style', '')
                url_match = re.search(r'url\(["\']?([^"\')\s]+)["\']?\)', style)
                if url_match:
                    img_src = url_match.group(1)
                    if img_src.startswith('https://'):
                        image_url = img_src

        # Categorize
        category = self.categorize_event(title, description)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start_datetime,
            source_url=event_url,
            source_name=self.source_name,
            venue_name="Somerville Theatre",
            street_address="55 Davis Square",
            city="Somerville",
            state="MA",
            zip_code="02144",
            category=category,
            image_url=image_url
        )

    def categorize_event(self, title: str, description: str) -> EventCategory:
        """Categorize event based on keywords"""
        text = f"{title} {description}".lower()

        if any(word in text for word in ['concert', 'music', 'band', 'dj', 'live music', 'symphony', 'orchestra', 'fiddle', 'celtic', 'irish']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['magic', 'magician', 'mentalism']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['comedy', 'stand-up', 'comedian', 'improv', 'slutcracker', 'burlesque']):
            return EventCategory.THEATER
        elif any(word in text for word in ['film', 'movie', 'screening', 'cinema']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['theater', 'theatre', 'play', 'musical', 'ballet', 'dance']):
            return EventCategory.THEATER
        else:
            return EventCategory.ARTS_CULTURE
