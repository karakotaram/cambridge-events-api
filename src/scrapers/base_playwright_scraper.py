"""Base Playwright scraper class for all event scrapers"""
import logging
import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import List, Optional
from urllib.parse import urljoin
from bs4 import BeautifulSoup

from src.models.event import EventCreate

logger = logging.getLogger(__name__)


class BasePlaywrightScraper(ABC):
    """
    Abstract base class for Playwright-based event scrapers.

    Advantages over Selenium:
    - Faster execution with built-in auto-waiting
    - More reliable element selection
    - Lower memory footprint
    - Better CI/CD compatibility
    """

    def __init__(self, source_name: str, source_url: str, user_agent: Optional[str] = None):
        """user_agent defaults to the browser's own.

        Do not spoof it without a reason. A UA claiming macOS on a browser whose
        client hints say Linux is a *contradiction*, and bot protection reads it
        as one — Porter Square Books returns 403 for the spoofed UA and 200 for
        the browser's own, from the same headless Chromium.
        """
        self.source_name = source_name
        self.source_url = source_url
        self.user_agent = user_agent
        self._browser = None
        self._context = None
        self._page = None

    def setup_browser(self):
        """Initialize Playwright browser with stealth settings"""
        if self._browser is None:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--disable-extensions',
                    '--disable-software-rasterizer',
                ]
            )
            context_options = {}
            if self.user_agent:
                context_options['user_agent'] = self.user_agent
            self._context = self._browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                java_script_enabled=True,
                **context_options,
                bypass_csp=True,
                extra_http_headers={
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'DNT': '1',
                }
            )
            # Block unnecessary resources for faster loading
            self._context.route("**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2}", lambda route: route.abort())
            self._page = self._context.new_page()
            logger.info(f"Playwright browser initialized for {self.source_name}")

    def cleanup_browser(self):
        """Close Playwright browser and cleanup resources"""
        if self._page:
            self._page.close()
            self._page = None
        if self._context:
            self._context.close()
            self._context = None
        if self._browser:
            self._browser.close()
            self._browser = None
        if hasattr(self, '_playwright') and self._playwright:
            self._playwright.stop()
            self._playwright = None
        logger.info(f"Playwright browser closed for {self.source_name}")

    @property
    def page(self):
        """Get the current page, setting up browser if needed"""
        if self._page is None:
            self.setup_browser()
        return self._page

    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 30000) -> None:
        """
        Navigate to URL with smart waiting.

        Args:
            url: URL to navigate to
            wait_until: When to consider navigation complete
                       - 'domcontentloaded': DOM is ready (faster)
                       - 'load': Full page load including resources
                       - 'networkidle': No network activity for 500ms (slowest but most complete)
            timeout: Maximum wait time in milliseconds
        """
        self.page.goto(url, wait_until=wait_until, timeout=timeout)

    def wait_for_selector(self, selector: str, timeout: int = 10000, state: str = "visible"):
        """
        Wait for element to appear.

        Args:
            selector: CSS selector or XPath
            timeout: Maximum wait time in milliseconds
            state: Element state to wait for ('attached', 'detached', 'visible', 'hidden')
        """
        return self.page.wait_for_selector(selector, timeout=timeout, state=state)

    def query_selector(self, selector: str):
        """Find first element matching selector"""
        return self.page.query_selector(selector)

    def query_selector_all(self, selector: str):
        """Find all elements matching selector"""
        return self.page.query_selector_all(selector)

    def get_text(self, selector: str) -> Optional[str]:
        """Get text content of element, returns None if not found"""
        elem = self.page.query_selector(selector)
        if elem:
            return self.clean_text(elem.text_content())
        return None

    def get_attribute(self, selector: str, attribute: str) -> Optional[str]:
        """Get attribute value of element, returns None if not found"""
        elem = self.page.query_selector(selector)
        if elem:
            return elem.get_attribute(attribute)
        return None

    def get_html(self) -> str:
        """Get current page HTML content"""
        return self.page.content()

    def get_soup(self) -> BeautifulSoup:
        """Get BeautifulSoup object of current page"""
        return BeautifulSoup(self.page.content(), 'html.parser')

    def scroll_to_bottom(self, delay: int = 500):
        """Scroll to bottom of page to trigger lazy loading"""
        self.page.evaluate("""
            async () => {
                await new Promise((resolve) => {
                    let totalHeight = 0;
                    const distance = 300;
                    const timer = setInterval(() => {
                        window.scrollBy(0, distance);
                        totalHeight += distance;
                        if (totalHeight >= document.body.scrollHeight) {
                            clearInterval(timer);
                            resolve();
                        }
                    }, 100);
                });
            }
        """)
        self.page.wait_for_timeout(delay)

    def click(self, selector: str, timeout: int = 5000):
        """Click element with auto-waiting"""
        self.page.click(selector, timeout=timeout)

    def fill(self, selector: str, value: str):
        """Fill input field"""
        self.page.fill(selector, value)

    def screenshot(self, path: str):
        """Take screenshot for debugging"""
        self.page.screenshot(path=path)

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

        return has_valid_ext or is_dynamic or '?' in url

    @abstractmethod
    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from source - must be implemented by subclasses"""
        pass

    def run(self) -> List[EventCreate]:
        """Execute the scraper and return events"""
        try:
            logger.info(f"Starting Playwright scrape of {self.source_name}")
            self.setup_browser()
            events = self.scrape_events()
            logger.info(f"Successfully scraped {len(events)} events from {self.source_name}")
            return events
        except Exception as e:
            logger.error(f"Failed to scrape {self.source_name}: {str(e)}")
            raise
        finally:
            self.cleanup_browser()
