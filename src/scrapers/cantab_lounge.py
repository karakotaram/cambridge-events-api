"""The Cantab Lounge, Central Square — two rooms, both booked through TuneHatch.

thecantablounge.com embeds TuneHatch calendar widgets, one per room: the Lounge
upstairs and the Cantab Underground in the basement. The widget is a browser
app, but what it renders comes from TuneHatch's public shows endpoint,
`/api/v1/shows?venue_id=…`, which this reads directly — the same parameters
the widget sends, including `starts_after`, without which the endpoint returns
every show since 2024.

**Times.** `startsAt` is a UTC instant and each show carries the venue's
`timezone`. Converting the instant is only right if the venue's clock is set
to Eastern, so a show stamped with any other zone is skipped rather than
guessed at (as in base_tribe). `doorsAt` is not used: it is often empty and at
least once sat a month before the show it belonged to.
"""
import logging
import re
from datetime import date, datetime, time, timezone
from typing import List, Optional
from zoneinfo import ZoneInfo

import requests

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import EASTERN_ZONES, BaseFeedScraper, Place

logger = logging.getLogger(__name__)

API = "https://tunehatch.com/api/v1/shows"
SHOW_PAGE = "https://tunehatch.com/shows/"
PAGE_SIZE = 100
MAX_PAGES = 5

# The rooms, as the venue's own pages embed them.
ROOMS = {
    "968ae511-dae2-43a9-a916-15ae44ef47b8": "Cantab Lounge",        # /lounge
    "6126dd7d-0e07-4aad-92ef-3b52c0955bd4": "Cantab Underground",   # /underground
}

# Ticketing notes in titles: "Dancehall - TIX ON EVENTBRITE".
TICKET_NOTE = re.compile(r"\s*[-–|]\s*tix (on|at) \w+\s*$", re.I)


class CantabLoungeScraper(BaseFeedScraper):
    HOME = Place("Cantab Lounge", "738 Massachusetts Avenue", "Cambridge", "02139")
    CATEGORY = EventCategory.MUSIC

    def __init__(self, today: Optional[date] = None):
        super().__init__(source_name="The Cantab Lounge", source_url="https://www.thecantablounge.com/")
        # Only bounds the query (shows starting after the start of today).
        self.today = today

    def fetch_shows(self, venue_id: str, starts_after: str, offset: int) -> dict:
        params = {
            "limit": PAGE_SIZE, "offset": offset, "venue_id": venue_id,
            "published": "true", "private": "false", "lineup_or_creator_only": "true",
            "starts_after": starts_after, "sort_direction": "asc",
        }
        response = requests.get(API, params=params, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.json()

    def scrape_events(self) -> List[EventCreate]:
        today = self.today or date.today()
        # Midnight Eastern, written as UTC, as the widget does.
        midnight = datetime.combine(today, time(), tzinfo=ZoneInfo("America/New_York"))
        starts_after = midnight.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        events, seen = [], set()
        for venue_id, room in ROOMS.items():
            for page in range(MAX_PAGES):
                data = self.fetch_shows(venue_id, starts_after, page * PAGE_SIZE)
                if not data.get("success", False):
                    raise ValueError(f"{self.source_name}: TuneHatch refused the {room} query: {data.get('error')}")
                for show in data.get("data") or []:
                    if show.get("id") in seen:
                        continue
                    seen.add(show.get("id"))
                    event = self.parse_show(show)
                    if event:
                        events.append(event)
                total = int((data.get("pagination") or {}).get("total") or 0)
                if (page + 1) * PAGE_SIZE >= total:
                    break
            else:
                logger.warning(f"{self.source_name}: {room} has more than {MAX_PAGES * PAGE_SIZE} upcoming shows")
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_show(self, show: dict) -> Optional[EventCreate]:
        title = TICKET_NOTE.sub("", self.text(show.get("name")))
        if not self.is_event(title) or show.get("private") or show.get("deletedAt"):
            return None
        url = f"{SHOW_PAGE}{show['slug']}" if show.get("slug") else self.source_url

        if show.get("timezone") not in EASTERN_ZONES:
            logger.warning(f"Skipping '{title}' - venue clock is {show.get('timezone')!r}, not Eastern ({url})")
            return None
        start = self.instant(show.get("startsAt"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date (startsAt={show.get('startsAt')!r}) ({url})")
            return None
        end = self.instant(show.get("endsAt"))
        if end is not None and end <= start:
            end = None

        where = self.place(title, show.get("venueName"), show.get("streetAddress"),
                           show.get("city") or show.get("venueCity"), show.get("zip"))
        if where is None:
            return None

        performers = [p.get("name") for p in show.get("performerNames") or []
                      if p.get("name") and not str(p.get("entityKey", "")).startswith("sr_group:")]
        about = self.text(show.get("about"))
        if re.fullmatch(r"https?://\S+", about):
            about = f"Tickets: {about}"  # some listings' whole blurb is a ticket link
        description = " ".join(part for part in (
            about,
            f"With {', '.join(performers)}." if performers and ", ".join(performers) not in title else "",
        ) if part)
        if len(description) < 20 or description.startswith("Tickets: "):
            description = f"{title} at {where.venue}, Central Square, Cambridge. {description}".strip()

        min_age = show.get("minAge") or show.get("venueMinAge")
        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.categorize(title),
            age_restrictions=f"{min_age}+" if min_age else None,
            source_name=self.source_name,
            source_url=url,
            image_url=show.get("flyerURL") or None,
        )

    @staticmethod
    def instant(value) -> Optional[datetime]:
        """'2026-11-16T01:30:00Z' -> naive Eastern wall clock."""
        if not value:
            return None
        try:
            moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if moment.tzinfo is None:
            return None
        return to_eastern_naive(moment)
