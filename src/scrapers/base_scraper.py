"""Base scraper class for all event scrapers"""
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup
import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from src.models.event import EventCreate

logger = logging.getLogger(__name__)


class BaseScraper(ABC):
    """Abstract base class for all event scrapers"""

    def __init__(self, source_name: str, source_url: str, use_selenium: bool = False):
        self.source_name = source_name
        self.source_url = source_url
        self.use_selenium = use_selenium
        self.driver: Optional[webdriver.Chrome] = None

    def setup_selenium(self):
        """Initialize Selenium WebDriver with headless Chrome"""
        if self.driver is None:
            options = Options()
            options.add_argument('--headless=new')  # New headless mode (more stable)
            options.add_argument('--no-sandbox')
            options.add_argument('--disable-dev-shm-usage')
            options.add_argument('--disable-gpu')
            options.add_argument('--window-size=1920,1080')
            options.add_argument('--disable-extensions')
            options.add_argument('--disable-software-rasterizer')
            # Additional stability options for CI environments
            options.add_argument('--disable-setuid-sandbox')
            options.add_argument('--disable-features=VizDisplayCompositor')
            options.add_argument('--remote-debugging-port=9222')
            options.add_argument('--disable-background-networking')
            options.add_argument('--disable-default-apps')
            options.add_argument('--disable-sync')
            options.add_argument('--disable-translate')
            options.add_argument('--metrics-recording-only')
            options.add_argument('--mute-audio')
            options.add_argument('--no-first-run')
            options.add_argument('--safebrowsing-disable-auto-update')
            # Memory constraints
            options.add_argument('--js-flags=--max-old-space-size=512')
            self.driver = webdriver.Chrome(options=options)
            self.driver.set_page_load_timeout(60)  # 60 second page load timeout
            logger.info(f"Selenium WebDriver initialized for {self.source_name}")

    def cleanup_selenium(self):
        """Close Selenium WebDriver"""
        if self.driver:
            self.driver.quit()
            self.driver = None
            logger.info(f"Selenium WebDriver closed for {self.source_name}")

    def fetch_html(self, url: str) -> str:
        """Fetch HTML content from URL"""
        try:
            if self.use_selenium:
                self.setup_selenium()
                self.driver.get(url)
                # Wait for page to load
                WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.TAG_NAME, "body"))
                )
                return self.driver.page_source
            else:
                response = requests.get(url, timeout=10, headers={
                    'User-Agent': 'Mozilla/5.0 (compatible; CambridgeEventScraper/1.0)'
                })
                response.raise_for_status()
                return response.text
        except Exception as e:
            logger.error(f"Error fetching {url}: {str(e)}")
            raise

    @abstractmethod
    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from source - must be implemented by subclasses"""
        pass

    def parse_html(self, html: str) -> BeautifulSoup:
        """Parse HTML into BeautifulSoup object"""
        return BeautifulSoup(html, 'html.parser')

    def clean_text(self, text: str) -> str:
        """Clean and normalize text"""
        if not text:
            return ""
        return ' '.join(text.strip().split())

    def extract_image_url(self, soup: BeautifulSoup, base_url: str = None) -> Optional[str]:
        """
        Extract the best image URL from a page.
        Tries multiple strategies: og:image meta tag, main image tags, etc.
        """
        # Strategy 1: Open Graph image (most reliable for event pages)
        og_image = soup.find('meta', property='og:image')
        if og_image and og_image.get('content'):
            img_url = og_image['content']
            return self._normalize_image_url(img_url, base_url)

        # Strategy 2: Twitter card image
        twitter_image = soup.find('meta', attrs={'name': 'twitter:image'})
        if twitter_image and twitter_image.get('content'):
            img_url = twitter_image['content']
            return self._normalize_image_url(img_url, base_url)

        # Strategy 3: Main content image (look for large images)
        main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=re.compile(r'content|event|detail', re.I))
        if main_content:
            img = main_content.find('img', src=True)
            if img:
                img_url = img.get('src') or img.get('data-src')
                if img_url and self._is_valid_event_image(img_url):
                    return self._normalize_image_url(img_url, base_url)

        # Strategy 4: First large image on page
        for img in soup.find_all('img', src=True)[:10]:
            img_url = img.get('src') or img.get('data-src')
            if img_url and self._is_valid_event_image(img_url):
                return self._normalize_image_url(img_url, base_url)

        return None

    def _normalize_image_url(self, url: str, base_url: str = None) -> str:
        """Normalize image URL to absolute URL"""
        if not url:
            return None

        # Already absolute URL
        if url.startswith('http://') or url.startswith('https://'):
            return url

        # Protocol-relative URL
        if url.startswith('//'):
            return f'https:{url}'

        # Relative URL - need base
        if base_url:
            return urljoin(base_url, url)

        return None

    def _is_valid_event_image(self, url: str) -> bool:
        """Check if URL appears to be a valid event image (not icon/logo/etc)"""
        if not url:
            return False

        url_lower = url.lower()

        # Skip common non-event images
        skip_patterns = [
            'logo', 'icon', 'favicon', 'sprite', 'placeholder',
            'avatar', 'profile', 'banner', 'header', 'footer',
            'loading', 'spinner', 'pixel', '1x1', 'spacer',
            'button', 'arrow', 'social', 'facebook', 'twitter',
            'instagram', 'youtube', 'linkedin', 'pinterest'
        ]

        for pattern in skip_patterns:
            if pattern in url_lower:
                return False

        # Check for common image extensions
        valid_extensions = ['.jpg', '.jpeg', '.png', '.webp', '.gif']
        has_valid_ext = any(ext in url_lower for ext in valid_extensions)

        # Also accept URLs that might be dynamic image services
        is_dynamic = any(service in url_lower for service in ['unsplash', 'cloudinary', 'imgix', 'cdn'])

        return has_valid_ext or is_dynamic or '?' in url  # Query params often indicate dynamic images

    def run(self) -> List[EventCreate]:
        """Execute the scraper and return events"""
        try:
            logger.info(f"Starting scrape of {self.source_name}")
            events = self.scrape_events()
            logger.info(f"Successfully scraped {len(events)} events from {self.source_name}")
            return events
        except Exception as e:
            logger.error(f"Failed to scrape {self.source_name}: {str(e)}")
            raise
        finally:
            if self.use_selenium:
                self.cleanup_selenium()


class GenericScraper(BaseScraper):
    """Generic scraper for sources without custom logic"""

    def scrape_events(self) -> List[EventCreate]:
        """Generic event scraping logic"""
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        events = []
        # Look for common event patterns
        event_elements = soup.find_all(['article', 'div'], class_=lambda x: x and any(
            keyword in x.lower() for keyword in ['event', 'calendar', 'listing']
        ))

        for element in event_elements:
            try:
                # Extract basic event information
                title_elem = element.find(['h1', 'h2', 'h3', 'h4'])
                if not title_elem:
                    continue

                title = self.clean_text(title_elem.get_text())
                if not title or len(title) < 5:
                    continue

                # Look for description
                desc_elem = element.find(['p', 'div'], class_=lambda x: x and 'desc' in x.lower() if x else False)
                description = self.clean_text(desc_elem.get_text()) if desc_elem else title

                # Look for date/time information
                # This is a simplified version - real implementation would need more sophisticated parsing
                date_elem = element.find(['time', 'span'], class_=lambda x: x and 'date' in x.lower() if x else False)
                date_str = self.clean_text(date_elem.get_text()) if date_elem else None

                if not date_str:
                    continue

                # Simplified date parsing - would need proper implementation
                try:
                    start_datetime = datetime.now()  # Placeholder
                except:
                    continue

                # Look for location
                location_elem = element.find(['span', 'div'], class_=lambda x: x and 'location' in x.lower() if x else False)
                venue_name = self.clean_text(location_elem.get_text()) if location_elem else None

                # Create event
                event = EventCreate(
                    title=title,
                    description=description[:2000],
                    start_datetime=start_datetime,
                    source_url=self.source_url,
                    source_name=self.source_name,
                    venue_name=venue_name,
                    city="Cambridge",
                    state="MA"
                )
                events.append(event)

            except Exception as e:
                logger.warning(f"Failed to parse event element: {str(e)}")
                continue

        return events
