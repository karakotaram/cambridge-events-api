"""Custom scraper for Somerville Theatre events

The /events/ page is WordPress (the Theater for WordPress plugin) and renders
server-side: one `div.wp_theatre_event` per performance, with the date and the
time in separate elements:

    <div class="wp_theatre_event_date wp_theatre_event_startdate">October 9, 2026</div>
    <div class="wp_theatre_event_time wp_theatre_event_starttime">7:00 pm</div>

A performance listed with a date and no time is published as all-day. One whose
date, or whose time text, cannot be read is skipped.
"""
import logging
import re
from datetime import datetime
from typing import List, Optional
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


USER_AGENT = "CambridgeCalendar/1.0 (+https://cambridgecalendar.com)"

MONTH_DATE = re.compile(
    r'(January|February|March|April|May|June|July|August|September|October|November|December)'
    r'\s+(\d{1,2}),?\s*(\d{4})', re.IGNORECASE)
CLOCK = re.compile(r'^(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?$', re.IGNORECASE)


class SomervilleTheatreScraper(BaseScraper):
    """Custom scraper for Somerville Theatre events using requests"""

    def __init__(self):
        super().__init__(
            source_name="Somerville Theatre",
            source_url="https://www.somervilletheatre.com/events/",
            use_selenium=False
        )

    def get_browser_headers(self) -> dict:
        # The server (nginx, no Cloudflare) returns 403 to requests' default
        # "python-requests" user-agent and 200 to others, so say who we are.
        # Do not claim to be a browser: CLAUDE.md, "Never spoof a browser
        # user-agent".
        return {
            'User-Agent': USER_AGENT,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
        }

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Somerville Theatre"""
        events = []
        seen_events = set()

        # A fetch failure raises: an empty list would read as "the theatre has
        # no shows" rather than "the scrape failed".
        html = self.fetch_html(self.source_url)
        soup = BeautifulSoup(html, 'html.parser')

        # No fallback selector: if the plugin markup changes, zero events is
        # the loud, correct outcome; a broad guess would scrape page chrome.
        event_divs = soup.find_all('div', class_='wp_theatre_event')
        logger.info(f"Found {len(event_divs)} wp_theatre_event elements")

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

        when = self.read_start(div)
        if when is None:
            logger.warning(f"Skipping '{title}' - no readable date/time ({event_url})")
            return None
        start_datetime, all_day = when

        # One row per performance; the listing can repeat a performance
        event_key = (title, start_datetime)
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
            all_day=all_day,
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

    def read_start(self, div) -> Optional[tuple]:
        """(start, all_day) for one performance, or None if it cannot be dated.

        A date with no time element, or an empty one, is a date-only listing:
        all-day at midnight. Time text that is present but is not a clock
        reading ("TBA", "evening") is not date-only - the venue has a time we
        cannot read - so the performance is skipped. This used to default to
        8:00 pm.
        """
        date_el = div.find(class_='wp_theatre_event_startdate') or div.find(class_='wp_theatre_event_date')
        if date_el is None:
            return None
        match = MONTH_DATE.search(self.clean_text(date_el.get_text()))
        if not match:
            return None
        try:
            day = datetime.strptime(f"{match.group(1)} {match.group(2)} {match.group(3)}", "%B %d %Y")
        except ValueError:
            return None

        time_el = div.find(class_='wp_theatre_event_starttime') or div.find(class_='wp_theatre_event_time')
        time_text = self.clean_text(time_el.get_text()) if time_el else ''
        if not time_text:
            return day, True

        clock = CLOCK.match(time_text)
        if not clock:
            return None
        hour, minute = int(clock.group(1)), int(clock.group(2) or 0)
        if not (1 <= hour <= 12 and minute <= 59):
            return None
        hour = hour % 12 + (12 if clock.group(3).lower() == 'p' else 0)
        return day.replace(hour=hour, minute=minute), False

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
