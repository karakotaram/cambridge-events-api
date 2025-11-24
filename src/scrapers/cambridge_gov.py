"""Custom scraper for Cambridge.gov events"""
import re
from datetime import datetime, timedelta
from typing import List
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class CambridgeGovScraper(BaseScraper):
    """Custom scraper for City of Cambridge events"""

    def __init__(self):
        super().__init__(
            source_name="City of Cambridge",
            source_url="https://www.cambridgema.gov/citycalendar",
            use_selenium=True  # May need JavaScript rendering
        )

    def fetch_event_details(self, event_url: str) -> tuple:
        """Fetch description, image, datetime, and location from an event detail page
        Returns: (description, image_url, datetime_obj, venue_name, street_address)
        """
        try:
            if not self.driver:
                return "", None, None, None, None

            # Navigate to event page
            self.driver.get(event_url)
            import time
            time.sleep(0.5)  # Reduced from 2s to 0.5s for faster scraping

            # Parse the page
            html = self.driver.page_source
            soup = self.parse_html(html)

            # Extract image URL
            image_url = None
            # Try og:image meta tag first
            og_image = soup.find('meta', {'property': 'og:image'})
            if og_image and og_image.get('content'):
                image_url = og_image.get('content')
            # Try finding img tags in main content
            if not image_url:
                main_content = soup.find('main') or soup.find('article') or soup.find('div', class_='content')
                if main_content:
                    img = main_content.find('img')
                    if img and img.get('src'):
                        src = img.get('src')
                        # Make absolute URL if needed
                        if src.startswith('/'):
                            image_url = f"https://www.cambridgema.gov{src}"
                        elif src.startswith('http'):
                            image_url = src

            # Extract date/time - look for text like "Monday November 24, 2025 10:30 AM - 12:00 PM"
            datetime_obj = None
            page_text = soup.get_text()
            # Look for date pattern with day of week
            import re
            date_pattern = r'(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+([A-Z][a-z]+)\s+(\d{1,2}),?\s+(\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)'
            match = re.search(date_pattern, page_text)
            if match:
                try:
                    # Parse the datetime from the matched text
                    datetime_str = f"{match.group(2)} {match.group(3)}, {match.group(4)} {match.group(5)}"
                    datetime_obj = date_parser.parse(datetime_str, fuzzy=False)
                except:
                    pass

            # Extract location - look for "Get directions" link or text after "Location" heading
            venue_name = None
            street_address = None

            # Try to find Google Maps "Get directions" link which has full address in daddr parameter
            maps_link = soup.find('a', href=lambda x: x and 'maps.google.com' in x if x else False)
            if maps_link and 'daddr=' in maps_link.get('href', ''):
                import urllib.parse
                href = maps_link.get('href')
                # Extract daddr parameter
                daddr_match = re.search(r'daddr=([^&]+)', href)
                if daddr_match:
                    # URL decode the address
                    full_address = urllib.parse.unquote_plus(daddr_match.group(1))
                    # Split into venue and street address
                    parts = full_address.split(',')
                    if len(parts) >= 1:
                        venue_name = parts[0].strip()[:200]
                    if len(parts) >= 2:
                        street_address = ', '.join(parts[1:]).strip()[:200]

            # If that didn't work, try finding text near "Location" heading
            if not venue_name:
                location_heading = soup.find(string=re.compile(r'Location', re.IGNORECASE))
                if location_heading:
                    # Look for text after the Location heading
                    parent = location_heading.find_parent()
                    if parent:
                        for sibling in parent.find_next_siblings(limit=3):
                            text = self.clean_text(sibling.get_text())
                            if text and len(text) < 200 and any(word in text.lower() for word in ['street', 'avenue', 'road', 'blvd', 'drive', 'square', 'place', 'cambridge']):
                                parts = text.split(',')
                                if len(parts) >= 1:
                                    venue_name = parts[0].strip()[:200]
                                if len(parts) >= 2:
                                    street_address = ', '.join(parts[1:]).strip()[:200]
                                break

            # Extract description
            description = ""
            # Look for description in meta tags first
            meta_desc = soup.find('meta', {'property': 'og:description'})
            if meta_desc and meta_desc.get('content'):
                desc_text = meta_desc.get('content')
                if len(desc_text) > 20 and 'Thursday,' not in desc_text and 'Monday,' not in desc_text:
                    description = self.clean_text(desc_text)[:2000]

            # Look for paragraphs in main content if no meta description
            if not description:
                main_content = soup.find('main') or soup.find('article')
                if main_content:
                    paragraphs = main_content.find_all('p')
                    description_parts = []

                    for p in paragraphs:
                        text = self.clean_text(p.get_text())
                        # Skip short text and government boilerplate
                        if len(text) > 50 and not any(skip in text.lower() for skip in [
                            'official website', '.gov website', 'secure .gov',
                            'quick links', 'contact', 'calendar'
                        ]):
                            description_parts.append(text)

                    if description_parts:
                        full_description = ' '.join(description_parts[:3])
                        description = full_description[:2000] if len(full_description) > 2000 else full_description

            return description, image_url, datetime_obj, venue_name, street_address
        except Exception as e:
            return "", None, None, None, None

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Cambridge.gov city calendar starting from today"""
        events = []
        seen_urls = set()  # Track URLs to avoid duplicates

        # Start from today to avoid scraping old data
        today = datetime.now()
        start_date = today
        # Scrape for next 60 days
        end_date = today + timedelta(days=60)

        # Scrape week by week using Week view (more efficient than day by day)
        current_date = start_date
        while current_date <= end_date:
            # Format: YYYYMMDDTHHMMSS
            date_str = current_date.strftime("%Y%m%dT000000")
            # Use Week view with 200 results per page
            week_url = f"{self.source_url}?start={date_str}&view=Week&page=1&resultsperpage=200"

            try:
                html = self.fetch_html(week_url)
                soup = self.parse_html(html)

                # Find event links for this day
                event_links = soup.find_all('a', href=lambda x: x and '/citycalendar/view.aspx?guid=' in x if x else False)

                for link in event_links:
                    try:
                        # Extract title from link text
                        title = self.clean_text(link.get_text())
                        if len(title) < 5:
                            continue

                        # Skip cancelled events
                        if 'CANCELLED' in title.upper() or 'CANCELED' in title.upper():
                            continue

                        # Extract event URL
                        event_url = link.get('href', '')
                        if event_url.startswith('/'):
                            event_url = f"https://www.cambridgema.gov{event_url}"
                        elif not event_url.startswith('http'):
                            event_url = self.source_url

                        # Skip if we've already seen this URL
                        if event_url in seen_urls:
                            continue
                        seen_urls.add(event_url)

                        # Fetch description, image, datetime, and location from detail page
                        description, image_url, detail_datetime, detail_venue, detail_address = self.fetch_event_details(event_url)

                        # Use detail page datetime if available, otherwise use current_date
                        start_datetime = detail_datetime if detail_datetime else current_date

                        # If no datetime from detail page, try to find time in parent (fallback)
                        if not detail_datetime:
                            parent = link.find_parent()
                            if parent:
                                # Look for time information
                                for element in parent.find_all_previous(['strong', 'b', 'p', 'div'], limit=5):
                                    text = self.clean_text(element.get_text())
                                    if 'AM' in text or 'PM' in text:
                                        try:
                                            parsed_datetime = date_parser.parse(f"{current_date.date()} {text}", fuzzy=True)
                                            start_datetime = parsed_datetime
                                            break
                                        except:
                                            pass

                        # Use description or title as fallback
                        if not description or len(description) < 20:
                            description = title

                        # Use detail page location if available, otherwise try to extract from listing page
                        venue_name = detail_venue
                        street_address = detail_address

                        if not venue_name:
                            parent = link.find_parent()
                            if parent:
                                for element in parent.find_all_next(['em', 'i', 'p'], limit=10):
                                    text = self.clean_text(element.get_text())
                                    if text and len(text) < 150 and any(word in text.lower() for word in ['street', 'avenue', 'road', 'blvd', 'drive', 'square', 'place']):
                                        parts = text.split(',')
                                        if len(parts) >= 1:
                                            venue_name = parts[0].strip()[:200]
                                        if len(parts) >= 2:
                                            street_address = ', '.join(parts[1:]).strip()[:200]
                                        break

                        # Determine category based on content
                        category = self.categorize_event(title, description)

                        event = EventCreate(
                            title=title[:200],
                            description=description[:2000],
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

            # Move to next week
            current_date += timedelta(days=7)

        return events

    def categorize_event(self, title: str, description: str) -> EventCategory:
        """Categorize event based on keywords"""
        text = f"{title} {description}".lower()

        # Fitness/exercise classes and dance training should be sports (check before generic "game" keyword)
        if any(word in text for word in ['zumba', 'yoga', 'pilates', 'tai chi', 'exercise', 'workout', 'dance class', 'aerobics', 'line dancing', 'swing dance', 'swing training', 'swing class']):
            return EventCategory.SPORTS
        # Concert/music events
        elif any(word in text for word in ['concert', 'music', 'band', 'orchestra', 'jazz', 'rock', 'folk music']):
            return EventCategory.MUSIC
        # Lectures and educational (check before art/culture to catch information sessions)
        elif any(word in text for word in ['lecture', 'talk', 'presentation', 'seminar', 'workshop', 'information session', 'training program']):
            return EventCategory.LECTURES
        # Children's activities (crafts, story time, sing-alongs)
        elif any(word in text for word in ['story time', 'storytime', 'sing-along', 'craft', 'children', 'kids activity']):
            return EventCategory.ARTS_CULTURE
        # Trivia and games
        elif any(word in text for word in ['trivia', 'quiz', 'jeopardy', 'bingo']):
            return EventCategory.ARTS_CULTURE
        # Art and culture (matches after lectures to avoid false matches on "art" substring)
        elif any(word in text for word in ['art ', ' art', 'gallery', 'exhibit', 'museum', 'painting', 'sculpture']):
            return EventCategory.ARTS_CULTURE
        # Theater
        elif any(word in text for word in ['theater', 'play', 'performance', 'drama', 'acting']):
            return EventCategory.THEATER
        # Sports (check after fitness classes)
        elif any(word in text for word in ['sport', 'tournament', 'competition', 'athletics']):
            return EventCategory.SPORTS
        # Food and drink (be specific to avoid false matches)
        elif any(word in text for word in ['tasting', 'brewery', 'wine', 'beer', 'cocktail', 'dinner', 'brunch', 'lunch']):
            return EventCategory.FOOD_DRINK
        # Community events
        elif any(word in text for word in ['community', 'meeting', 'council', 'public hearing', 'town hall']):
            return EventCategory.COMMUNITY
        else:
            return EventCategory.OTHER
