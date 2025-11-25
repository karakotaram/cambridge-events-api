"""Custom scraper for The Dance Complex"""
import json
import re
import html
from datetime import datetime
from typing import List
from dateutil import parser as date_parser
from urllib.parse import urljoin

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class DanceComplexScraper(BaseScraper):
    """Custom scraper for The Dance Complex events"""

    def __init__(self):
        super().__init__(
            source_name="The Dance Complex",
            source_url="https://www.dancecomplex.org/",
            use_selenium=False  # JSON-LD data available in static HTML
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from The Dance Complex using JSON-LD and HTML"""
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        events = []

        # Find JSON-LD script tags
        json_ld_scripts = soup.find_all('script', type='application/ld+json')

        for script in json_ld_scripts:
            try:
                data = json.loads(script.string)

                # Handle both single Event and array of Events
                event_list = []
                if isinstance(data, dict):
                    if data.get('@type') == 'Event':
                        event_list = [data]
                    elif '@graph' in data:
                        # Check if @graph contains events
                        for item in data['@graph']:
                            if isinstance(item, dict) and item.get('@type') == 'Event':
                                event_list.append(item)
                elif isinstance(data, list):
                    event_list = [item for item in data if isinstance(item, dict) and item.get('@type') == 'Event']

                # Limit to reasonable number
                for event_data in event_list[:30]:
                    try:
                        # Extract title
                        title = event_data.get('name', '').strip()
                        if not title or len(title) < 3:
                            continue

                        # Extract date
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
                            # Decode HTML entities first
                            description = html.unescape(description)
                            # Remove HTML tags from description
                            description = re.sub(r'<[^>]+>', '', description)
                            description = self.clean_text(description)

                        # Extract image URL
                        image_url = None
                        if 'image' in event_data:
                            if isinstance(event_data['image'], str):
                                image_url = event_data['image']
                            elif isinstance(event_data['image'], dict):
                                image_url = event_data['image'].get('url')
                            elif isinstance(event_data['image'], list) and len(event_data['image']) > 0:
                                first_image = event_data['image'][0]
                                if isinstance(first_image, str):
                                    image_url = first_image
                                elif isinstance(first_image, dict):
                                    image_url = first_image.get('url')

                        # Extract event URL
                        event_url = self.source_url
                        if 'url' in event_data:
                            url = event_data['url']
                            if url.startswith('http'):
                                event_url = url
                            else:
                                event_url = urljoin(self.source_url, url)

                        # Extract cost/price
                        cost = None
                        if 'offers' in event_data:
                            offers = event_data['offers']
                            if isinstance(offers, dict):
                                if 'price' in offers:
                                    price_value = offers['price']
                                    # Handle "0" or "free" prices
                                    if price_value and str(price_value) not in ['0', '0.00']:
                                        currency = offers.get('priceCurrency', 'USD')
                                        if currency == 'USD':
                                            cost = f"${price_value}"
                            elif isinstance(offers, list) and len(offers) > 0:
                                first_offer = offers[0]
                                if isinstance(first_offer, dict) and 'price' in first_offer:
                                    price_value = first_offer['price']
                                    if price_value and str(price_value) not in ['0', '0.00']:
                                        currency = first_offer.get('priceCurrency', 'USD')
                                        if currency == 'USD':
                                            cost = f"${price_value}"

                        # Venue information - The Dance Complex
                        venue_name = "The Dance Complex"
                        street_address = "536 Massachusetts Ave"
                        city = "Cambridge"
                        state = "MA"
                        zip_code = "02139"

                        # Extract venue from location if available
                        if 'location' in event_data:
                            location = event_data['location']
                            if isinstance(location, dict):
                                if 'name' in location:
                                    venue_name = location['name']
                                if 'address' in location:
                                    address = location['address']
                                    if isinstance(address, dict):
                                        if 'streetAddress' in address:
                                            street_address = address['streetAddress']
                                        if 'addressLocality' in address:
                                            city = address['addressLocality']
                                        if 'addressRegion' in address:
                                            state = address['addressRegion']
                                        if 'postalCode' in address:
                                            zip_code = address['postalCode']

                        # All Dance Complex events are sports/dance
                        category = EventCategory.SPORTS

                        event = EventCreate(
                            title=title[:200],
                            description=description[:2000] if description else f"{title} at The Dance Complex in Cambridge, MA",
                            start_datetime=start_datetime,
                            source_url=event_url,
                            source_name=self.source_name,
                            venue_name=venue_name[:200],
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
