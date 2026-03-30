"""Scraper for The Rockwell events in Somerville"""
import html
import logging
from datetime import datetime
from typing import List, Optional
import requests

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

API_URL = "https://therockwell.org/wp-json/tribe/events/v1/events"
PER_PAGE = 50
MAX_PAGES = 5


class RockwellScraper(BaseScraper):
    """Scraper for The Rockwell - Somerville venue for comedy, music, and events"""

    def __init__(self):
        super().__init__(
            source_name="The Rockwell",
            source_url="https://therockwell.org/calendar/",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from The Rockwell via Tribe Events REST API"""
        events: List[EventCreate] = []
        seen_ids: set = set()

        page = 1
        while page <= MAX_PAGES:
            try:
                resp = requests.get(
                    API_URL,
                    params={
                        "per_page": PER_PAGE,
                        "start_date": "now",
                        "status": "publish",
                        "page": page,
                    },
                    timeout=30,
                    headers=self.get_browser_headers(),
                )
                resp.raise_for_status()
                data = resp.json()
            except Exception as e:
                logger.error(f"Error fetching Rockwell API page {page}: {e}")
                break

            api_events = data.get("events", [])
            if not api_events:
                break

            for item in api_events:
                event = self._parse_event(item)
                if event and item.get("id") not in seen_ids:
                    events.append(event)
                    seen_ids.add(item.get("id"))

            total_pages = data.get("total_pages", 1)
            if page >= total_pages:
                break
            page += 1

        logger.info(f"Scraped {len(events)} events from The Rockwell API")
        return events

    def _parse_event(self, item: dict) -> Optional[EventCreate]:
        """Parse a single event from the Tribe Events API response"""
        try:
            title = html.unescape(self.clean_text(item.get("title", "")))
            if not title:
                return None

            # Parse start datetime
            start_str = item.get("start_date")
            if not start_str:
                return None
            start_datetime = datetime.fromisoformat(start_str)

            # Description — prefer excerpt (shorter), fall back to full description
            description = self.clean_text(
                _strip_html(item.get("excerpt", ""))
                or _strip_html(item.get("description", ""))
                or f"{title} at The Rockwell"
            )

            # Event URL
            url = item.get("url") or self.source_url

            # Image
            image_url = None
            image = item.get("image")
            if isinstance(image, dict):
                image_url = image.get("url")
            elif isinstance(image, str) and image.startswith("http"):
                image_url = image

            # Cost
            cost = item.get("cost")

            # Category
            categories = item.get("categories", [])
            category = self._detect_category(title, description, categories)

            # Venue info — use API venue if present, else default
            venue = item.get("venue", {}) or {}
            venue_name = venue.get("venue") or venue.get("name") or "The Rockwell"
            address = venue.get("address") or "255 Elm Street"
            city = venue.get("city") or "Somerville"
            state = venue.get("state") or venue.get("province") or "MA"
            zip_code = venue.get("zip") or "02144"

            return EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start_datetime,
                venue_name=venue_name,
                street_address=address,
                city=city,
                state=state,
                zip_code=zip_code,
                category=category,
                cost=cost,
                source_name=self.source_name,
                source_url=url,
                image_url=image_url,
            )

        except Exception as e:
            logger.debug(f"Error parsing Rockwell event: {e}")
            return None

    def _detect_category(
        self, title: str, description: str, api_categories: list
    ) -> EventCategory:
        """Detect event category from title, description, and API categories"""
        # Check API category names first
        cat_names = {c.get("name", "").lower() for c in api_categories if isinstance(c, dict)}

        if cat_names & {"music", "concert", "live music"}:
            return EventCategory.MUSIC
        if cat_names & {"theater", "theatre", "staged reading", "performance"}:
            return EventCategory.THEATER
        if cat_names & {"comedy", "stand-up", "improv"}:
            return EventCategory.THEATER
        if cat_names & {"film", "screening", "movie"}:
            return EventCategory.ARTS_CULTURE
        if cat_names & {"food", "drink", "tasting"}:
            return EventCategory.FOOD_DRINK
        if cat_names & {"community", "trivia", "game", "bingo"}:
            return EventCategory.COMMUNITY

        # Fall back to text matching
        text = f"{title} {description}".lower()

        if any(w in text for w in ["comedy", "standup", "stand-up", "improv", "comedian"]):
            return EventCategory.THEATER
        if any(w in text for w in ["concert", "music", "band", "singer", "dj", "jazz", "rock"]):
            return EventCategory.MUSIC
        if any(w in text for w in ["trivia", "game", "bingo", "quiz"]):
            return EventCategory.COMMUNITY
        if any(w in text for w in ["drag", "cabaret", "burlesque", "show", "performance"]):
            return EventCategory.THEATER
        if any(w in text for w in ["film", "movie", "screening"]):
            return EventCategory.ARTS_CULTURE

        return EventCategory.OTHER


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string"""
    if not text:
        return ""
    from bs4 import BeautifulSoup
    return BeautifulSoup(text, "html.parser").get_text(separator=" ").strip()
