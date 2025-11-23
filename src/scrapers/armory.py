"""Custom scraper for Arts at the Armory events"""
import re
from datetime import datetime
from typing import List
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class ArtsAtTheArmoryScraper(BaseScraper):
    """Custom scraper for Arts at the Armory events"""

    def __init__(self):
        super().__init__(
            source_name="Arts at the Armory",
            source_url="https://artsatthearmory.org/upcoming-events/",
            use_selenium=True  # JavaScript-rendered content
        )

    def fetch_event_details(self, event_url: str) -> tuple:
        """Fetch the full description and image from an event detail page
        Returns (description, image_url)
        """
        try:
            if not self.driver:
                return "", None

            # Navigate to event page
            self.driver.get(event_url)
            import time
            time.sleep(2)  # Wait for page load

            # Parse the page
            html = self.driver.page_source
            soup = self.parse_html(html)

            # Extract image
            image_url = None

            # Try og:image first
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                image_url = og_image['content']

            # If no og:image, look for featured image in entry-content
            if not image_url:
                featured_img = soup.find('img', class_=lambda x: x and 'wp-post-image' in x if x else False)
                if featured_img:
                    image_url = featured_img.get('src') or featured_img.get('data-src')

            # If still no image, look for any image in entry content
            if not image_url:
                entry_content = soup.find(class_='entry-content')
                if entry_content:
                    img = entry_content.find('img', src=True)
                    if img:
                        img_src = img.get('src') or img.get('data-src')
                        if img_src and not any(skip in img_src.lower() for skip in ['logo', 'icon', 'avatar']):
                            image_url = img_src

            # Normalize image URL
            if image_url and not image_url.startswith('http'):
                if image_url.startswith('//'):
                    image_url = f'https:{image_url}'
                else:
                    image_url = f"https://artsatthearmory.org{image_url}"

            # Find description in entry-content
            description = ""
            entry_content = soup.find(class_='entry-content')
            if entry_content:
                # Get all paragraph tags, skip the first one (usually date/time)
                paragraphs = entry_content.find_all('p')
                description_parts = []

                for p in paragraphs[1:]:  # Skip first paragraph (date/time info)
                    text = self.clean_text(p.get_text())
                    # Filter out short navigation/footer text
                    if len(text) > 30 and not any(skip in text.lower() for skip in [
                        'get tickets', 'buy tickets', 'register here', 'click here',
                        'for more information', 'visit our website'
                    ]):
                        description_parts.append(text)

                if description_parts:
                    # Take first 3 paragraphs for description
                    full_description = ' '.join(description_parts[:3])
                    description = full_description[:2000] if len(full_description) > 2000 else full_description

            return description, image_url
        except Exception as e:
            return "", None

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Arts at the Armory"""
        html = self.fetch_html(self.source_url)

        # Add extra wait for JavaScript to load
        if self.driver:
            import time
            time.sleep(5)  # Give JS time to render events
            html = self.driver.page_source

        soup = self.parse_html(html)

        events = []
        seen_urls = set()  # Track URLs to avoid duplicates

        # Find all event containers
        event_divs = soup.find_all('div', class_='filterDiv')

        # Limit to reasonable number (20 upcoming events)
        event_divs = event_divs[:20]

        for div in event_divs:
            try:
                # Extract title
                title_elem = div.find(['h3', 'h4'])
                if not title_elem:
                    continue

                title_link = title_elem.find('a')
                if not title_link:
                    continue

                title = self.clean_text(title_link.get_text())
                if len(title) < 3:
                    continue

                # Extract event URL
                event_url = title_link.get('href', '')
                if not event_url or not event_url.startswith('http'):
                    continue

                # Skip if we've already seen this URL (same event in multiple categories)
                if event_url in seen_urls:
                    continue
                seen_urls.add(event_url)

                # Extract date - look for em-event-date element
                date_elem = div.find(class_='em-event-date')
                if not date_elem:
                    continue

                date_text = self.clean_text(date_elem.get_text())
                # Date format: "Tue. Nov. 18, 2025 - Tue. Nov. 25, 2025"
                # We want the first date
                date_text = date_text.split(' - ')[0].strip()

                # Extract time - look for em-event-time element
                time_elem = div.find(class_='em-event-time')
                time_text = ""
                if time_elem:
                    time_text = self.clean_text(time_elem.get_text())
                    # Time format: "6:30 pm - 9:30 pm"
                    # We want the start time
                    time_text = time_text.split(' - ')[0].strip()

                # Combine date and time for parsing
                datetime_str = f"{date_text} {time_text}"

                # Parse the datetime
                try:
                    start_datetime = date_parser.parse(datetime_str, fuzzy=True)
                except:
                    # If parsing fails, try just the date
                    try:
                        start_datetime = date_parser.parse(date_text, fuzzy=True)
                    except:
                        continue

                # Default location (Arts at the Armory)
                venue_name = "Arts at the Armory"
                street_address = "191 Highland Ave"
                city = "Somerville"
                state = "MA"
                zip_code = "02143"

                # Extract cost if available
                cost = None
                body_text = div.get_text().lower()
                if 'free' in body_text and 'admission' in body_text:
                    cost = "Free"
                elif '$' in body_text:
                    # Try to extract dollar amount
                    cost_match = re.search(r'\$(\d+(?:\.\d{2})?)', body_text)
                    if cost_match:
                        cost = f"${cost_match.group(1)}"

                # Fetch description and image from detail page
                description = ""
                image_url = None
                if event_url:
                    detail_desc, image_url = self.fetch_event_details(event_url)
                    if detail_desc and len(detail_desc) > 20:
                        description = detail_desc

                # Fallback description
                if not description or len(description) < 20:
                    description = f"{title} at {venue_name}"

                # Categorize events
                category = self.categorize_event(title, description)

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
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

        return events

    def categorize_event(self, title: str, description: str) -> EventCategory:
        """Categorize event based on keywords"""
        text = f"{title} {description}".lower()

        # Check specific categories
        if any(word in text for word in ['trivia', 'quiz', 'jeopardy', 'bingo']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['concert', 'music', 'band', 'dj', 'live music', 'musical', 'symphony', 'orchestra']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['theater', 'theatre', 'play', 'performance', 'show', 'acting', 'drama']):
            return EventCategory.THEATER
        elif any(word in text for word in ['comedy', 'stand-up', 'comedian', 'improv']):
            return EventCategory.THEATER
        elif any(word in text for word in ['dance', 'ballet', 'dancing']):
            return EventCategory.THEATER
        elif any(word in text for word in ['art', 'paint', 'craft', 'exhibit', 'gallery', 'sculpture']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['film', 'movie', 'cinema', 'screening']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['workshop', 'class', 'lesson', 'learn']):
            return EventCategory.ARTS_CULTURE
        else:
            return EventCategory.ARTS_CULTURE  # Default to arts & culture
