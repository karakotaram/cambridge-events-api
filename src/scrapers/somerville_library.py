"""Custom scraper for the Somerville Public Library

The library's own site embeds an Assabet Interactive calendar in an iframe; this
reads Assabet directly. Its month listing renders server-side and carries two
descriptions of every event:

  - a schema.org JSON-LD block: `startDate` (date only), `doorTime` (start),
    `duration`, address, image
  - a visible card: "Thursday, October 1", "10:30 AM—12:00 PM", branch, and
    the event's Assabet categories as CSS classes

The date and time come from the JSON-LD and are only believed when the card
agrees. `doorTime` normally means doors-open, not start; here it is the start
for all 522 events checked on 2026-10-05, but if Assabet ever starts using it
as written, the cross-check is what notices.

Months chain by the listing's own "next" link from `/calendar/event-listing/`,
which redirects to the current month, so no clock is consulted.
"""
import html
import json
import logging
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

BASE = "https://somervillepubliclibrary.assabetinteractive.com"
LIBRARY = "Somerville Public Library"

# Programming is published about six months out as of 2026-10.
MAX_MONTHS = 6

# "10:30 AM—12:00 PM", or "6:00—7:00 PM" when both share a meridiem
VISIBLE_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([AP]M)?\s*[—–-]\s*\d{1,2}:\d{2}\s*([AP]M)", re.IGNORECASE)
# "79 Highland Ave, Somerville , MA, 02143"
ADDRESS = re.compile(r"^(?P<street>.+?),\s*(?P<city>[^,]+?)\s*,\s*(?P<state>[A-Z]{2}),?\s*(?P<zip>\d{5})?$")
DURATION = re.compile(r"^PT(\d+)S$")

FAMILY_CATEGORIES = {"children", "storytimes", "all-ages"}


class SomervillePublicLibraryScraper(BaseScraper):
    """Custom scraper for Somerville Public Library events"""

    def __init__(self):
        super().__init__(
            source_name=LIBRARY,
            source_url=f"{BASE}/calendar/event-listing/",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()

        url = self.source_url
        for _ in range(MAX_MONTHS):
            try:
                soup = self.parse_html(self.fetch_html(url))
            except Exception as e:
                logger.error(f"Failed to fetch {url}: {e}")
                break

            new = 0
            for event in self.parse_month(soup):
                key = (event.source_url, event.start_datetime)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
                    new += 1

            # An empty month is the edge of what has been published
            next_link = soup.select_one('a[href*="/event-listing/?from=next"]')
            if not new or not next_link:
                break
            url = next_link["href"]

        logger.info(f"Scraped {len(events)} events from {LIBRARY}")
        return events

    def parse_month(self, soup) -> List[EventCreate]:
        """Every dateable, scheduled event on one month's listing."""
        structured = self._json_ld_by_url(soup)
        events = []
        for card in soup.select("div.listing-event"):
            # Closure notices ("All Closed") are cards too, with no event link
            link = card.select_one("h3 a[href]")
            if not link:
                continue
            data = structured.get(link["href"])
            if data is None:
                logger.warning(f"Skipping {link['href']} - no structured data to date it by")
                continue
            event = self._parse_event(card, data)
            if event:
                events.append(event)
        return events

    @staticmethod
    def _json_ld_by_url(soup) -> Dict[str, dict]:
        out = {}
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                # Descriptions carry raw newlines, which strict JSON forbids
                data = json.loads(script.string or "", strict=False)
            except ValueError as e:
                logger.warning(f"Unparseable JSON-LD block: {e}")
                continue
            if isinstance(data, dict) and data.get("@type") == "Event" and data.get("url"):
                out[data["url"]] = data
        return out

    def _parse_event(self, card, data: dict) -> Optional[EventCreate]:
        title = self.clean_text(html.unescape(data.get("name") or ""))
        if len(title) < 3:
            return None
        if (data.get("eventStatus") or "").endswith("EventCancelled") or "CANCEL" in title.upper():
            return None

        start = self._start(card, data)
        if start is None:
            # Never guess — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{title}' - start time unreadable or self-contradictory ({data.get('url')})")
            return None

        duration = DURATION.match(data.get("duration") or "")
        end = start + timedelta(seconds=int(duration.group(1))) if duration else None

        categories = {c[len("category-"):] for c in card.get("class", []) if c.startswith("category-")}
        description = self._description(data.get("description") or "") or title
        venue, street, city, state, zip_code = self._location(card, data)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end if end and end > start else None,
            source_url=data["url"],
            source_name=self.source_name,
            venue_name=venue,
            street_address=street,
            city=city,
            state=state,
            zip_code=zip_code,
            image_url=data.get("image") or None,
            category=self.categorize_event(categories, title, description),
            family_friendly=bool(categories & FAMILY_CATEGORIES),
        )

    def _start(self, card, data: dict) -> Optional[datetime]:
        """`startDate` + `doorTime`, if the visible card says the same thing."""
        try:
            start = datetime.strptime(f"{data.get('startDate')} {data.get('doorTime')}", "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            return None

        day = card.select_one(".event-day")
        when = card.select_one(".event-time")
        match = VISIBLE_TIME.search(self.clean_text(when.get_text())) if when else None
        if not day or not match:
            return None

        if f"{start:%B} {start.day}" not in self.clean_text(day.get_text()):
            logger.warning(f"Card day {day.get_text()!r} disagrees with startDate {data.get('startDate')}")
            return None
        meridiem = (match.group(3) or match.group(4)).upper()
        hour = int(match.group(1)) % 12 + (12 if meridiem == "PM" else 0)
        if (hour, int(match.group(2))) != (start.hour, start.minute):
            logger.warning(f"Card time {match.group(0)!r} disagrees with doorTime {data.get('doorTime')}")
            return None
        return start

    @staticmethod
    def _description(raw: str) -> str:
        """The excerpt is entity-encoded HTML ending in a "Learn More" link.

        Some excerpts are encoded twice ("&amp;amp;", "&amp;nbsp;"), so decode
        until nothing changes.
        """
        text = raw
        for _ in range(3):
            decoded = html.unescape(text)
            if decoded == text:
                break
            text = decoded
        if "<" in text:
            text = BeautifulSoup(text, "html.parser").get_text(" ")
        text = re.sub(r"\s*Learn More\s*$", "", text)
        return " ".join(text.split())

    def _location(self, card, data: dict) -> Tuple[Optional[str], ...]:
        """(venue, street, city, state, zip) for a branch, an online event, or an off-site one."""
        def text(selector):
            el = card.select_one(selector)
            return self.clean_text(el.get_text()) if el else None

        branch = text(".event-location-branch")
        room = text(".event-location-location")
        online = (data.get("eventAttendanceMode") or "").endswith("OnlineEventAttendanceMode")
        if online or (not branch and room == "Zoom"):
            return "Online", None, None, None, None

        venue = f"{LIBRARY} – {branch}" if branch else room
        address = text(".event-location-address")
        match = ADDRESS.match(address or "")
        if match:
            return venue, match["street"], match["city"], match["state"], match["zip"]
        return venue, address, "Somerville", "MA", None

    @staticmethod
    def categorize_event(categories: set, title: str, description: str) -> EventCategory:
        """The library's own categories first, keywords for the rest."""
        text = f"{title} {description}".lower()
        if categories & {"esl", "us-citizenship", "social-work"}:
            return EventCategory.COMMUNITY
        if "music-movies" in categories:
            return EventCategory.MUSIC if any(w in text for w in ["music", "concert", "sing"]) else EventCategory.ARTS_CULTURE
        if categories & {"storytimes", "hands-on", "book-clubs"}:
            return EventCategory.ARTS_CULTURE
        if any(w in text for w in ["concert", "singalong", "sing-along", "music"]):
            return EventCategory.MUSIC
        if any(w in text for w in ["workshop", "class", "lecture", "talk", "author"]):
            return EventCategory.LECTURES
        if any(w in text for w in ["craft", "art", "film", "movie", "game", "club"]):
            return EventCategory.ARTS_CULTURE
        return EventCategory.COMMUNITY
