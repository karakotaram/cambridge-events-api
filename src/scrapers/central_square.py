"""Scraper for Central Square Theater events"""
import re
import json
import logging
from datetime import datetime, timedelta
from typing import List, Optional
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from src.models.event import EventCreate, EventCategory
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)


class CentralSquareTheaterScraper(BaseScraper):
    """Scraper for Central Square Theater"""

    def __init__(self):
        super().__init__(
            source_name="Central Square Theater",
            source_url="https://www.centralsquaretheater.org/calendar/",
            use_selenium=True  # Calendar uses AJAX to load events
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Central Square Theater calendar"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        import time

        events = []
        seen_events = set()

        try:
            logger.info(f"Fetching {self.source_url}")
            html = self.fetch_html(self.source_url)

            if not self.driver:
                return events

            # Scrape multiple months (current + next 3 months)
            for month_num in range(4):
                try:
                    # Wait for calendar events to load
                    WebDriverWait(self.driver, 15).until(
                        EC.presence_of_element_located((By.CLASS_NAME, "eventon_list_event"))
                    )
                    time.sleep(2)

                    html = self.driver.page_source
                    soup = BeautifulSoup(html, 'html.parser')

                    # Find all event items in the EventON calendar
                    event_items = soup.find_all('div', class_='eventon_list_event')
                    logger.info(f"Found {len(event_items)} event items in month {month_num + 1}")

                    for item in event_items:
                        try:
                            event = self._parse_calendar_event(item, seen_events)
                            if event:
                                events.append(event)
                        except Exception as e:
                            logger.warning(f"Error parsing event: {e}")
                            continue

                    # Click next month button if not last iteration
                    if month_num < 3:
                        next_btn = self.driver.find_element(By.ID, "evcal_next")
                        next_btn.click()
                        time.sleep(2)  # Wait for AJAX to load next month

                except Exception as e:
                    logger.warning(f"Error scraping month {month_num + 1}: {e}")
                    break

            logger.info(f"Successfully parsed {len(events)} total events")

        except Exception as e:
            logger.error(f"Error scraping Central Square Theater: {e}")

        return events

    def _parse_calendar_event(self, item, seen_events: set) -> Optional[EventCreate]:
        """Parse a single event from the EventON calendar"""
        try:
            # Get event title
            title_elem = item.find('span', class_='evcal_event_title')
            if not title_elem:
                return None
            title = self.clean_text(title_elem.get_text())
            if not title or len(title) < 3:
                return None

            # Get event URL
            link_elem = item.find('a', class_='evcal_list_a')
            event_url = link_elem.get('href') if link_elem else self.source_url

            # Get date/time from data-time attribute (format: "start_unix-end_unix")
            time_data = item.get('data-time')
            if not time_data:
                return None

            try:
                start_unix = time_data.split('-')[0]
                start_datetime = datetime.fromtimestamp(int(start_unix))
            except (ValueError, TypeError, IndexError):
                return None

            # Create unique key to avoid duplicates
            event_key = f"{title}_{start_datetime.isoformat()}"
            if event_key in seen_events:
                return None
            seen_events.add(event_key)

            # Try to get description from JSON-LD schema
            description = f"{title} at Central Square Theater"
            script_elem = item.find('script', type='application/ld+json')
            if script_elem:
                try:
                    schema_data = json.loads(script_elem.string)
                    if schema_data.get('description'):
                        description = self.clean_text(schema_data['description'])
                except:
                    pass

            # Get image from schema or img element
            image_url = None
            if script_elem:
                try:
                    schema_data = json.loads(script_elem.string)
                    if schema_data.get('image'):
                        image_url = schema_data['image']
                except:
                    pass
            if not image_url:
                img_elem = item.find('img')
                if img_elem:
                    image_url = img_elem.get('src') or img_elem.get('data-src')

            return EventCreate(
                title=title,
                description=description[:2000] if description else f"{title} at Central Square Theater",
                start_datetime=start_datetime,
                venue_name="Central Square Theater",
                street_address="450 Massachusetts Avenue",
                city="Cambridge",
                state="MA",
                zip_code="02139",
                category=EventCategory.THEATER,
                image_url=image_url,
                source_name=self.source_name,
                source_url=event_url
            )

        except Exception as e:
            logger.warning(f"Error parsing calendar event: {e}")
            return None

    def _scrape_show_detail(self, show_url: str) -> List[EventCreate]:
        """Scrape a single show detail page and create an event for the show"""
        events = []

        try:
            logger.info(f"Fetching show detail page: {show_url}")
            html = self.fetch_html(show_url)

            if not html:
                logger.warning(f"Failed to fetch {show_url}")
                return events

            soup = BeautifulSoup(html, 'html.parser')

            # Get show title - look for h1 with class "header large" (show title, not site title)
            title = None
            title_elem = soup.find('h1', class_='header large')
            if title_elem:
                title = self.clean_text(title_elem.get_text())

            if not title:
                logger.warning(f"No title found on {show_url}")
                return events

            # Get show run dates text
            run_dates_text = None
            run_dates_elem = soup.find('span', class_='show-run-dates')
            if run_dates_elem:
                run_dates_text = run_dates_elem.get_text(strip=True)

            # Get description and prepend run dates
            description = self._extract_show_description(soup)
            if run_dates_text:
                description = f"Show runs: {run_dates_text}. {description}" if description else f"Show runs: {run_dates_text}"

            # Get image URL (og:image or featured image)
            image_url = self._extract_show_image(soup)

            # Get cost/price
            cost = self._extract_show_cost(soup)

            # Get performance start date
            performance_dates = self._extract_performance_dates(soup)

            if not performance_dates:
                logger.warning(f"No performance dates found for {title}")
                return events

            # Create a single event for the show (using the start date of the run)
            event = EventCreate(
                title=title,
                description=description,
                start_datetime=performance_dates[0],  # Use first (start) date
                venue_name="Central Square Theater",
                street_address="450 Massachusetts Avenue",
                city="Cambridge",
                state="MA",
                zip_code="02139",
                category=EventCategory.THEATER,
                cost=cost,
                image_url=image_url,
                source_name=self.source_name,
                source_url=show_url
            )
            events.append(event)

            logger.info(f"Created event for {title} (runs {run_dates_text})")

        except Exception as e:
            logger.error(f"Error scraping show detail {show_url}: {e}", exc_info=True)

        return events

    def _extract_show_description(self, soup: BeautifulSoup) -> str:
        """Extract show description from detail page (main synopsis only)"""
        try:
            # Look for content div
            content_div = soup.find('div', class_=lambda x: x and 'content' in x.lower() if x else False)
            if content_div:
                # Get all paragraphs
                paragraphs = content_div.find_all('p')
                if paragraphs:
                    # Collect paragraph text
                    desc_parts = []
                    for p in paragraphs:
                        text = p.get_text(strip=True)
                        # Stop at common delimiters that indicate end of synopsis
                        if any(marker in text.lower() for marker in [
                            'directed by', 'season tickets', 'buy tickets',
                            'student matinees', 'interested in bringing',
                            'central conversations', 'join us'
                        ]):
                            break
                        if text:
                            desc_parts.append(text)

                    if desc_parts:
                        return self.clean_text(' '.join(desc_parts))

            # Try main content area
            main_content = soup.find('main') or soup.find('article')
            if main_content:
                paragraphs = main_content.find_all('p')
                if paragraphs:
                    desc_parts = []
                    for p in paragraphs[:5]:  # Check first 5 paragraphs
                        text = p.get_text(strip=True)
                        if any(marker in text.lower() for marker in [
                            'directed by', 'season tickets', 'buy tickets',
                            'student matinees', 'interested in bringing'
                        ]):
                            break
                        if text:
                            desc_parts.append(text)

                    if desc_parts:
                        return self.clean_text(' '.join(desc_parts))

            return ""

        except Exception as e:
            logger.error(f"Error extracting description: {e}")
            return ""

    def _extract_show_image(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract image URL from show detail page"""
        try:
            # Look for og:image meta tag
            og_image = soup.find('meta', property='og:image')
            if og_image and og_image.get('content'):
                return og_image['content']

            # Look for featured image
            featured_img = soup.find('img', class_=lambda x: x and ('featured' in x.lower() or 'hero' in x.lower()) if x else False)
            if featured_img:
                img_url = featured_img.get('src') or featured_img.get('data-src')
                if img_url:
                    if not img_url.startswith('http'):
                        img_url = f"https://www.centralsquaretheater.org{img_url}"
                    return img_url

            # Try first image in content
            content_div = soup.find('div', class_=lambda x: x and 'content' in x.lower() if x else False)
            if content_div:
                img = content_div.find('img')
                if img:
                    img_url = img.get('src') or img.get('data-src')
                    if img_url:
                        if not img_url.startswith('http'):
                            img_url = f"https://www.centralsquaretheater.org{img_url}"
                        return img_url

            return None

        except Exception as e:
            logger.error(f"Error extracting image: {e}")
            return None

    def _extract_show_cost(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract cost/price from show detail page"""
        try:
            # Look for ticket/price info in text
            text = soup.get_text()

            # Look for price patterns
            price_patterns = [
                r'Tickets?:?\s*\$(\d+(?:\.\d{2})?)',
                r'\$(\d+(?:\.\d{2})?)\s*(?:tickets?|admission)',
                r'Price:?\s*\$(\d+(?:\.\d{2})?)',
            ]

            for pattern in price_patterns:
                match = re.search(pattern, text, re.IGNORECASE)
                if match:
                    return f"${match.group(1)}"

            # Look for price in specific elements
            price_elem = soup.find(class_=lambda x: x and ('price' in x.lower() or 'ticket' in x.lower()) if x else False)
            if price_elem:
                price_text = price_elem.get_text()
                price_match = re.search(r'\$\d+(?:\.\d{2})?', price_text)
                if price_match:
                    return price_match.group(0)

            return None

        except Exception as e:
            logger.error(f"Error extracting cost: {e}")
            return None

    def _extract_performance_dates(self, soup: BeautifulSoup) -> List[datetime]:
        """Extract show run dates and return the start date"""
        dates = []

        try:
            # Look for show-run-dates span
            run_dates_elem = soup.find('span', class_='show-run-dates')
            if run_dates_elem:
                run_dates_text = run_dates_elem.get_text(strip=True)
                # Parse date range like "September 11 - October 5, 2025"
                # Extract the start date
                match = re.match(r'([A-Z][a-z]+\s+\d{1,2})\s*-\s*[A-Z][a-z]+\s+\d{1,2},\s+(\d{4})', run_dates_text)
                if match:
                    start_date_str = f"{match.group(1)}, {match.group(2)}"
                    try:
                        # Parse as start date with default time of 7:30pm (common theater time)
                        dt = date_parser.parse(f"{start_date_str} 7:30 PM")
                        dates.append(dt)
                        logger.info(f"Extracted show run start date: {dt} from '{run_dates_text}'")
                    except Exception as e:
                        logger.warning(f"Failed to parse start date '{start_date_str}': {e}")

        except Exception as e:
            logger.error(f"Error extracting performance dates: {e}")

        return sorted(dates)
