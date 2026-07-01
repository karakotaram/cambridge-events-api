"""Custom scraper for Harvard Book Store events"""
import logging
import re
from datetime import datetime
from typing import List, Optional
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
            full_url = event_url if event_url.startswith('http') else f"https://www.harvard.com{event_url}"
            response = requests.get(full_url, timeout=30, headers=self.get_browser_headers())
            response.raise_for_status()
            soup = self.parse_html(response.text)

            # The event body renders in one or more `.aba-body` blocks; take the
            # longest substantial one as the description.
            blocks = [self.clean_text(b.get_text(' ')) for b in soup.find_all(class_='aba-body')]
            blocks = [b for b in blocks if len(b) > 30]
            if blocks:
                return max(blocks, key=len)[:2000]
            return ""
        except Exception:
            return ""

    @staticmethod
    def _detail_item(row, label: str) -> str:
        """Return the value of an `event-list__details--item` by its label (e.g. Date/Time)."""
        for item in row.find_all(class_='event-list__details--item'):
            lab = item.find(class_='event-list__details--label')
            if lab and lab.get_text(strip=True).rstrip(':').lower() == label.lower():
                full = ' '.join(item.get_text(' ', strip=True).split())
                return full.replace(lab.get_text(strip=True), '', 1).strip()
        return ""

    def _parse_row(self, row) -> Optional[EventCreate]:
        """Parse a single `.views-row` from the events listing."""
        title_elem = row.find(class_='event-list__title')
        link = title_elem.find('a', href=True) if title_elem else None
        if not link:
            return None
        title = self.clean_text(link.get_text())
        if len(title) < 3:
            return None

        href = link.get('href', '')
        event_url = href if href.startswith('http') else f"https://www.harvard.com{href}"

        # Date/time live in labeled detail items: "Wed, 7/1/2026" + "7:00pm - 8:00pm"
        date_str = self._detail_item(row, 'Date')
        if not date_str:
            return None
        time_str = self._detail_item(row, 'Time')
        start_part = time_str.split('-')[0].strip() if time_str else ''
        try:
            start_datetime = date_parser.parse(f"{date_str} {start_part}".strip(), fuzzy=True)
        except Exception:
            return None

        # Venue / address parsed from the location <address> block
        venue_name = "Harvard Book Store"
        street_address, city, state, zip_code = "1256 Massachusetts Ave", "Cambridge", "MA", "02138"
        loc = row.find(class_='event-details__location--location')
        addr = loc.find('address') if loc else None
        if addr:
            lines = [ln.strip() for ln in addr.get_text('\n').split('\n') if ln.strip()]
            if lines:
                venue_name = lines[0]
            if len(lines) >= 2:
                street_address = lines[1]
            if len(lines) >= 3:
                m = re.match(r'(?P<city>[^,]+),\s*(?P<state>[A-Z]{2})\s*(?P<zip>\d{5})', lines[2])
                if m:
                    city, state, zip_code = m.group('city').strip(), m.group('state'), m.group('zip')

        # Cost from tags (Free / Ticketed)
        cost = None
        for tag in row.find_all(class_='event-tag__term'):
            t = tag.get_text(strip=True).lower()
            if t == 'free':
                cost = "Free"
            elif t.startswith('ticket'):
                cost = "Ticketed"

        # Description: full text from the detail page, else the listing body, else title
        description = self.fetch_event_description(event_url)
        if not description or len(description) < 20:
            body = row.find(class_='event-list__body')
            if body:
                description = self.clean_text(body.get_text())
        if not description or len(description) < 20:
            description = f"{title} at {venue_name}"

        # Image
        image_url = None
        img_wrap = row.find(class_='event-list__image')
        img = img_wrap.find('img', src=True) if img_wrap else None
        if img:
            src = img.get('src', '')
            image_url = src if src.startswith('http') else f"https://www.harvard.com{src}"

        category = self.categorize_event(title, description)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start_datetime,
            source_url=event_url or self.source_url,
            source_name=self.source_name,
            venue_name=venue_name[:150],
            street_address=street_address[:200],
            city=city,
            state=state,
            zip_code=zip_code,
            category=category,
            cost=cost,
            image_url=image_url,
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Harvard Book Store.

        The listing (harvard.com/events) renders all upcoming events on a single
        page as `.views-row` items; there is no server-side pagination.
        """
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        rows = soup.find_all('div', class_='views-row')
        logger.info(f"Found {len(rows)} event rows")

        events = []
        for row in rows:
            try:
                event = self._parse_row(row)
                if event:
                    events.append(event)
            except Exception as e:
                logger.warning(f"Error parsing Harvard Book Store event: {e}")
                continue

        logger.info(f"Parsed {len(events)} events from Harvard Book Store")
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
