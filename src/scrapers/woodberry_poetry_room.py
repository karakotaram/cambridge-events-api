"""Woodberry Poetry Room (Harvard Library) — readings and literary events.

The room's events page lists its season as cards, each a title, a printed date
and a place, linking to the event on Harvard Library's LibCal:

    WAX POETICS: Shane McCrae on John Berryman
    Wednesday, November 18, 6:00pm
    Forum Room, Lamont Library

The printed date has no year. Rather than derive one, each card's LibCal page
is read for its schema.org Event, whose `startDate` is a full ISO timestamp
with offset ("2026-11-18T18:00:00-05:00"). The card's printed weekday, month,
day and time must agree with it; a card that disagrees is skipped, since one of
the two is wrong and nothing says which.

The page is the room's own curation. LibCal files its events under several
calendars (Harvard Library Events, FAS Public Events) and under the building
they are held in, so no LibCal query returns exactly the room's programme.

A card without a LibCal link has no year anywhere and is skipped. Past events
stay on the page for the season; the validator drops them by date.
"""
import json
import logging
import re
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

PAGE = "https://library.harvard.edu/libraries/woodberry-poetry-room/events"
LIBCAL_EVENT = re.compile(r"^https://libcal\.library\.harvard\.edu/event/\d+")

# "Thursday, October 15, 6:00pm"
PRINTED = re.compile(
    r"(?P<weekday>Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+"
    r"(?P<month>[A-Z][a-z]+)\s+(?P<day>\d{1,2})(?:st|nd|rd|th)?,?\s+"
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>[ap])\.?m\.?", re.I)


class WoodberryPoetryRoomScraper(BaseFeedScraper):
    # Lamont Library, Room 330. A card naming another Harvard room without a
    # city ("Edison-Newman Room, Houghton Library") is on the same campus.
    HOME = Place("Woodberry Poetry Room", "11 Quincy Street", "Cambridge", "02138")
    CATEGORY = EventCategory.LECTURES

    def __init__(self):
        super().__init__(source_name="Woodberry Poetry Room", source_url=PAGE)

    def scrape_events(self) -> List[EventCreate]:
        soup = BeautifulSoup(self.fetch_html(self.source_url), "html.parser")
        cards = soup.select("section.hl__stacked-promo")
        if not cards:
            raise ValueError(f"{self.source_name}: no event cards on {self.source_url} — has the page changed?")
        events = []
        for card in cards:
            event = self.parse_card(card)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name} ({len(cards)} cards)")
        return events

    def parse_card(self, card) -> Optional[EventCreate]:
        link = card.select_one(".hl__stacked-promo__title a[href]")
        if link is None:
            return None
        title = self.text(link.get_text(" "))
        url = link["href"].strip()
        if not self.is_event(title):
            return None
        description_block = card.select_one(".hl__stacked-promo__description")
        lines = [self.clean_text(s) for s in description_block.stripped_strings] if description_block else []
        printed = PRINTED.search(" ".join(lines))
        if printed is None:
            logger.warning(f"Skipping '{title}' - no printed date on its card ({url})")
            return None
        if not LIBCAL_EVENT.match(url):
            logger.warning(f"Skipping '{title}' - its card prints no year and links nowhere that does ({url})")
            return None

        detail = BeautifulSoup(self.fetch_html(url), "html.parser")
        data = self.event_json(detail)
        start = self.instant(data.get("startDate"))
        if start is None:
            logger.warning(f"Skipping '{title}' - LibCal gives no start ({url})")
            return None
        if not self.agrees(printed, start):
            logger.warning(f"Skipping '{title}' - card says {printed.group(0)!r}, LibCal says {start} ({url})")
            return None
        if "cancel" in str(data.get("eventStatus") or "").lower():
            logger.info(f"{self.source_name}: skipping cancelled '{title}'")
            return None
        end = self.instant(data.get("endDate"))
        if end is not None and end <= start:
            end = None

        where = self.where(title, lines[lines.index(printed.group(0)) + 1:] if printed.group(0) in lines else lines[1:])
        if where is None:
            return None

        body = detail.select_one("#s-lc-event-desc")
        description = self.text(str(body)) if body else self.text(data.get("description"))
        if len(description) < 20:
            description = self.fallback_description(title, where)

        image = card.select_one(".hl__stacked-promo__image img[src]")
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
            cost="Free",
            source_name=self.source_name,
            source_url=url,
            image_url=data.get("image") or (f"https://library.harvard.edu{image['src']}"
                                            if image and image["src"].startswith("/") else None),
        )

    # -- helpers --------------------------------------------------------- #

    @staticmethod
    def event_json(detail: BeautifulSoup) -> dict:
        for script in detail.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("@type") == "Event":
                return data
        return {}

    @staticmethod
    def instant(value) -> Optional[datetime]:
        """ISO 8601 with offset -> naive Eastern wall clock."""
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None  # an offset-less time is a guess about its zone
        return to_eastern_naive(parsed).replace(second=0, microsecond=0)

    @staticmethod
    def agrees(printed: re.Match, start: datetime) -> bool:
        hour = int(printed["hour"]) % 12 + (12 if printed["ampm"].lower() == "p" else 0)
        return (start.strftime("%A").lower() == printed["weekday"].lower()
                and start.strftime("%B").lower() == printed["month"].lower()
                and start.day == int(printed["day"])
                and (start.hour, start.minute) == (hour, int(printed["minute"] or 0)))

    def where(self, title: str, lines: List[str]) -> Optional[Place]:
        """The card's place line: "CCVA Auditorium, 24 Quincy Street" / "Woodberry Poetry Room"."""
        line = lines[0] if lines else ""
        if not line:
            return self.HOME
        parts = [p.strip() for p in line.split(",") if p.strip()]
        street_at = next((i for i, p in enumerate(parts) if p[:1].isdigit()), None)
        if street_at is None:
            venue, street = line, None
        else:
            venue, street = ", ".join(parts[:street_at]) or parts[street_at], ", ".join(parts[street_at:])
        return self.place(title, venue, street, None)
