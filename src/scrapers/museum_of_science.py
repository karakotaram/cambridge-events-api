"""Scraper for Museum of Science Boston events using Playwright"""
import logging
import re
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup
from dateutil import parser as dateutil_parser

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class MuseumOfScienceScraper(BasePlaywrightScraper):
    """Scraper for Museum of Science Boston events via rendered page."""

    def __init__(self):
        super().__init__(
            source_name="Museum of Science",
            source_url="https://www.mos.org/events",
        )

    def scrape_events(self) -> List[EventCreate]:
        events = []

        try:
            self.goto(self.source_url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(5000)

            soup = self.get_soup()
            cards = soup.select(".listing-item")
            logger.info(f"Found {len(cards)} event cards on MOS page")

            for card in cards:
                try:
                    event = self._parse_card(card)
                    if event:
                        events.append(event)
                except Exception as e:
                    logger.debug(f"Error parsing MOS event card: {e}")

        except Exception as e:
            logger.error(f"Error scraping Museum of Science: {e}")

        logger.info(f"Scraped {len(events)} events from Museum of Science")
        return events

    def _parse_card(self, card) -> Optional[EventCreate]:
        """Parse a single .listing-item card."""
        # Title
        title_tag = card.select_one("h3.listing-item__title a")
        if not title_tag:
            return None
        title = self.clean_text(title_tag.get_text())
        if not title:
            return None

        # URL
        href = title_tag.get("href", "")
        if href.startswith("/"):
            href = f"https://www.mos.org{href}"
        source_url = href or self.source_url

        # Date/time
        date_tag = card.select_one(".field--name-field-date-time-info")
        date_text = self.clean_text(date_tag.get_text()) if date_tag else ""
        start_dt = self._parse_date(date_text)
        if not start_dt:
            return None

        # Description
        summary_tag = card.select_one(".listing-item__summary")
        description = self.clean_text(summary_tag.get_text()) if summary_tag else title

        # Image
        img_tag = card.select_one("picture img[src]")
        image_url = img_tag["src"] if img_tag else None

        # Category
        category = self._detect_category(title, description)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start_dt,
            venue_name="Museum of Science",
            street_address="1 Museum Of Science Driveway",
            city="Boston",
            state="MA",
            zip_code="02114",
            category=category,
            source_name=self.source_name,
            source_url=source_url,
            image_url=image_url,
        )

    def _parse_date(self, text: str) -> Optional[datetime]:
        """Parse date strings like 'Thursday, February 19 | 7:00 pm'."""
        if not text:
            return None

        # Strip day-of-week prefix
        text = re.sub(
            r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),?\s*",
            "", text, flags=re.I,
        )

        # Handle "Doors at X, Show at Y" — use show time
        show_match = re.search(r"Show\s+(?:at\s+)?(\d{1,2}:\d{2}\s*[ap]m)", text, re.I)
        if show_match:
            time_str = show_match.group(1)
            date_part = text.split("|")[0].strip() if "|" in text else text.split(",")[0].strip()
            text = f"{date_part} {time_str}"
        elif "|" in text:
            parts = text.split("|")
            date_part = parts[0].strip()
            time_part = parts[1].strip()
            # Handle range like "10:00 am - 4:00 pm" — use start time
            time_part = re.split(r"\s*[-–]\s*", time_part)[0].strip()
            text = f"{date_part} {time_part}"

        # Add current year if not present
        if not re.search(r"\d{4}", text):
            text = f"{text} {datetime.now().year}"

        try:
            return dateutil_parser.parse(text, fuzzy=True)
        except (ValueError, OverflowError):
            return None

    def _detect_category(self, title: str, description: str) -> EventCategory:
        text = f"{title} {description}".lower()
        if any(w in text for w in ["concert", "music", "jazz", "symphony", "dj", "tribute"]):
            return EventCategory.MUSIC
        if any(w in text for w in ["lecture", "talk", "speaker", "seminar", "symposium"]):
            return EventCategory.LECTURES
        if any(w in text for w in ["exhibit", "exhibition", "gallery", "art", "film", "screening"]):
            return EventCategory.ARTS_CULTURE
        if any(w in text for w in ["theater", "theatre", "comedy", "improv"]):
            return EventCategory.THEATER
        return EventCategory.COMMUNITY
