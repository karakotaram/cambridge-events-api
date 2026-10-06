"""Scraper for The Lily Pad, Inman Square.

The venue's site is a Squarespace events collection. Appending `?format=json`
returns every upcoming event in one response — start and end as epoch
milliseconds, plus the full post body — so no browser and no per-event page
loads are needed. The rendered page is no better: it is cached, and on
2026-10-06 it still marked six shows from two days earlier as upcoming.
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

SITE = "https://www.lilypadinman.com"

# Squarespace editor placeholders that survive into published bodies.
PLACEHOLDERS = re.compile(r"double-click to edit\.*|your custom text here", re.I)

PRIVATE = ("private party", "private event", "closed to public",
           "invite only", "members only", "by invitation")


def epoch_ms_to_eastern(ms) -> Optional[datetime]:
    """Squarespace epoch milliseconds -> naive Eastern wall clock, on the minute.

    The milliseconds are noise Squarespace adds to keep timestamps unique
    (19:30 arrives as ...400563), and EventValidator rightly rejects a start
    carrying sub-minute precision, so they are dropped.
    """
    if not isinstance(ms, (int, float)) or ms <= 0:
        return None
    instant = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
    return to_eastern_naive(instant).replace(second=0, microsecond=0)


class LilyPadScraper(BaseScraper):
    """The Lily Pad's Squarespace events collection, read as JSON."""

    def __init__(self):
        super().__init__(
            source_name="The Lily Pad",
            source_url=f"{SITE}/",
            use_selenium=False,
        )

    def fetch_collection(self) -> dict:
        response = requests.get(self.source_url, params={"format": "json"}, timeout=30)
        response.raise_for_status()
        return response.json()

    def scrape_events(self) -> List[EventCreate]:
        payload = self.fetch_collection()
        if "upcoming" not in payload:
            # A shape change must fail the source, not publish an empty one.
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
        url = f"{SITE}{item.get('fullUrl', '')}" if item.get("fullUrl") else self.source_url

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
            description = f"{title} - live at The Lily Pad in Inman Square, Cambridge."

        cost = re.search(r"\$\d+(?:\s*-\s*\$?\d+|\s*/\s*\$\d+)?", excerpt or description)

        genres = [self.clean_text(c) for c in item.get("categories") or [] if c]
        category = EventCategory.SPORTS if "Yoga" in genres else EventCategory.MUSIC

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            source_url=url,
            source_name=self.source_name,
            venue_name="The Lily Pad",
            street_address="1353 Cambridge St",
            city="Cambridge",
            state="MA",
            zip_code="02139",
            category=category,
            tags=genres,
            cost=cost.group() if cost else None,
            image_url=item.get("assetUrl") or None,
        )
