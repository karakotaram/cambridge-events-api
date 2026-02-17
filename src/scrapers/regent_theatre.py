"""Scraper for Regent Theatre Arlington events"""
import json
import logging
import re
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class RegentTheatreScraper(BasePlaywrightScraper):
    """Scraper for Regent Theatre (regenttheatre.com) — JSON-LD via Playwright."""

    def __init__(self):
        super().__init__(
            source_name="Regent Theatre",
            source_url="https://www.regenttheatre.com/schedule",
        )

    def scrape_events(self) -> List[EventCreate]:
        events = []

        try:
            self.goto(self.source_url, wait_until="networkidle")
            self.page.wait_for_timeout(3000)

            soup = self.get_soup()

            for script in soup.find_all("script", type="application/ld+json"):
                try:
                    data = json.loads(script.string)
                except (json.JSONDecodeError, TypeError):
                    continue

                # Handle single event objects
                if isinstance(data, dict) and data.get("@type") == "Event":
                    event = self._parse_json_ld(data)
                    if event:
                        events.append(event)
                # Handle lists
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("@type") == "Event":
                            event = self._parse_json_ld(item)
                            if event:
                                events.append(event)

        except Exception as e:
            logger.error(f"Error scraping Regent Theatre: {e}")

        logger.info(f"Scraped {len(events)} events from Regent Theatre")
        return events

    def _parse_json_ld(self, data: dict) -> Optional[EventCreate]:
        """Parse a single JSON-LD Event object."""
        name = (data.get("name") or "").strip()
        if not name or name.lower() == "private event":
            return None

        # Parse start date — format is non-standard: "2026-2-17T19:00-4:00"
        start_str = data.get("startDate", "")
        start_dt = self._parse_date(start_str)
        if not start_dt:
            return None

        end_dt = self._parse_date(data.get("endDate", ""))

        # URL
        url = data.get("url", self.source_url)

        # Description — may contain HTML
        raw_desc = data.get("description", "")
        if raw_desc:
            desc_soup = BeautifulSoup(raw_desc, "html.parser")
            description = self.clean_text(desc_soup.get_text())
        else:
            description = name

        # Image
        image_url = data.get("image")

        # Location
        venue_name = "Regent Theatre"
        street_address = "7 Medford Street"
        city = "Arlington"
        zip_code = "02474"

        location = data.get("location")
        if isinstance(location, list) and location:
            location = location[0]
        if isinstance(location, dict):
            venue_name = location.get("name", venue_name)
            address = location.get("address", {})
            if isinstance(address, dict):
                addr_str = address.get("streetAddress", "")
                if addr_str:
                    # Parse "7 Medford Street, Arlington, MA 02474"
                    parts = [p.strip() for p in addr_str.split(",")]
                    if parts:
                        street_address = parts[0]

        category = self._detect_category(name, description)

        return EventCreate(
            title=name[:200],
            description=description[:2000],
            start_datetime=start_dt,
            end_datetime=end_dt,
            venue_name=venue_name,
            street_address=street_address,
            city=city,
            state="MA",
            zip_code=zip_code,
            category=category,
            source_name=self.source_name,
            source_url=url,
            image_url=image_url,
        )

    def _parse_date(self, text: str) -> Optional[datetime]:
        """Parse non-standard ISO dates like '2026-2-17T19:00-4:00'."""
        if not text:
            return None

        try:
            # Fix non-standard timezone offset: -4:00 -> -04:00
            text = re.sub(r"([+-])(\d):(\d{2})$", r"\g<1>0\2:\3", text)

            # Zero-pad month and day for Python 3.9 fromisoformat compatibility
            # '2026-2-17' -> '2026-02-17'
            date_match = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})(.*)", text)
            if date_match:
                y, m, d, rest = date_match.groups()
                text = f"{y}-{int(m):02d}-{int(d):02d}{rest}"

            # Handle date-only format like '2026-02-20'
            if "T" not in text:
                return datetime.strptime(text.strip(), "%Y-%m-%d").replace(hour=12)
            return datetime.fromisoformat(text)
        except (ValueError, OverflowError):
            return None

    def _detect_category(self, title: str, description: str) -> EventCategory:
        text = f"{title} {description}".lower()
        if any(w in text for w in ["concert", "music", "jazz", "band", "singer", "dj", "tribute"]):
            return EventCategory.MUSIC
        if any(w in text for w in ["film", "screening", "movie", "cinema"]):
            return EventCategory.ARTS_CULTURE
        if any(w in text for w in ["lecture", "talk", "speaker"]):
            return EventCategory.LECTURES
        if any(w in text for w in ["theater", "theatre", "comedy", "improv", "play", "drama"]):
            return EventCategory.THEATER
        if any(w in text for w in ["exhibit", "gallery", "art"]):
            return EventCategory.ARTS_CULTURE
        return EventCategory.OTHER
