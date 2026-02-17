"""Scraper for Harvard Graduate School of Design (GSD) public programs"""
import logging
import re
from html import unescape
from typing import List

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparse

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

API_URL = "https://www.gsd.harvard.edu/wp-json/gsd/v1/events"


class HarvardGSDScraper(BaseScraper):
    """Scrape public events from Harvard GSD via their WordPress REST API"""

    def __init__(self):
        super().__init__(
            source_name="Harvard GSD",
            source_url="https://www.gsd.harvard.edu/public-programs/",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        response = requests.get(
            API_URL,
            headers=self.get_browser_headers(),
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()

        events = []
        for item in data:
            try:
                event = self._parse_event(item)
                if event:
                    events.append(event)
            except Exception as e:
                logger.warning(f"Failed to parse GSD event: {e}")

        logger.info(f"Found {len(events)} Harvard GSD events")
        return events

    def _extract_about(self, desc_html: str) -> str:
        """Extract the 'About this Event' text, or fall back to body paragraphs."""
        if not desc_html:
            return ""
        soup = BeautifulSoup(desc_html, "html.parser")

        # Try to find "About this Event" heading
        heading = soup.find(
            lambda tag: tag.name in ("h2", "h3")
            and "about this event" in tag.get_text().lower()
        )
        if heading:
            parts = []
            for sib in heading.find_next_siblings():
                if sib.name and sib.name.startswith("h"):
                    break
                text = sib.get_text(strip=True)
                if text:
                    parts.append(text)
            if parts:
                return " ".join(parts)[:2000]

        # Fallback: collect <p> text after removing the hero banner
        for div in soup.find_all("div", class_=re.compile(r"hero-banner")):
            div.decompose()
        paragraphs = []
        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if len(text) > 20:
                paragraphs.append(text)
        if paragraphs:
            return " ".join(paragraphs)[:2000]
        return ""

    def _parse_event(self, item: dict):
        # Extract title, stripping HTML tags and decoding entities
        raw_title = item.get("title", {}).get("rendered", "")
        title = unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
        if not title:
            return None

        # Prefix with series name if available
        series = item.get("series", "")
        if series and series.lower() not in title.lower():
            title = f"{series}: {title}"

        # Get occurrence data
        occurrences = item.get("occurrences", [])
        if not occurrences:
            return None
        occ = occurrences[0]

        # Parse start/end times
        time_start = occ.get("time_start")
        if not time_start:
            return None
        try:
            start_datetime = dateparse.parse(time_start)
        except (ValueError, TypeError):
            return None

        end_datetime = None
        time_end = occ.get("time_end")
        if time_end:
            try:
                end_datetime = dateparse.parse(time_end)
            except (ValueError, TypeError):
                pass

        all_day = occ.get("is_all_day", False)

        # Location
        location = (occ.get("location") or "").strip()
        venue = f"Harvard GSD - {location}" if location else "Harvard GSD"

        # Build description from "About this Event" section
        description = self._extract_about(item.get("description", {}).get("rendered", ""))
        if not description:
            description = title

        # Event URL
        event_url = item.get("link", self.source_url)

        # Image
        image_url = None
        card_html = item.get("card_html", "")
        if card_html:
            img_match = re.search(r'<img[^>]+src="([^"]+)"', card_html)
            if img_match:
                image_url = img_match.group(1)

        # Categorize based on event type
        event_type = occ.get("type", "")
        category = EventCategory.LECTURES
        type_lower = event_type.lower()
        if any(w in type_lower for w in ("exhibition", "gallery", "art")):
            category = EventCategory.ARTS_CULTURE
        elif "performance" in type_lower:
            category = EventCategory.ARTS_CULTURE
        elif "community" in type_lower:
            category = EventCategory.COMMUNITY

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            all_day=all_day,
            source_url=event_url,
            source_name=self.source_name,
            venue_name=venue[:150],
            city="Cambridge",
            state="MA",
            category=category,
            family_friendly=False,
            image_url=image_url,
        )
