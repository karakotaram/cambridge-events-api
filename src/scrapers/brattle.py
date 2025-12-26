"""Custom scraper for Brattle Theatre showtimes from coming-soon page"""
import logging
import re
from datetime import datetime
from typing import List, Optional
import requests

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class BrattleTheaterScraper(BaseScraper):
    """Custom scraper for Brattle Theatre showtimes"""

    def __init__(self):
        super().__init__(
            source_name="Brattle Theatre",
            source_url="https://brattlefilm.org/coming-soon/",
            use_selenium=False  # HTML is server-rendered
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape all showtimes from Brattle Theatre coming-soon page"""
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        events = []
        now = datetime.now()

        # Find all show-details containers (each represents a film)
        show_details = soup.find_all('div', class_='show-details')
        logger.info(f"Found {len(show_details)} films on coming-soon page")

        for show in show_details:
            try:
                # Extract title and URL
                title_elem = show.find('h2', class_='show-title')
                if not title_elem:
                    continue

                title_link = title_elem.find('a')
                if not title_link:
                    continue

                title = self.clean_text(title_link.get_text())
                film_url = title_link.get('href', self.source_url)

                if not title or len(title) < 2:
                    continue

                # Extract poster image
                image_url = None
                poster_div = show.find('div', class_='show-poster')
                if poster_div:
                    img = poster_div.find('img')
                    if img:
                        image_url = img.get('src') or img.get('data-src')

                # Extract description from film detail page (cached per film)
                description = self._fetch_film_description(film_url, title)

                # Find all showtimes - each li with data-date in showtimes-container
                showtimes_container = show.find('div', class_='showtimes-container')
                if not showtimes_container:
                    continue

                showtime_items = showtimes_container.find_all('li', attrs={'data-date': True})

                for item in showtime_items:
                    try:
                        # Get Unix timestamp from data-date
                        timestamp = int(item.get('data-date', 0))
                        if timestamp == 0:
                            continue

                        # Get time text from the anchor
                        time_anchor = item.find('a', class_='showtime')
                        if not time_anchor:
                            continue

                        # Extract time text (e.g., "12:30 pm")
                        time_text = time_anchor.get_text(strip=True)

                        # Check for format info (e.g., "35mm")
                        extra_span = time_anchor.find('span', class_='extra')
                        format_info = None
                        if extra_span:
                            format_info = extra_span.get_text(strip=True)
                            # Remove format from time text
                            time_text = time_text.replace(format_info, '').strip()

                        # Parse time
                        time_match = re.match(r'(\d{1,2}):?(\d{2})?\s*(am|pm)', time_text, re.IGNORECASE)
                        if not time_match:
                            continue

                        hour = int(time_match.group(1))
                        minute = int(time_match.group(2) or 0)
                        am_pm = time_match.group(3).lower()

                        if am_pm == 'pm' and hour != 12:
                            hour += 12
                        elif am_pm == 'am' and hour == 12:
                            hour = 0

                        # Create datetime from Unix timestamp (date) + parsed time
                        date_from_timestamp = datetime.fromtimestamp(timestamp)
                        start_datetime = date_from_timestamp.replace(
                            hour=hour, minute=minute, second=0, microsecond=0
                        )

                        # Skip past events
                        if start_datetime < now:
                            continue

                        # Get purchase URL
                        purchase_url = time_anchor.get('href', film_url)

                        # Build event title with format info if available
                        event_title = title
                        if format_info:
                            event_title = f"{title} ({format_info})"

                        # Create event
                        event = EventCreate(
                            title=event_title[:200],
                            description=description[:2000] if description else f"{title} at Brattle Theatre",
                            start_datetime=start_datetime,
                            venue_name="Brattle Theatre",
                            street_address="40 Brattle Street",
                            city="Cambridge",
                            state="MA",
                            zip_code="02138",
                            category=EventCategory.ARTS_CULTURE,
                            image_url=image_url,
                            source_name=self.source_name,
                            source_url=purchase_url
                        )
                        events.append(event)

                    except Exception as e:
                        logger.warning(f"Error parsing showtime for {title}: {e}")
                        continue

            except Exception as e:
                logger.warning(f"Error parsing film: {e}")
                continue

        logger.info(f"Scraped {len(events)} showtimes from Brattle Theatre")
        return events

    def _fetch_film_description(self, film_url: str, title: str) -> str:
        """Fetch description from film detail page"""
        try:
            response = requests.get(film_url, timeout=30, headers=self.get_browser_headers())
            response.raise_for_status()
            soup = self.parse_html(response.text)

            # Try og:description first
            og_desc = soup.find('meta', property='og:description')
            if og_desc and og_desc.get('content'):
                desc = self.clean_text(og_desc['content'])
                if len(desc) > 30:
                    return desc

            # Try meta description
            meta_desc = soup.find('meta', attrs={'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                desc = self.clean_text(meta_desc['content'])
                if len(desc) > 30:
                    return desc

            # Try finding description in page content
            desc_div = soup.find('div', class_='entry-content')
            if desc_div:
                paragraphs = desc_div.find_all('p')
                for p in paragraphs:
                    text = self.clean_text(p.get_text())
                    if len(text) > 50:
                        return text[:2000]

            return f"{title} at Brattle Theatre"

        except Exception as e:
            logger.warning(f"Error fetching description for {title}: {e}")
            return f"{title} at Brattle Theatre"
