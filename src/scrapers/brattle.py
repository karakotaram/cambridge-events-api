"""Custom scraper for Brattle Theater special events"""
import logging
import re
from datetime import datetime
from typing import List, Optional
from dateutil import parser as date_parser
import requests

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class BrattleTheaterScraper(BaseScraper):
    """Custom scraper for Brattle Theater special events"""

    def __init__(self):
        super().__init__(
            source_name="Brattle Theatre",
            source_url="https://brattlefilm.org/film-series/special-events/",
            use_selenium=False  # HTML is server-rendered
        )

    def fetch_event_image(self, event_url: str) -> Optional[str]:
        """Fetch the image URL from an event detail page"""
        try:
            response = requests.get(event_url, timeout=30, headers=self.get_browser_headers())
            response.raise_for_status()
            soup = self.parse_html(response.text)

            # Look for poster image with alt text containing "Poster"
            imgs = soup.find_all('img')
            for img in imgs:
                alt = img.get('alt', '')
                src = img.get('src') or img.get('data-src')
                if src and 'poster' in alt.lower():
                    return src

            # Fallback to og:image
            og_img = soup.find('meta', property='og:image')
            if og_img and og_img.get('content'):
                return og_img['content']

            return None
        except Exception as e:
            logger.warning(f"Error fetching image from {event_url}: {e}")
            return None

    def scrape_events(self) -> List[EventCreate]:
        """Scrape special events from Brattle Theater"""
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        events = []
        current_year = datetime.now().year

        # Find all show-details containers (each represents an event)
        show_details = soup.find_all('div', class_='show-details')
        logger.info(f"Found {len(show_details)} show-details elements")

        for show in show_details:
            try:
                # Extract title and URL from h2
                h2 = show.find('h2')
                if not h2:
                    continue

                title = self.clean_text(h2.get_text())
                if not title or len(title) < 3:
                    continue

                link = h2.find('a')
                event_url = link.get('href') if link else self.source_url

                # Extract date from the full text using regex
                full_text = show.get_text()
                date_match = re.search(
                    r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun)[,.]?\s+'
                    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+(\d{1,2})',
                    full_text
                )

                if not date_match:
                    logger.warning(f"No date found for {title}")
                    continue

                date_str = date_match.group()

                # Extract time from showtimes-container list items
                time_str = "7:00 pm"  # Default time
                showtimes_container = show.find('div', class_='showtimes-container')
                if showtimes_container:
                    time_item = showtimes_container.find('li')
                    if time_item:
                        time_text = time_item.get_text().strip()
                        # Extract time pattern
                        time_match = re.search(r'(\d{1,2}:\d{2}\s*(?:am|pm))', time_text, re.IGNORECASE)
                        if time_match:
                            time_str = time_match.group(1)

                # Parse datetime
                try:
                    datetime_str = f"{date_str} {current_year} {time_str}"
                    start_datetime = date_parser.parse(datetime_str, fuzzy=True)

                    # If the parsed date is in the past, assume next year
                    if start_datetime < datetime.now():
                        start_datetime = start_datetime.replace(year=current_year + 1)
                except Exception as e:
                    logger.warning(f"Failed to parse datetime for {title}: {e}")
                    continue

                # Extract runtime
                runtime = None
                runtime_match = re.search(r'Run Time:\s*(\d+)\s*min', full_text)
                if runtime_match:
                    runtime = f"{runtime_match.group(1)} min"

                # Extract description
                description = self._extract_description(show, title, runtime)

                # Fetch image from detail page
                image_url = None
                if event_url and event_url != self.source_url:
                    image_url = self.fetch_event_image(event_url)

                # Create event
                event = EventCreate(
                    title=title[:200],
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
                    source_url=event_url
                )
                events.append(event)
                logger.info(f"Parsed event: {title} on {start_datetime}")

            except Exception as e:
                logger.warning(f"Error parsing event: {e}")
                continue

        return events

    def _extract_description(self, show, title: str, runtime: Optional[str]) -> str:
        """Extract and clean description from event container"""
        try:
            desc_inner = show.find('div', class_='showtimes-description-inner')
            if not desc_inner:
                return f"{title} at Brattle Theatre"

            # Get all text content
            full_text = desc_inner.get_text()

            # Split by runtime and get the description part
            if runtime:
                parts = re.split(r'Run Time:\s*\d+\s*min\.?', full_text, flags=re.IGNORECASE)
                if len(parts) > 1:
                    description = parts[1].strip()
                else:
                    description = full_text
            else:
                description = full_text

            # Clean up the description - remove boilerplate
            # Remove "Special Events" category tag
            description = re.sub(r'\s*Special Events\s*', ' ', description)
            # Remove "See full details for..." text
            description = re.sub(r'See full details for[^.]*\.?', '', description, flags=re.IGNORECASE)
            description = re.sub(r'See full details\s*', '', description, flags=re.IGNORECASE)
            # Remove "Dates with showtimes for..." text
            description = re.sub(r'Dates with showtimes for[^.]*\.?', '', description, flags=re.IGNORECASE)
            # Remove "Created with Sketch" (SVG remnant)
            description = re.sub(r'Created with Sketch\.?\s*', '', description, flags=re.IGNORECASE)
            # Remove format/year/starring info (we'll use this for films)
            description = re.sub(r'Format:\s*\w+\s*', '', description)
            description = re.sub(r'Release Year:\s*\d+\s*', '', description)
            # Clean up "Starring:" line but keep the info
            description = re.sub(r'Starring:\s*', 'Starring: ', description)
            # Remove "Filmmaker in Person!" prefix (but keep the context)
            description = re.sub(r'^Filmmaker in Person!\s*', '', description)
            # Remove "Watch trailer for..." text
            description = re.sub(r'Watch trailer for[^.]*\.?\s*', '', description, flags=re.IGNORECASE)
            description = re.sub(r'Watch trailer\s*', '', description, flags=re.IGNORECASE)

            # Clean whitespace
            description = self.clean_text(description)

            # If description is too short, provide a default
            if len(description) < 20:
                return f"{title} at Brattle Theatre"

            # Add runtime info to description if available
            if runtime:
                description = f"{description} ({runtime})"

            return description

        except Exception as e:
            logger.warning(f"Error extracting description: {e}")
            return f"{title} at Brattle Theatre"
