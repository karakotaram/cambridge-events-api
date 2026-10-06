"""Custom scraper for The Comedy Studio"""
import json
import re
from datetime import datetime
from typing import List, Optional
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class ComedyStudioScraper(BaseScraper):
    """Custom scraper for The Comedy Studio events"""

    def __init__(self):
        super().__init__(
            source_name="The Comedy Studio",
            source_url="https://www.thecomedystudio.com/",
            use_selenium=False  # JSON-LD data available in static HTML
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from The Comedy Studio using JSON-LD data"""
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        events = []

        # Find JSON-LD script tag
        json_ld_scripts = soup.find_all('script', type='application/ld+json')

        for script in json_ld_scripts:
            try:
                data = json.loads(script.string)

                # Check if this is the events data
                if isinstance(data, dict) and 'events' in data:
                    event_list = data['events']

                    # Every show. The list is not in date order (163 shows,
                    # Oct 2026 - May 2027, on 2026-10-06), so the old [:30]
                    # cap kept an arbitrary slice of the season.
                    for event_data in event_list:
                        try:
                            # Extract title
                            title = event_data.get('name', '').strip()
                            if not title or len(title) < 3:
                                continue

                            # Extract date (ISO 8601 format)
                            start_date_str = event_data.get('startDate')
                            if not start_date_str:
                                continue

                            try:
                                start_datetime = date_parser.parse(start_date_str)
                            except:
                                continue

                            # Extract description
                            description = event_data.get('description', '')
                            if description:
                                # Remove HTML tags from description
                                description = re.sub(r'<[^>]+>', '', description)
                                # Remove "Ticket Link:" and associated URLs (case-insensitive)
                                description = re.sub(r'TICKET\s+LINK:\s*https?://[^\s]+\s*', '', description, flags=re.IGNORECASE)
                                description = self.clean_text(description)

                            # Extract image URL, falling back to the first
                            # performer's. `performer` is a list on this site
                            # (schema.org allows one or many), so a dict-only
                            # check never reached the fallback.
                            image_url = self._image_url(event_data.get('image'))
                            if not image_url:
                                for performer in self._as_list(event_data.get('performer')):
                                    if isinstance(performer, dict):
                                        image_url = self._image_url(performer.get('image'))
                                    if image_url:
                                        break

                            # Extract event URL from offers
                            event_url = self.source_url
                            if 'offers' in event_data and event_data['offers']:
                                offers = event_data['offers']
                                if isinstance(offers, list) and len(offers) > 0:
                                    first_offer = offers[0]
                                    if isinstance(first_offer, dict) and 'url' in first_offer:
                                        event_url = first_offer['url']

                            # Extract cost
                            cost = None
                            if 'offers' in event_data and event_data['offers']:
                                offers = event_data['offers']
                                if isinstance(offers, list) and len(offers) > 0:
                                    first_offer = offers[0]
                                    if isinstance(first_offer, dict) and 'price' in first_offer:
                                        price_value = first_offer['price']
                                        currency = first_offer.get('priceCurrency', 'USD')
                                        if currency == 'USD':
                                            cost = f"${price_value}"

                            # Venue information - The Comedy Studio
                            venue_name = "The Comedy Studio"
                            street_address = "5 John F. Kennedy St"
                            city = "Cambridge"
                            state = "MA"
                            zip_code = "02138"

                            # All Comedy Studio events are theater/performance
                            category = EventCategory.THEATER

                            event = EventCreate(
                                title=title[:200],
                                description=description[:2000] if description else f"{title} at The Comedy Studio in Cambridge, MA",
                                start_datetime=start_datetime,
                                source_url=event_url,
                                source_name=self.source_name,
                                venue_name=venue_name,
                                street_address=street_address,
                                city=city,
                                state=state,
                                zip_code=zip_code,
                                category=category,
                                cost=cost,
                                image_url=image_url
                            )
                            events.append(event)

                        except Exception as e:
                            # Log error but continue processing other events
                            continue

            except json.JSONDecodeError:
                # Not valid JSON, skip this script tag
                continue
            except Exception as e:
                # Log error but continue processing other script tags
                continue

        return events

    @staticmethod
    def _as_list(value) -> list:
        """schema.org properties hold one value or a list of them."""
        if value is None:
            return []
        return value if isinstance(value, list) else [value]

    @classmethod
    def _image_url(cls, value) -> Optional[str]:
        """An ImageObject, a URL string, or a list of either."""
        for item in cls._as_list(value):
            if isinstance(item, str) and item:
                return item
            if isinstance(item, dict) and item.get('url'):
                return item['url']
        return None
