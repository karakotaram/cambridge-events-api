"""Custom scraper for American Repertory Theater (A.R.T.) events"""
import logging
import json
import re
import requests
from datetime import datetime
from typing import List, Optional, Dict
from dateutil import parser as date_parser
from bs4 import BeautifulSoup

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

# Curated show descriptions from A.R.T. website
SHOW_DESCRIPTIONS = {
    "wonder": "A world-premiere musical adaptation of R.J. Palacio's novel and the Lionsgate/Mandeville film. The story follows Auggie Pullman, who has been homeschooled due to a facial difference and must now navigate attending school. Featuring music by Grammy Award-winning duo A Great Big World, this uplifting new musical celebrates empathy, resilience, and the power of choosing kindness.",
    "black swan": "Tony Award winner Sonya Tayeh brings a bold new musical adaptation of the 2010 psychological thriller to life on stage, featuring a score by Dave Malloy and book by Jen Silverman. This electrifying production explores ambition, rivalry, and the dark side of artistic perfection.",
    "cultivating compassion": "A panel discussion on strategies for bullying prevention, presented in connection with the Wonder musical. Educators and experts share practical approaches to fostering empathy and kindness in schools and communities.",
    "family matters": "A special film screening series exploring themes of family, acceptance, and belonging, presented in connection with the Wonder musical at A.R.T.",
    "choosing kindness": "A special screening of the film Wonder, followed by discussion about the power of choosing kindness and empathy in our daily lives.",
    "community workshops": "Interactive workshops exploring themes from Wonder, designed to foster empathy, creativity, and community connection through theatrical activities.",
    "four dimensions": "An immersive art experience exploring the intersection of visual arts and theater, featuring a stunning mural installation at Harvard's ArtLab.",
    "gala": "The annual A.R.T. Gala celebrates the theater's mission to expand the boundaries of theater, featuring special performances and exclusive experiences.",
}

# Curated high-quality images from A.R.T. S3 bucket (1600x1000 resolution)
SHOW_IMAGES = {
    "wonder": "https://american-rep-assets.s3.amazonaws.com/wp-content/uploads/2025/09/2526-Wonder_Web-General-listingimage1-1600x1000.jpg",
    "black swan": "https://american-rep-assets.s3.amazonaws.com/wp-content/uploads/2025/06/2526-Season-Artwork-Final-EmailWeb-Web-BlackSwan_NoTitle-1600x1000.jpg",
    "cultivating compassion": "https://american-rep-assets.s3.amazonaws.com/wp-content/uploads/2025/11/2526-Wonder-Web-CultivatingCompassion-WebCard2-1600x1000.jpg",
    "family matters": "https://american-rep-assets.s3.amazonaws.com/wp-content/uploads/2025/12/2526-Wonder-Web-AssociatedEvents-FamilyMatters-WebCard-1600x1000.jpg",
    "choosing kindness": "https://american-rep-assets.s3.amazonaws.com/wp-content/uploads/2025/11/2526-Wonder-Web-Screening-WebCard-1600x1000.jpg",
    "community workshops": "https://american-rep-assets.s3.amazonaws.com/wp-content/uploads/2025/11/2526-Wonder-Web-CommunityWorkshops-WebCard-1600x1000.jpg",
    "four dimensions": "https://american-rep-assets.s3.amazonaws.com/wp-content/uploads/2025/07/Mural-WebHeader_Problak-1600x1000.jpg",
    "gala": "https://american-rep-assets.s3.amazonaws.com/wp-content/uploads/2025/11/2526-Devo-Gala-Web-Card-1600x1000.jpg",
}


class AmericanRepertoryTheaterScraper(BaseScraper):
    """Custom scraper for American Repertory Theater events"""

    def __init__(self):
        super().__init__(
            source_name="American Repertory Theater",
            source_url="https://americanrepertorytheater.org/shows-events/",
            use_selenium=True  # Page uses JavaScript for calendar rendering
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from American Repertory Theater"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        import time

        html = self.fetch_html(self.source_url)

        if self.driver:
            try:
                # Wait for page content to load
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.TAG_NAME, "script"))
                )
                time.sleep(3)
                html = self.driver.page_source
            except Exception as e:
                logger.warning(f"Timeout waiting for page: {e}")

        soup = self.parse_html(html)
        events = []
        seen_events = set()

        # Look for the attendable JavaScript object in script tags
        scripts = soup.find_all('script')
        attendable_data = None

        for script in scripts:
            if script.string and 'attendable' in script.string:
                # Try to extract the attendable JSON object
                match = re.search(r'var\s+attendable\s*=\s*(\{.*?\});', script.string, re.DOTALL)
                if match:
                    try:
                        attendable_data = json.loads(match.group(1))
                        logger.info("Found attendable data object")
                        break
                    except json.JSONDecodeError:
                        # Try a more lenient extraction
                        pass

                # Also try to find calendarInstances directly
                match = re.search(r'"calendarInstances"\s*:\s*(\[.*?\])', script.string, re.DOTALL)
                if match:
                    try:
                        instances = json.loads(match.group(1))
                        attendable_data = {'calendarInstances': instances}
                        logger.info(f"Found {len(instances)} calendar instances")
                        break
                    except json.JSONDecodeError:
                        pass

        if attendable_data and 'calendarInstances' in attendable_data:
            for instance in attendable_data['calendarInstances']:
                try:
                    event = self._parse_calendar_instance(instance, seen_events)
                    if event:
                        events.append(event)
                except Exception as e:
                    logger.warning(f"Error parsing calendar instance: {e}")
                    continue

        # Also try to parse event cards from HTML as fallback
        if not events:
            logger.info("No events from JSON, trying HTML parsing")
            event_cards = soup.find_all(['div', 'article'], class_=lambda x: x and ('production' in x.lower() or 'event' in x.lower() or 'show' in x.lower()) if x else False)
            logger.info(f"Found {len(event_cards)} event card elements")

            for card in event_cards:
                try:
                    event = self._parse_event_card(card, seen_events)
                    if event:
                        events.append(event)
                except Exception as e:
                    logger.warning(f"Error parsing event card: {e}")
                    continue

        return events

    def _parse_calendar_instance(self, instance: dict, seen_events: set) -> EventCreate:
        """Parse a calendar instance from the attendable data"""
        try:
            # Get title from instance or parent
            title = instance.get('post_title', '')
            if not title:
                parent = instance.get('parent', {})
                title = parent.get('post_title', '')

            if not title or len(title) < 3:
                return None

            # Parse date
            post_date = instance.get('post_date', '')
            if not post_date:
                return None

            try:
                # Format: "YYYY-MM-DD HH:MM:SS"
                start_datetime = datetime.strptime(post_date, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                try:
                    start_datetime = date_parser.parse(post_date)
                except Exception as e:
                    logger.warning(f"Failed to parse date '{post_date}': {e}")
                    return None

            # Default to 7:30 PM if no time was parsed (midnight means no time found)
            if start_datetime.hour == 0 and start_datetime.minute == 0:
                start_datetime = start_datetime.replace(hour=19, minute=30)

            # Skip past events
            if start_datetime < datetime.now():
                return None

            # Create unique key
            event_key = f"{title}_{start_datetime.date()}_{start_datetime.hour}"
            if event_key in seen_events:
                return None
            seen_events.add(event_key)

            # Get description from parent
            description = ""
            parent = instance.get('parent', {})
            if parent:
                description = parent.get('post_excerpt', '') or parent.get('post_content', '')
            if not description:
                description = f"{title} at American Repertory Theater"
            description = self.clean_text(description)

            # Get URL
            event_url = instance.get('bookingURL', '') or self.source_url
            if not event_url.startswith('http'):
                event_url = self.source_url

            # Get availability/cost info
            cost = None
            availability = instance.get('availibility', '')  # Note: typo in source data
            if availability:
                if 'sold out' in availability.lower():
                    cost = "Sold Out"
                elif 'limited' in availability.lower():
                    cost = "Limited Availability"

            # Get image from parent
            image_url = None
            if parent:
                image_url = parent.get('featured_image', '') or parent.get('thumbnail', '')

            category = self.categorize_event(title, description)

            return EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start_datetime,
                source_url=event_url,
                source_name=self.source_name,
                venue_name="American Repertory Theater",
                street_address="64 Brattle Street",
                city="Cambridge",
                state="MA",
                zip_code="02138",
                category=category,
                cost=cost,
                image_url=image_url
            )

        except Exception as e:
            logger.warning(f"Error parsing calendar instance: {e}")
            return None

    def _parse_event_card(self, card, seen_events: set) -> EventCreate:
        """Parse an event from an HTML card element"""
        try:
            # Get title
            title_elem = card.find(['h2', 'h3', 'h4', 'a'], class_=lambda x: x and ('title' in x.lower() or 'name' in x.lower()) if x else False)
            if not title_elem:
                title_elem = card.find(['h2', 'h3', 'h4'])

            if not title_elem:
                return None

            title = self.clean_text(title_elem.get_text())
            if not title or len(title) < 3:
                return None

            # Get URL
            event_url = self.source_url
            link = card.find('a', href=True)
            if link:
                event_url = link.get('href')
                if not event_url.startswith('http'):
                    event_url = f"https://americanrepertorytheater.org{event_url}"

            # Get date/time - look for date patterns
            card_text = card.get_text()
            date_match = re.search(
                r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}',
                card_text,
                re.IGNORECASE
            )

            if not date_match:
                # Try to find date range like "Dec 9 - Feb 8"
                date_match = re.search(
                    r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2}',
                    card_text,
                    re.IGNORECASE
                )

            if not date_match:
                return None

            date_str = date_match.group()

            try:
                start_datetime = date_parser.parse(date_str, fuzzy=True)
                # Ensure year is set correctly
                if start_datetime.year < datetime.now().year:
                    start_datetime = start_datetime.replace(year=datetime.now().year)
                    if start_datetime < datetime.now():
                        start_datetime = start_datetime.replace(year=datetime.now().year + 1)
                # Default to 7:30 PM if no time was parsed (midnight means no time found)
                if start_datetime.hour == 0 and start_datetime.minute == 0:
                    start_datetime = start_datetime.replace(hour=19, minute=30)
            except Exception as e:
                return None

            # Check for duplicates
            event_key = f"{title}_{start_datetime.date()}"
            if event_key in seen_events:
                return None
            seen_events.add(event_key)

            # Get description - first check curated descriptions
            description = None
            title_slug = title.lower().replace(' ', '-').replace("'", "")
            for slug, desc in SHOW_DESCRIPTIONS.items():
                if slug in title_slug or slug in event_url.lower():
                    description = desc
                    break

            # Fallback to card description
            if not description:
                desc_elem = card.find(['p', 'div'], class_=lambda x: x and ('desc' in x.lower() or 'excerpt' in x.lower() or 'summary' in x.lower()) if x else False)
                if desc_elem:
                    description = self.clean_text(desc_elem.get_text())

            if not description:
                description = f"{title} at American Repertory Theater"

            # Get image - check multiple sources
            image_url = None
            img = card.find('img')
            if img:
                # Check various image attributes
                image_url = img.get('src') or img.get('data-src') or img.get('data-lazy-src') or img.get('srcset', '').split()[0] if img.get('srcset') else None

            # Also check for background image in style
            if not image_url:
                style_elem = card.find(style=lambda x: x and 'background' in x if x else False)
                if style_elem:
                    style = style_elem.get('style', '')
                    url_match = re.search(r'url\(["\']?([^"\')\s]+)["\']?\)', style)
                    if url_match:
                        image_url = url_match.group(1)

            # Also look for picture/source elements
            if not image_url:
                source = card.find('source')
                if source:
                    srcset = source.get('srcset', '')
                    if srcset:
                        image_url = srcset.split()[0]

            # Clean up image URL
            if image_url:
                if image_url.startswith('//'):
                    image_url = f"https:{image_url}"
                elif not image_url.startswith('http'):
                    image_url = f"https://americanrepertorytheater.org{image_url}"
                # Skip placeholder/data images
                if 'data:image' in image_url:
                    image_url = None

            # Always prefer curated high-res images over scraped thumbnails
            title_lower = title.lower()
            for key, img_url in SHOW_IMAGES.items():
                if key in title_lower:
                    image_url = img_url
                    break

            category = self.categorize_event(title, description)

            return EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start_datetime,
                source_url=event_url,
                source_name=self.source_name,
                venue_name="American Repertory Theater",
                street_address="64 Brattle Street",
                city="Cambridge",
                state="MA",
                zip_code="02138",
                category=category,
                image_url=image_url
            )

        except Exception as e:
            logger.warning(f"Error parsing event card: {e}")
            return None

    def categorize_event(self, title: str, description: str) -> EventCategory:
        """Categorize event based on keywords"""
        text = f"{title} {description}".lower()

        if any(word in text for word in ['concert', 'music', 'orchestra', 'symphony']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['play', 'musical', 'theater', 'theatre', 'drama', 'comedy', 'performance']):
            return EventCategory.THEATER
        elif any(word in text for word in ['dance', 'ballet']):
            return EventCategory.THEATER
        elif any(word in text for word in ['workshop', 'class', 'lecture', 'talk']):
            return EventCategory.ARTS_CULTURE
        else:
            return EventCategory.THEATER  # Default for A.R.T.
