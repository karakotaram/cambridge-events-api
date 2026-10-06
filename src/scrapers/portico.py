"""Scraper for Portico Brewing, Somerville.

The events page is a Squarespace collection. `?format=json` returns every
upcoming event in one response, with start/end as epoch milliseconds and the
full post body, so neither a browser nor per-event page loads are needed. The
same payload carries the 30 most recent past events under `past`; those are
not read.
"""
import html
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

SITE = "https://porticobrewing.com"

PLACEHOLDERS = re.compile(r"double-click to edit\.*|your custom text here", re.I)

PRIVATE = ("private party", "private event", "closed to public",
           "invite only", "members only", "by invitation")

TRIVIA = ("trivia", "quiz", "jeopardy", "bingo")


def epoch_ms_to_eastern(ms) -> Optional[datetime]:
    """Squarespace epoch milliseconds -> naive Eastern wall clock, on the minute.

    Squarespace adds noise in the milliseconds (6 PM arrives as ...800772), and
    EventValidator rejects any start with sub-minute precision.
    """
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    instant = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return to_eastern_naive(instant).replace(second=0, microsecond=0)


class PorticoScraper(BaseScraper):
    """Portico Brewing's Squarespace events collection, read as JSON."""

    def __init__(self):
        super().__init__(
            source_name="Portico Brewing",
            source_url=f"{SITE}/upcoming-events",
            use_selenium=False,
        )

    def fetch_collection(self) -> dict:
        response = requests.get(self.source_url, params={"format": "json"}, timeout=30)
        response.raise_for_status()
        return response.json()

    def scrape_events(self) -> List[EventCreate]:
        payload = self.fetch_collection()
        if "upcoming" not in payload:
            raise ValueError(f"{self.source_name}: Squarespace JSON has no 'upcoming' list")

        events = []
        for item in payload["upcoming"]:
            try:
                event = self.parse_item(item)
            except Exception as e:
                logger.warning(f"{self.source_name}: failed to parse {item.get('fullUrl')}: {e}")
                continue
            if event:
                events.append(event)
        return events

    def body_text(self, body_html: str) -> str:
        soup = BeautifulSoup(body_html or "", "html.parser")
        blocks = [self.clean_text(b.get_text(" ")) for b in soup.select(".sqs-html-content")]
        blocks = [PLACEHOLDERS.sub("", b).strip() for b in blocks]
        return self.clean_text(" ".join(b for b in blocks if b))

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        title = self.clean_text(html.unescape(item.get("title") or ""))
        if len(title) < 3:
            return None
        url = f"{SITE}{item['fullUrl']}" if item.get("fullUrl") else self.source_url

        start = epoch_ms_to_eastern(item.get("startDate"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date ({url})")
            return None
        end = epoch_ms_to_eastern(item.get("endDate"))
        if end is not None and end < start:
            end = None

        excerpt = self.clean_text(BeautifulSoup(item.get("excerpt") or "", "html.parser").get_text(" "))
        description = self.body_text(item.get("body")) or excerpt
        if any(k in f"{title} {description}".lower() for k in PRIVATE):
            logger.info(f"{self.source_name}: skipping private event '{title}'")
            return None
        if len(description) < 20:
            description = f"{title} at Portico Brewing in Somerville, MA"

        # Dollar amounts in a trivia listing are prizes, not admission.
        cost = None
        if not any(w in f"{title} {description}".lower() for w in TRIVIA):
            m = re.search(r"\$\d+(?:\.\d{2})?", description)
            cost = m.group() if m else None

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            source_url=url,
            source_name=self.source_name,
            venue_name="Portico Brewing",
            street_address="101 South St",
            city="Somerville",
            state="MA",
            zip_code="02143",
            category=self.categorize_event(title, description),
            cost=cost,
            image_url=item.get("assetUrl") or None,
        )

    def categorize_event(self, title: str, description: str) -> EventCategory:
        """Categorize event based on keywords"""
        text = f"{title} {description}".lower()

        # Check trivia first to ensure it takes priority
        if any(word in text for word in TRIVIA):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['concert', 'music', 'band', 'dj', 'live music']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['yoga', 'fitness', 'workout']):
            return EventCategory.SPORTS
        elif any(word in text for word in ['comedy', 'stand-up', 'comedian']):
            return EventCategory.THEATER
        elif any(word in text for word in ['cooking', 'chef', 'food', 'tasting', 'dinner']):
            return EventCategory.FOOD_DRINK
        elif any(word in text for word in ['art', 'paint', 'craft']):
            return EventCategory.ARTS_CULTURE
        else:
            return EventCategory.COMMUNITY
