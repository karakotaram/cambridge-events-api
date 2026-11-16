"""Spectrum Singers — a Cambridge choir that sells its concerts through OvationTix.

spectrumsingers.org is a Squarespace site whose concert pages are prose; each
links to an OvationTix production (client 34652). The storefront reads two
JSON endpoints, and so do we:

  - `CalendarProductions`: every upcoming performance, with its local start
    ("2026-11-21 19:30") and cancelled/visible flags;
  - `Production?expandPerformances=summary`: each production's venue.

The API answers only with a `clientId` header naming the organisation. Times
are the storefront's wall clock; on 2026-11-16 "2026-11-21 19:30" and
"2026-12-19 15:00" matched the choir's own "Saturday, November 21, 2026 |
7:30pm" and "Saturday, December 19, 2026 | 3:00pm".

The choir also sings at All Saints Parish in Brookline; those concerts are
outside the calendar's area and dropped. OvationTix calls the Cambridge church
"First Church Congregational"; it is First Church in Cambridge, 11 Garden
Street, and is named that way so readers (and deduplication against
BostonShows.org) recognise it.

Production descriptions are a subtitle followed by checkout instructions
("NOTE: Select your seating section..."); only the subtitle is kept.
"""
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

import requests

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper

logger = logging.getLogger(__name__)

API = "https://web.ovationtix.com/trs/api/rest"
CLIENT_ID = "34652"
VENUE_NAMES = {"First Church Congregational": "First Church in Cambridge"}
CHECKOUT_NOTE = re.compile(r"\bNOTE:\s.*$", re.S)


class SpectrumSingersScraper(BaseFeedScraper):
    HOME = None
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Spectrum Singers",
                         source_url="https://www.spectrumsingers.org/concerts-and-tickets")

    def api(self, path: str, params: Optional[dict] = None):
        headers = {**self.get_browser_headers(), "Accept": "application/json",
                   "clientId": CLIENT_ID, "newCIRequest": "true"}
        response = requests.get(f"{API}/{path}", params=params, headers=headers, timeout=30)
        response.raise_for_status()
        return response.json()

    def scrape_events(self) -> List[EventCreate]:
        productions = {p["id"]: p for p in self.api("Production", {"expandPerformances": "summary"})}
        calendar = self.api("CalendarProductions")
        events = []
        for day in calendar:
            for production in day.get("productions") or []:
                for show in production.get("showtimes") or []:
                    event = self.parse_showtime(show, production, productions)
                    if event:
                        events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    @staticmethod
    def wall_clock(raw) -> Optional[datetime]:
        try:
            return datetime.strptime(str(raw), "%Y-%m-%d %H:%M")
        except ValueError:
            return None

    def parse_showtime(self, show: dict, production: dict, productions: Dict[int, dict]) -> Optional[EventCreate]:
        name = self.text(production.get("name"))
        title = f"Spectrum Singers: {name}"
        if not name or not self.is_event(name):
            return None
        pid = show.get("productionId") or production.get("productionId")
        url = f"https://ci.ovationtix.com/{CLIENT_ID}/production/{pid}"
        if show.get("isCancelled") or show.get("isVisible") is False or production.get("hidden"):
            logger.info(f"{self.source_name}: skipping cancelled or hidden performance of '{name}'")
            return None

        start = self.wall_clock(show.get("performanceStartTime"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date ({url})")
            return None
        end = self.wall_clock(show.get("performanceEndTime"))
        if end is not None and end <= start:
            end = None

        detail = productions.get(pid) or {}
        venue = detail.get("venue") or {}
        address = venue.get("address") or {}
        venue_name = VENUE_NAMES.get(venue.get("name"), venue.get("name"))
        where = self.place(title, venue_name, address.get("line1"), address.get("city"), address.get("zipcode"))
        if where is None:
            return None

        subtitle = CHECKOUT_NOTE.sub("", self.text(detail.get("description")).replace("﻿", "")).strip()
        description = f"{name}{': ' + subtitle if subtitle else ''}. Spectrum Singers in concert at {where.venue}, {where.city}."

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
            category=EventCategory.MUSIC,
            source_name=self.source_name,
            source_url=url,
        )
