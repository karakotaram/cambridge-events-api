"""Scraper for The Sinclair Cambridge events"""
import logging
import re
from datetime import datetime
from typing import List, Optional

from dateutil import parser as dateutil_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class TheSinclairScraper(BaseScraper):
    """Scraper for The Sinclair (sinclaircambridge.com) — static HTML."""

    def __init__(self):
        super().__init__(
            source_name="The Sinclair",
            source_url="https://www.sinclaircambridge.com/events/",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        events = []
        html = self.fetch_html(self.source_url)
        if not html:
            return events

        soup = self.parse_html(html)
        entries = soup.select("div.entry.sinclair")

        for entry in entries:
            try:
                event = self._parse_entry(entry)
                if event:
                    events.append(event)
            except Exception as e:
                logger.debug(f"Error parsing Sinclair event: {e}")

        logger.info(f"Scraped {len(events)} events from The Sinclair")
        return events

    def _parse_entry(self, entry) -> Optional[EventCreate]:
        """Parse a single event entry div."""
        # Title (main artist)
        title_tag = entry.select_one("h3.carousel_item_title_small a")
        if not title_tag:
            return None
        title = self.clean_text(title_tag.get_text())
        if not title:
            return None

        # Supporting act / tour name for richer description
        parts = [title]
        tour_tag = entry.select_one("h5.tour")
        if tour_tag and tour_tag.get_text(strip=True):
            parts.append(tour_tag.get_text(strip=True))
        support_tag = entry.select_one("h4.supporting")
        if support_tag and support_tag.get_text(strip=True):
            parts.append(f"with {support_tag.get_text(strip=True)}")
        description = " — ".join(parts)

        # Detail URL
        source_url = title_tag.get("href", self.source_url)
        if source_url.startswith("/"):
            source_url = f"https://www.sinclaircambridge.com{source_url}"

        # Date and time
        date_tag = entry.select_one("span.date")
        time_tag = entry.select_one("span.time")
        date_text = self.clean_text(date_tag.get_text()) if date_tag else ""
        time_text = self.clean_text(time_tag.get_text()) if time_tag else ""

        start_dt = self._parse_date(date_text, time_text)
        if not start_dt:
            return None

        # Age restriction
        age_tag = entry.select_one("span.age")
        age_text = self.clean_text(age_tag.get_text()) if age_tag else ""
        family_friendly = "all ages" in age_text.lower()

        # Image
        img_tag = entry.select_one("div.thumb img")
        image_url = img_tag.get("src") if img_tag else None

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start_dt,
            venue_name="The Sinclair",
            street_address="52 Church St",
            city="Cambridge",
            state="MA",
            zip_code="02138",
            category=EventCategory.MUSIC,
            family_friendly=family_friendly,
            age_restrictions=age_text or None,
            source_name=self.source_name,
            source_url=source_url,
            image_url=image_url,
        )

    def _parse_date(self, date_text: str, time_text: str) -> Optional[datetime]:
        """Parse date like 'Tue, Feb 17, 2026' and time like 'Doors 7:00 PM'."""
        if not date_text:
            return None

        # Extract time from "Doors 7:00 PM" or "Show 8:00 PM"
        time_match = re.search(r"(\d{1,2}:\d{2}\s*[AP]M)", time_text, re.I)
        combined = date_text
        if time_match:
            combined = f"{date_text} {time_match.group(1)}"

        try:
            return dateutil_parser.parse(combined, fuzzy=True)
        except (ValueError, OverflowError):
            return None
