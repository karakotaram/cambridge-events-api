"""Custom scraper for Harvard-Radcliffe Dramatic Club"""
import re
from datetime import datetime
from typing import List
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class HRDCScraper(BaseScraper):
    """Custom scraper for HRDC theater events"""

    def __init__(self):
        super().__init__(
            source_name="Harvard-Radcliffe Dramatic Club",
            source_url="https://my.hrdctheater.org/publicity/calendar/",
            use_selenium=True
        )

    def fetch_show_details(self, show_url: str) -> tuple:
        """Fetch the description and image from a show detail page
        Returns (description, image_url)
        """
        try:
            if not self.driver:
                return "", None

            self.driver.get(show_url)
            import time
            time.sleep(2)

            html = self.driver.page_source
            soup = self.parse_html(html)

            # Extract image - look for show poster/image
            image_url = None

            # Try og:image first
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                image_url = og_image['content']

            # If no og:image, look for show image in main content
            if not image_url:
                main = soup.find('main') or soup.find('article')
                if main:
                    img = main.find('img', src=True)
                    if img:
                        img_src = img.get('src') or img.get('data-src')
                        if img_src and not any(skip in img_src.lower() for skip in ['logo', 'icon', 'avatar']):
                            image_url = img_src

            # Normalize image URL
            if image_url and not image_url.startswith('http'):
                if image_url.startswith('//'):
                    image_url = f'https:{image_url}'
                else:
                    image_url = f"https://hrdctheater.org{image_url}"

            # Find main content area for description
            main = soup.find('main') or soup.find('article')
            description = ""
            if main:
                # Get all paragraphs from main
                paragraphs = main.find_all('p')
                description_parts = []

                for p in paragraphs:
                    text = self.clean_text(p.get_text())
                    # Look for substantive paragraphs (skip credits, dates, warnings)
                    if len(text) > 100 and not any(skip in text.lower() for skip in [
                        'director', 'producer', 'stage manager', 'music director',
                        'wednesday,', 'thursday,', 'friday,', 'saturday,', 'sunday,',
                        'run time:', 'content warning', 'free with huid'
                    ]):
                        description_parts.append(text)

                if description_parts:
                    # Use the first substantive paragraph as description
                    description = description_parts[0][:2000]

            return description, image_url
        except Exception:
            return "", None

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from HRDC calendar"""
        events = []
        seen_identifiers = set()  # Track (title, date) to avoid duplicates

        try:
            html = self.fetch_html(self.source_url)
            soup = self.parse_html(html)

            # Find all event list items
            event_items = soup.find_all('li', class_='list-group-item')

            for item in event_items:
                try:
                    # Find the title link
                    title_link = item.find('a', href=lambda x: x and 'hrdctheater.org/shows/' in x if x else False)
                    if not title_link:
                        continue

                    title = self.clean_text(title_link.get_text())
                    if len(title) < 3:
                        continue

                    # Skip "apps due" events - these aren't real events
                    if 'apps due' in title.lower() or 'applications' in title.lower():
                        continue

                    # Extract event URL
                    event_url = title_link.get('href', '')
                    if not event_url.startswith('http'):
                        event_url = f"https://hrdctheater.org{event_url}"

                    # Extract date/time from pull-right span
                    date_span = item.find('span', class_='pull-right')
                    if not date_span:
                        continue

                    date_text = self.clean_text(date_span.get_text())
                    # Format: "Dec 4 @ 7:30 PM" or "Nov 28 @ 11:59 PM"

                    # Skip if we've already seen this event on this date
                    identifier = f"{title}|{date_text}"
                    if identifier in seen_identifiers:
                        continue
                    seen_identifiers.add(identifier)

                    # Parse the date/time
                    try:
                        # Add current year if not present
                        current_year = datetime.now().year
                        date_with_year = f"{date_text} {current_year}"
                        start_datetime = date_parser.parse(date_with_year, fuzzy=True)

                        # If the parsed date is in the past, assume it's next year
                        if start_datetime < datetime.now():
                            start_datetime = date_parser.parse(f"{date_text} {current_year + 1}", fuzzy=True)
                    except Exception:
                        # If parsing fails, skip this event
                        continue

                    # Extract venue information
                    venue_name = None
                    street_address = None

                    # Look for Google Maps link with venue info
                    maps_link = item.find('a', href=lambda x: x and 'google.com/maps' in x if x else False)
                    if maps_link:
                        # Venue name is in a span inside the maps link
                        venue_span = maps_link.find('span', class_='badge')
                        if venue_span:
                            venue_text = self.clean_text(venue_span.get_text())
                            if venue_text and len(venue_text) < 100:
                                venue_name = venue_text[:200]

                    # Fetch description and image from show detail page
                    description, image_url = self.fetch_show_details(event_url)
                    if not description or len(description) < 50:
                        description = title

                    # All HRDC events are theater
                    category = EventCategory.THEATER

                    event = EventCreate(
                        title=title[:200],
                        description=description,
                        start_datetime=start_datetime,
                        source_url=event_url,
                        source_name=self.source_name,
                        venue_name=venue_name,
                        street_address=street_address,
                        city="Cambridge",
                        state="MA",
                        category=category,
                        image_url=image_url
                    )
                    events.append(event)

                except Exception as e:
                    continue

        except Exception as e:
            pass

        return events
