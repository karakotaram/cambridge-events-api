"""Custom scraper for American Repertory Theater (A.R.T.) events"""
import logging
import re
from datetime import datetime
from typing import List, Optional, Dict
from dateutil import parser as date_parser
from bs4 import BeautifulSoup

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class AmericanRepertoryTheaterScraper(BaseScraper):
    """Custom scraper for American Repertory Theater events"""

    def __init__(self):
        super().__init__(
            source_name="American Repertory Theater",
            source_url="https://americanrepertorytheater.org/shows-events/",
            use_selenium=True
        )
        self.base_url = "https://americanrepertorytheater.org"

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from American Repertory Theater"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        import time

        events = []

        # First, get the list of shows from the main page
        html = self.fetch_html(self.source_url)
        if self.driver:
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "a"))
                )
                time.sleep(2)
                html = self.driver.page_source
            except Exception as e:
                logger.warning(f"Timeout on main page: {e}")

        soup = self.parse_html(html)
        show_urls = self._get_show_urls(soup)
        logger.info(f"Found {len(show_urls)} show pages to scrape")

        # Visit each show page and extract events
        for show_url in show_urls:
            try:
                show_events = self._scrape_show_page(show_url)
                events.extend(show_events)
                logger.info(f"Got {len(show_events)} events from {show_url}")
            except Exception as e:
                logger.warning(f"Error scraping {show_url}: {e}")
                continue

        return events

    def _get_show_urls(self, soup: BeautifulSoup) -> List[str]:
        """Extract all show/event page URLs from the main listing"""
        urls = set()

        for link in soup.find_all('a', href=True):
            href = link['href']
            if '/shows-events/' in href and href != '/shows-events/' and href != self.source_url:
                if href.startswith('/'):
                    href = f"{self.base_url}{href}"
                if href.startswith(self.base_url) and href not in urls:
                    if not any(skip in href for skip in ['#', '?', 'category', 'page']):
                        urls.add(href)

        return list(urls)

    def _scrape_show_page(self, url: str) -> List[EventCreate]:
        """Scrape all performances from a single show page"""
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        import time

        events = []

        html = self.fetch_html(url)
        if self.driver:
            try:
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "script"))
                )
                time.sleep(3)
                # Scroll to load calendar
                self.driver.execute_script('window.scrollTo(0, 500);')
                time.sleep(2)
                html = self.driver.page_source
            except Exception as e:
                logger.warning(f"Timeout on {url}: {e}")

        soup = self.parse_html(html)

        # Extract show metadata
        title = self._get_title(soup)
        if not title:
            return []

        description = self._get_description(soup)
        image_url = self._get_image(soup)
        venue_info = self._get_venue(soup, url)

        # Parse performance instances from HTML
        performances = self._extract_performances(soup)

        if performances:
            for perf in performances:
                event = self._create_event(
                    title, description, image_url, venue_info, url, perf
                )
                if event:
                    events.append(event)
            logger.info(f"Found {len(events)} performances for {title}")
        else:
            # Fallback: create single event from page metadata
            event = self._create_fallback_event(
                soup, title, description, image_url, venue_info, url
            )
            if event:
                events.append(event)

        return events

    def _get_title(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract the show title"""
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            title = og_title['content']
            if ' - ' in title:
                title = title.split(' - ')[0]
            if ' | ' in title:
                title = title.split(' | ')[0]
            return self.clean_text(title)

        h1 = soup.find('h1')
        if h1:
            return self.clean_text(h1.get_text())

        return None

    def _get_description(self, soup: BeautifulSoup) -> str:
        """Extract the show description"""
        og_desc = soup.find('meta', property='og:description')
        if og_desc and og_desc.get('content'):
            return self.clean_text(og_desc['content'])

        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc and meta_desc.get('content'):
            return self.clean_text(meta_desc['content'])

        return ""

    def _get_image(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract the show image"""
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            return og_image['content']
        return None

    def _get_venue(self, soup: BeautifulSoup, url: str) -> Dict:
        """Extract venue information"""
        # Default venue for A.R.T. mainstage shows
        venue = {
            "name": "Loeb Drama Center",
            "address": "64 Brattle Street",
            "city": "Cambridge",
            "state": "MA",
            "zip": "02138"
        }

        # Look for explicit venue mentions in specific elements (not full page text)
        # Check the URL for venue hints
        url_lower = url.lower()

        # Only check for alternate venues if the URL suggests it's not a main show
        if 'screening' in url_lower or 'family-matters' in url_lower:
            page_text = soup.get_text().lower()
            if 'brattle theatre' in page_text or 'the brattle' in page_text:
                venue = {
                    "name": "Brattle Theatre",
                    "address": "40 Brattle Street",
                    "city": "Cambridge",
                    "state": "MA",
                    "zip": "02138"
                }
        elif 'choosing-kindness' in url_lower:
            venue = {
                "name": "Cambridge Public Library",
                "address": "449 Broadway",
                "city": "Cambridge",
                "state": "MA",
                "zip": "02138"
            }

        return venue

    def _extract_performances(self, soup: BeautifulSoup) -> List[Dict]:
        """Extract performance data from booking instance elements"""
        performances = []

        # Find all booking instance divs
        instances = soup.find_all('div', class_=re.compile(r'c-booking-instance(?!__)'))

        for instance in instances:
            try:
                perf = {}

                # Get date from class name or content
                class_str = ' '.join(instance.get('class', []))
                class_match = re.search(r'c-booking-instance--(\w+)-(\d+)', class_str)

                # Get the day name and date number
                days_elem = instance.find('div', class_='c-booking-instance__dates--days')
                if days_elem:
                    date_text = days_elem.get_text(strip=True)
                    # Parse "TuesdayDecember 9" format - day name and month are merged
                    months = ['January', 'February', 'March', 'April', 'May', 'June',
                              'July', 'August', 'September', 'October', 'November', 'December']
                    for month in months:
                        if month in date_text:
                            # Extract month and day number
                            match = re.search(rf'{month}\s*(\d+)', date_text)
                            if match:
                                perf['date_str'] = f"{month} {match.group(1)}"
                            break

                # Get time
                time_elem = instance.find('div', class_='c-booking-instance__dates--time')
                if time_elem:
                    perf['time_str'] = time_elem.get_text(strip=True).replace(' ET', '')

                # Get price
                price_elem = instance.find('div', class_='c-booking-instance__price')
                if price_elem:
                    perf['price'] = price_elem.get_text(strip=True)

                # Get availability
                button_elem = instance.find('div', class_='c-booking-instance__button')
                if button_elem:
                    button_text = button_elem.get_text(strip=True)
                    if 'sold out' in button_text.lower():
                        perf['availability'] = 'Sold Out'
                    elif 'limited' in button_text.lower():
                        perf['availability'] = 'Limited'
                    else:
                        perf['availability'] = 'Available'

                # Get booking link
                link = instance.find('a', href=True)
                if link:
                    perf['booking_url'] = link['href']

                if 'date_str' in perf and 'time_str' in perf:
                    performances.append(perf)

            except Exception as e:
                logger.warning(f"Error parsing performance instance: {e}")
                continue

        return performances

    def _create_event(
        self,
        title: str,
        description: str,
        image_url: Optional[str],
        venue_info: Dict,
        source_url: str,
        perf: Dict
    ) -> Optional[EventCreate]:
        """Create an event from performance data"""
        try:
            # Parse datetime
            date_str = perf.get('date_str', '')
            time_str = perf.get('time_str', '7PM')

            # Parse time
            time_match = re.match(r'(\d+)(?::(\d+))?\s*(AM|PM)', time_str, re.I)
            if time_match:
                hour = int(time_match.group(1))
                minute = int(time_match.group(2) or 0)
                if time_match.group(3).upper() == 'PM' and hour < 12:
                    hour += 12
                elif time_match.group(3).upper() == 'AM' and hour == 12:
                    hour = 0
            else:
                hour, minute = 19, 0  # Default 7PM

            # Parse date
            try:
                dt = date_parser.parse(date_str)
                # Handle year
                now = datetime.now()
                if dt.month < now.month or (dt.month == now.month and dt.day < now.day):
                    dt = dt.replace(year=now.year + 1)
                else:
                    dt = dt.replace(year=now.year)

                start_datetime = dt.replace(hour=hour, minute=minute, second=0)
            except Exception as e:
                logger.warning(f"Could not parse date '{date_str}': {e}")
                return None

            # Skip past events
            if start_datetime < datetime.now():
                return None

            # Build cost string
            cost = None
            if perf.get('price'):
                cost = perf['price']
            if perf.get('availability') == 'Sold Out':
                cost = 'Sold Out'

            # Format time for title
            time_display = start_datetime.strftime('%I:%M %p').lstrip('0').replace(':00 ', ' ')
            event_title = f"{title} - {time_display}"

            booking_url = perf.get('booking_url', source_url)
            if booking_url and not booking_url.startswith('http'):
                booking_url = f"{self.base_url}{booking_url}"

            return EventCreate(
                title=event_title[:200],
                description=description[:2000] if description else f"{title} at American Repertory Theater",
                start_datetime=start_datetime,
                source_url=booking_url or source_url,
                source_name=self.source_name,
                venue_name=venue_info["name"],
                street_address=venue_info["address"],
                city=venue_info["city"],
                state=venue_info["state"],
                zip_code=venue_info["zip"],
                category=self._categorize(title, description),
                cost=cost,
                image_url=image_url
            )

        except Exception as e:
            logger.warning(f"Error creating event: {e}")
            return None

    def _create_fallback_event(
        self,
        soup: BeautifulSoup,
        title: str,
        description: str,
        image_url: Optional[str],
        venue_info: Dict,
        source_url: str
    ) -> Optional[EventCreate]:
        """Create a single event from page metadata (fallback)"""
        page_text = soup.get_text()

        # Look for date
        date_match = re.search(
            r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}',
            page_text,
            re.IGNORECASE
        )

        if not date_match:
            return None

        try:
            start_datetime = date_parser.parse(date_match.group(), fuzzy=True)
            now = datetime.now()
            if start_datetime.year < now.year:
                start_datetime = start_datetime.replace(year=now.year)
            if start_datetime < now:
                start_datetime = start_datetime.replace(year=now.year + 1)

            # Look for time
            time_match = re.search(r'(\d{1,2})\s*(pm|am)', page_text, re.IGNORECASE)
            if time_match:
                hour = int(time_match.group(1))
                if 'pm' in time_match.group(2).lower() and hour < 12:
                    hour += 12
                start_datetime = start_datetime.replace(hour=hour, minute=0)
            else:
                start_datetime = start_datetime.replace(hour=19, minute=0)

            if start_datetime < datetime.now():
                return None

            return EventCreate(
                title=title[:200],
                description=description[:2000] if description else f"{title} at American Repertory Theater",
                start_datetime=start_datetime,
                source_url=source_url,
                source_name=self.source_name,
                venue_name=venue_info["name"],
                street_address=venue_info["address"],
                city=venue_info["city"],
                state=venue_info["state"],
                zip_code=venue_info["zip"],
                category=self._categorize(title, description),
                image_url=image_url
            )

        except Exception as e:
            logger.warning(f"Error creating fallback event: {e}")
            return None

    def _categorize(self, title: str, description: str) -> EventCategory:
        """Categorize the event"""
        text = f"{title} {description}".lower()

        if any(word in text for word in ['workshop', 'class', 'conversation', 'panel', 'discussion']):
            return EventCategory.EDUCATION
        elif any(word in text for word in ['screening', 'film', 'movie']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['gala', 'fundraiser', 'benefit']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['concert', 'music', 'orchestra', 'symphony']):
            return EventCategory.MUSIC
        else:
            return EventCategory.THEATER
