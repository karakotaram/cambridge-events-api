"""Goofs Comedy Club, Somerville — the club's own public shows endpoint.

goofscomedy.com is a Next.js app; its show list (/p/shows) pages through
`/api/public/shows?after=<date>&limit=<n>`, which returns each show's `date`
("2026-11-18") and `time` ("8:00 PM") as the club prints them, a title, the
headliner, the price and a slug. The slug encodes the same start
("2026-11-18-2000"); a show whose slug disagrees with its date and time is
skipped rather than trusted.

Shows are booked months ahead under standing titles ("Secret Headliner and
Friends", "Fresh Goofs"); one request for LIMIT shows covers the next few
months, which a daily run keeps rolling forward.
"""
import logging
from datetime import date, datetime, timedelta
from typing import List, Optional

import requests

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

SITE = "https://goofscomedy.com"
LIMIT = 100


class GoofsComedyScraper(BaseFeedScraper):
    HOME = Place("Goofs Comedy Club", "432 McGrath Highway", "Somerville", "02143")
    CATEGORY = EventCategory.THEATER

    def __init__(self, today: Optional[date] = None):
        super().__init__(source_name="Goofs Comedy Club", source_url=f"{SITE}/p/shows")
        # Only bounds the query: shows after yesterday, as the site asks.
        self.today = today

    def fetch_shows(self, after: str) -> dict:
        response = requests.get(f"{SITE}/api/public/shows", params={"after": after, "limit": LIMIT},
                                headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.json()

    def scrape_events(self) -> List[EventCreate]:
        after = ((self.today or date.today()) - timedelta(days=1)).isoformat()
        data = self.fetch_shows(after)
        if "shows" not in data:
            raise ValueError(f"{self.source_name}: response has no 'shows' list")
        events = [e for e in (self.parse_show(s) for s in data["shows"]) if e]
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    @staticmethod
    def start_of(show: dict) -> Optional[datetime]:
        try:
            start = datetime.strptime(f"{show.get('date')} {show.get('time')}", "%Y-%m-%d %I:%M %p")
        except (TypeError, ValueError):
            return None
        if show.get("slug") and show["slug"] != start.strftime("%Y-%m-%d-%H%M"):
            return None
        return start

    def parse_show(self, show: dict) -> Optional[EventCreate]:
        title = self.text(show.get("computedDisplayTitle") or show.get("title"))
        if not self.is_event(title):
            return None
        url = f"{SITE}/p/show/{show['slug']}" if show.get("slug") else self.source_url

        start = self.start_of(show)
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date (date={show.get('date')!r}, "
                           f"time={show.get('time')!r}, slug={show.get('slug')!r}) ({url})")
            return None

        blurb = next((self.text(show.get(k)) for k in
                      ("shortDescriptionOverride", "eventCardDescription", "headlinerShortDescription")
                      if show.get(k)), "")
        headliner = self.text(show.get("headlinerName"))
        lead = f"Stand-up comedy at Goofs Comedy Club, Somerville{f', headlined by {headliner}' if headliner else ''}."
        description = f"{blurb} {lead}".strip() if blurb else lead

        cents = show.get("priceGaCents")
        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            venue_name=self.HOME.venue,
            street_address=self.HOME.street,
            city=self.HOME.city,
            state="MA",
            zip_code=self.HOME.zip_code,
            # The club's standing formats ("Detention", "Fresh Goofs") are stand-up
            # whatever the title adds ("… + DJ Afterparty"); special events vary.
            category=self.categorize(title) if show.get("showType") == "Special Event" else EventCategory.THEATER,
            cost=f"${cents / 100:.0f}" if isinstance(cents, (int, float)) and cents > 0 else None,
            source_name=self.source_name,
            source_url=url,
            image_url=show.get("promoPhoto") or show.get("headlinerHeadshot") or None,
        )
