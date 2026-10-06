"""Scraper for The Sinclair Cambridge events.

The /events/ page renders the first 20 listings. Its "Load More Events" button
fetches the rest 20 at a time from /events/events_ajax/{offset}, which answers
with the same entry markup encoded as a JSON string. Reading only the page
stopped the calendar at the third week out.
"""
import json
import logging
import re
from datetime import datetime
from typing import List, Optional

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

PER_PAGE = 20
AJAX_URL = ("https://www.sinclaircambridge.com/events/events_ajax/{offset}"
            "?category=0&venue=0&team=0&exclude=&per_page=20&came_from_page=event-list-page")
# ~70 listings reach five months out; the cap only bounds a feed that never ends.
MAX_OFFSET = 400


class TheSinclairScraper(BaseScraper):
    """Scraper for The Sinclair (sinclaircambridge.com) — static HTML."""

    def __init__(self):
        super().__init__(
            source_name="The Sinclair",
            source_url="https://www.sinclaircambridge.com/events/",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        soup = self.parse_html(self.fetch_html(self.source_url))
        events = self.parse_entries(soup)
        listed = len(soup.select("div.entry"))

        offset = PER_PAGE
        while listed >= PER_PAGE and offset <= MAX_OFFSET:
            soup = self.parse_html(self.decode_ajax(self.fetch_html(AJAX_URL.format(offset=offset))))
            listed = len(soup.select("div.entry"))
            if not listed:
                break
            events.extend(self.parse_entries(soup))
            offset += PER_PAGE

        seen, unique = set(), []
        for event in events:
            key = (event.source_url, event.start_datetime)
            if key not in seen:
                seen.add(key)
                unique.append(event)
        logger.info(f"Scraped {len(unique)} events from The Sinclair")
        return unique

    @staticmethod
    def decode_ajax(body: str) -> str:
        """The "Load More" endpoint returns its HTML as a JSON-encoded string."""
        try:
            decoded = json.loads(body)
        except (TypeError, ValueError):
            return body or ""
        return decoded if isinstance(decoded, str) else ""

    def parse_entries(self, soup) -> List[EventCreate]:
        events = []
        # Entries without the "sinclair" class are listings for 52 Church, the
        # bar next door, and other rooms.
        for entry in soup.select("div.entry.sinclair"):
            try:
                event = self._parse_entry(entry)
                if event:
                    events.append(event)
            except Exception as e:
                logger.warning(f"Error parsing Sinclair event: {e}")
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
        # A bare headliner ("Tricky") is too short to pass the validator, and a
        # listing should still say where it is.
        description = " — ".join(parts) if len(parts) > 1 else f"{title} at The Sinclair, Cambridge"

        # Detail URL
        source_url = title_tag.get("href", self.source_url)
        if source_url.startswith("/"):
            source_url = f"https://www.sinclaircambridge.com{source_url}"

        # Date and time
        date_tag = entry.select_one("span.date")
        time_tag = entry.select_one("span.time")
        date_text = self.clean_text(date_tag.get_text()) if date_tag else ""
        time_text = self.clean_text(time_tag.get_text()) if time_tag else ""

        start_dt, all_day = self._parse_date(date_text, time_text)
        if not start_dt:
            logger.warning(f"Skipping '{title}' - unreadable date/time {date_text!r} {time_text!r} ({source_url})")
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
            all_day=all_day,
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

    @staticmethod
    def _parse_date(date_text: str, time_text: str):
        """(start, all_day) from 'Tue, Oct 6, 2026' and 'Doors 7:00 PM'.

        No time text at all gives an all-day listing; time text that cannot be
        read gives (None, False) so the entry is skipped, never placed at
        midnight.
        """
        date_text = re.sub(r"\bSept\b", "Sep", re.sub(r"\s+", " ", date_text or "").strip())
        day = None
        for fmt in ("%a, %b %d, %Y", "%a, %B %d, %Y"):
            try:
                day = datetime.strptime(date_text, fmt)
                break
            except ValueError:
                continue
        if day is None:
            return None, False

        time_text = re.sub(r"\s+", " ", time_text or "").strip()
        if not time_text:
            return day, True
        m = re.search(r"(\d{1,2}):(\d{2})\s*([AP])\.?M", time_text, re.I)
        if not m:
            return None, False
        hour, minute = int(m.group(1)) % 12, int(m.group(2))
        if m.group(3).upper() == "P":
            hour += 12
        return day.replace(hour=hour, minute=minute), False
