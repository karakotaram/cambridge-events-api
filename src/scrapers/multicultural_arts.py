"""Custom scraper for Multicultural Arts Center events"""
import logging
import re
import time
from datetime import datetime
from typing import List, Optional
import requests

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class MulticulturalArtsCenterScraper(BaseScraper):
    """Custom scraper for Multicultural Arts Center events"""

    def __init__(self):
        super().__init__(
            source_name="Multicultural Arts Center",
            source_url="https://multiculturalartscenter.org/events/",
            use_selenium=False
        )
        self.base_url = "https://multiculturalartscenter.org"

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Multicultural Arts Center"""
        events = []

        # Scrape first few pages of events
        for page in range(1, 4):  # Pages 1-3
            page_url = self.source_url if page == 1 else f"{self.source_url}page/{page}/"
            try:
                page_events = self._scrape_events_page(page_url)
                events.extend(page_events)
                logger.info(f"Scraped {len(page_events)} events from page {page}")

                if len(page_events) == 0:
                    break  # No more events
            except Exception as e:
                logger.warning(f"Error scraping page {page}: {e}")
                break

        logger.info(f"Scraped {len(events)} total events from Multicultural Arts Center")
        return events

    def _scrape_events_page(self, page_url: str) -> List[EventCreate]:
        """Scrape events from a single page"""
        html = self.fetch_html(page_url)
        soup = self.parse_html(html)
        events = []
        now = datetime.now()

        # Find all event articles - they have class 'elementor-post'
        articles = soup.find_all('article', class_='elementor-post')
        logger.info(f"Found {len(articles)} event articles on page")

        for article in articles:
            try:
                # Get title from h3
                title_elem = article.find('h3')
                if not title_elem:
                    continue

                title = self.clean_text(title_elem.get_text())
                if not title or len(title) < 3:
                    continue

                # Get event link
                link_elem = title_elem.find('a') or article.find('a')
                if not link_elem:
                    continue

                event_url = link_elem.get('href', '')
                if not event_url:
                    continue

                # Get image
                image_url = None
                img = article.find('img')
                if img:
                    image_url = img.get('src') or img.get('data-src')

                # Fetch detail page for more info (with rate limiting)
                time.sleep(1)  # Be respectful to the server
                event_data = self._fetch_event_details(event_url, title)
                if not event_data:
                    continue

                # Skip past events
                if event_data['start_datetime'] and event_data['start_datetime'] < now:
                    continue

                event = EventCreate(
                    title=title[:200],
                    description=event_data['description'][:2000],
                    start_datetime=event_data['start_datetime'],
                    end_datetime=event_data.get('end_datetime'),
                    venue_name="Multicultural Arts Center",
                    street_address="41 Second Street",
                    city="Cambridge",
                    state="MA",
                    zip_code="02141",
                    category=EventCategory.ARTS_CULTURE,
                    image_url=image_url or event_data.get('image_url'),
                    source_name=self.source_name,
                    source_url=event_url,
                    cost=event_data.get('cost')
                )
                events.append(event)
                logger.info(f"Scraped event: {title}")

            except Exception as e:
                logger.warning(f"Error parsing event article: {e}")
                continue

        return events

    def _fetch_event_details(self, event_url: str, title: str) -> Optional[dict]:
        """Fetch event details from detail page"""
        try:
            response = requests.get(event_url, timeout=30, headers=self.get_browser_headers())
            response.raise_for_status()
            soup = self.parse_html(response.text)

            details = {
                'description': '',
                'start_datetime': None,
                'end_datetime': None,
                'cost': None,
                'image_url': None
            }

            # Get description from og:description or page content
            og_desc = soup.find('meta', property='og:description')
            if og_desc and og_desc.get('content'):
                details['description'] = self.clean_text(og_desc['content'])

            # If no og description, try to get from page content
            if not details['description'] or len(details['description']) < 30:
                # Look for main content paragraphs
                content_divs = soup.find_all(['div', 'section'], class_=re.compile(r'elementor-widget-text|entry-content'))
                for div in content_divs:
                    paragraphs = div.find_all('p')
                    for p in paragraphs:
                        text = self.clean_text(p.get_text())
                        if len(text) > 50 and not text.startswith('http'):
                            details['description'] = text
                            break
                    if details['description'] and len(details['description']) > 50:
                        break

            if not details['description'] or len(details['description']) < 30:
                details['description'] = f"{title} at Multicultural Arts Center, 41 Second Street, Cambridge."

            # Get image from og:image
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                details['image_url'] = og_image['content']

            # Parse date and time from page content
            page_text = soup.get_text()

            # Try to find date patterns like "January 16-18, 2026" or "January 16, 2026"
            date_patterns = [
                r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})(?:\s*[-–]\s*\d{1,2})?,?\s*(\d{4})',
                r'(\d{1,2})/(\d{1,2})/(\d{4})',
            ]

            for pattern in date_patterns:
                match = re.search(pattern, page_text, re.IGNORECASE)
                if match:
                    try:
                        date_str = match.group(0)
                        # Handle date ranges - take the first date
                        date_str = re.sub(r'[-–]\d{1,2}', '', date_str)

                        # Parse the date
                        from dateutil import parser as date_parser
                        parsed_date = date_parser.parse(date_str, fuzzy=True)

                        # Try to find time - look for patterns like "8pm" or "8:00 PM"
                        time_match = re.search(r'(\d{1,2})\s*(?::(\d{2}))?\s*(am|pm)', page_text, re.IGNORECASE)
                        if time_match:
                            hour = int(time_match.group(1))
                            minute = int(time_match.group(2) or 0)
                            am_pm = time_match.group(3).lower()

                            if am_pm == 'pm' and hour != 12:
                                hour += 12
                            elif am_pm == 'am' and hour == 12:
                                hour = 0

                            parsed_date = parsed_date.replace(hour=hour, minute=minute)
                        else:
                            # Default to 7pm for evening events
                            parsed_date = parsed_date.replace(hour=19, minute=0)

                        details['start_datetime'] = parsed_date
                        break
                    except Exception as e:
                        logger.debug(f"Failed to parse date '{date_str}': {e}")
                        continue

            # If no date found, skip this event
            if not details['start_datetime']:
                logger.warning(f"Could not parse date for event: {title}")
                return None

            return details

        except Exception as e:
            logger.warning(f"Error fetching details for {title}: {e}")
            return None
