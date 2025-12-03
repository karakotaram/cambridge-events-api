"""Custom scraper for First Parish in Cambridge events"""
import logging
import re
import hashlib
from datetime import datetime
from typing import List, Optional
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class FirstParishScraper(BaseScraper):
    """Custom scraper for First Parish in Cambridge events"""

    def __init__(self):
        super().__init__(
            source_name="First Parish in Cambridge",
            source_url="https://www.firstparishcambridge.org/events/",
            use_selenium=False  # Static HTML, no JavaScript needed
        )
        self.base_url = "https://www.firstparishcambridge.org"

    def fetch_event_details(self, event_url: str) -> tuple:
        """Fetch full description and image from event detail page.
        Returns (description, image_url)
        """
        try:
            import requests
            full_url = event_url if event_url.startswith('http') else f"{self.base_url}{event_url}"

            response = requests.get(full_url, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; CambridgeEventScraper/1.0)'
            })
            response.raise_for_status()

            soup = self.parse_html(response.text)

            # Get full description
            description = ""
            desc_elem = soup.find('div', class_='eventitem-column-content')
            if desc_elem:
                # Get all paragraphs
                paragraphs = desc_elem.find_all('p')
                desc_parts = []
                for p in paragraphs:
                    text = self.clean_text(p.get_text())
                    if len(text) > 20:
                        desc_parts.append(text)
                if desc_parts:
                    description = ' '.join(desc_parts[:5])[:2000]

            # Get image
            image_url = None
            # Try og:image first
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                image_url = og_image['content']

            # Try event image
            if not image_url:
                img_elem = soup.find('img', class_=lambda c: c and 'eventitem' in ' '.join(c) if c else False)
                if img_elem:
                    image_url = img_elem.get('data-src') or img_elem.get('src')

            return description, image_url

        except Exception as e:
            logger.warning(f"Failed to fetch event details from {event_url}: {e}")
            return "", None

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from First Parish in Cambridge"""
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        events = []
        seen_urls = set()

        # Find all event articles
        event_articles = soup.find_all('article', class_='eventlist-event')
        logger.info(f"Found {len(event_articles)} event articles")

        # Only process upcoming events
        event_articles = [e for e in event_articles if 'eventlist-event--upcoming' in e.get('class', [])]

        for article in event_articles[:30]:  # Limit to 30 events
            try:
                # Extract title and URL
                title_elem = article.find('h1', class_='eventlist-title')
                if not title_elem:
                    continue

                title_link = title_elem.find('a', class_='eventlist-title-link')
                if not title_link:
                    continue

                title = self.clean_text(title_link.get_text())
                if len(title) < 3:
                    continue

                event_path = title_link.get('href', '')
                if not event_path:
                    continue

                event_url = f"{self.base_url}{event_path}"

                # Skip duplicates
                if event_url in seen_urls:
                    continue
                seen_urls.add(event_url)

                # Extract date
                date_elem = article.find('time', class_='event-date')
                if not date_elem:
                    continue

                date_str = date_elem.get('datetime', '')
                if not date_str:
                    continue

                # Extract time
                start_time_elem = article.find('time', class_='event-time-localized-start')
                end_time_elem = article.find('time', class_='event-time-localized-end')

                start_time = start_time_elem.get_text().strip() if start_time_elem else ""

                # Combine date and time
                try:
                    if start_time:
                        datetime_str = f"{date_str} {start_time}"
                        start_datetime = date_parser.parse(datetime_str)
                    else:
                        start_datetime = date_parser.parse(date_str)
                except Exception as e:
                    logger.warning(f"Failed to parse date '{date_str} {start_time}': {e}")
                    continue

                # Extract category
                cat_elem = article.find('a', href=lambda h: h and 'category=' in h if h else False)
                category_text = cat_elem.get_text().strip() if cat_elem else ""

                # Get excerpt description from listing
                excerpt = ""
                excerpt_elem = article.find('div', class_='eventlist-excerpt')
                if excerpt_elem:
                    excerpt = self.clean_text(excerpt_elem.get_text())[:500]

                # Fetch full description and image from detail page
                full_description, image_url = self.fetch_event_details(event_path)

                # Use full description if available, otherwise excerpt
                description = full_description if full_description else excerpt
                if not description or len(description) < 20:
                    description = f"{title} at First Parish in Cambridge"

                # Venue info - First Parish in Cambridge
                venue_name = "First Parish in Cambridge"
                street_address = "3 Church St"
                city = "Cambridge"
                state = "MA"
                zip_code = "02138"

                # Categorize event
                category = self.categorize_event(title, description, category_text)

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
                    image_url=image_url
                )
                events.append(event)

            except Exception as e:
                logger.warning(f"Failed to parse event: {e}")
                continue

        return events

    def categorize_event(self, title: str, description: str, category_text: str) -> EventCategory:
        """Categorize event based on keywords and category tag"""
        text = f"{title} {description} {category_text}".lower()

        # Check category tag first
        if 'worship' in category_text.lower():
            return EventCategory.COMMUNITY
        elif 'music' in category_text.lower() or 'concert' in category_text.lower():
            return EventCategory.MUSIC

        # Check keywords
        if any(word in text for word in ['concert', 'music', 'choir', 'singing', 'carol']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['worship', 'service', 'sermon', 'prayer', 'meditation']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['meeting', 'committee', 'board']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['class', 'workshop', 'discussion', 'study']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['potluck', 'dinner', 'lunch', 'food']):
            return EventCategory.FOOD_DRINK
        elif any(word in text for word in ['children', 'youth', 'family', 'kids']):
            return EventCategory.COMMUNITY  # Family events categorized as community
        else:
            return EventCategory.COMMUNITY
