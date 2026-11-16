"""Mass Audubon programs at Magazine Beach Nature Center, Cambridge.

/programs/magazine-beach lists the centre's programs nine to a page (`?page=N`,
followed by rel=next). Each card carries its next meeting twice in machine
form — `data-next_meeting="2026-11-21 08:00:00"` (Eastern wall clock) and
`data-next_meeting_ts="1795266000"` (the same instant as an epoch) — and once
in print: "Saturday, November 21" / "8:00-10:00am". A card is kept only when
all three agree; the printed range supplies the end time. A multi-session
program prints a span rather than one date, fails the check, and is skipped
with a warning rather than guessed at.

Some programs meet elsewhere — the "T-Stop Trek" walks start at MBTA stations
in Revere, Malden, Newton — and base_feed drops those as outside the area. A
card with no place at all is skipped (HOME is None, because of those walks).
"""
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional, Tuple
from urllib.parse import urljoin

import requests

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import BaseFeedScraper

logger = logging.getLogger(__name__)

LISTING = "https://www.massaudubon.org/programs/magazine-beach"
MAX_PAGES = 6

PRINTED_DATE = re.compile(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+([A-Z][a-z]+)\s+(\d{1,2})$")
TIME_RANGE = re.compile(
    r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\s*[-–]\s*(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", re.I)


def _hour(h: str, meridiem: str) -> int:
    h = int(h) % 12
    return h + 12 if meridiem.lower() == "pm" else h


def printed_range(text: str) -> Optional[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """'8:00-10:00am' -> ((8, 0), (10, 0)); '11:00-1:00pm' -> ((11, 0), (13, 0))."""
    m = TIME_RANGE.match(text.strip())
    if not m:
        return None
    h1, m1, mer1, h2, m2, mer2 = m.groups()
    end = (_hour(h2, mer2), int(m2 or 0))
    start = (_hour(h1, mer1 or mer2), int(m1 or 0))
    if not mer1 and start > end:          # "11:00-1:00pm" starts in the morning
        start = (_hour(h1, "am"), start[1])
    return start, end


class MassAudubonMagazineBeachScraper(BaseFeedScraper):
    HOME = None
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Mass Audubon Magazine Beach", source_url=LISTING)

    def fetch(self, url: str) -> str:
        response = requests.get(url, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        url: Optional[str] = self.source_url
        seen = set()
        for _ in range(MAX_PAGES):
            soup = self.parse_html(self.fetch(url))
            cards = soup.select("article.event-card")
            if not cards and not events:
                raise ValueError(f"{self.source_name}: no event cards at {url} — has the listing markup changed?")
            for card in cards:
                event = self.parse_card(card)
                if event and (event.source_url, event.start_datetime) not in seen:
                    seen.add((event.source_url, event.start_datetime))
                    events.append(event)
            nxt = soup.select_one('a[rel="next"]')
            if not nxt or not nxt.get("href"):
                break
            url = urljoin(LISTING, nxt["href"])
        else:
            logger.warning(f"{self.source_name}: stopped after {MAX_PAGES} pages; the listing has more")
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def start_of(self, card, title: str, url: str) -> Optional[datetime]:
        stamp, epoch = card.get("data-next_meeting"), card.get("data-next_meeting_ts")
        try:
            start = datetime.strptime(stamp or "", "%Y-%m-%d %H:%M:%S")
            instant = to_eastern_naive(datetime.fromtimestamp(int(epoch), tz=timezone.utc))
        except (TypeError, ValueError):
            logger.warning(f"Skipping '{title}' - no machine-readable meeting time ({stamp!r}, {epoch!r}) ({url})")
            return None
        if start != instant or start.second:
            logger.warning(f"Skipping '{title}' - next_meeting {stamp} and its epoch {epoch} disagree ({url})")
            return None
        return start

    def parse_card(self, card) -> Optional[EventCreate]:
        title = self.text(card.select_one(".event-card__content__title").get_text(" ")
                          if card.select_one(".event-card__content__title") else "")
        link = card.find("a", href=True)
        url = urljoin(LISTING, link["href"]) if link else self.source_url
        if not self.is_event(title):
            return None

        start = self.start_of(card, title, url)
        if start is None:
            return None

        lines = [self.text(p.get_text(" ")) for p in card.select(".event-card__content__date p")]
        lines = [line for line in lines if line]
        printed = PRINTED_DATE.match(lines[0]) if lines else None
        if not printed or (printed.group(1), printed.group(2), int(printed.group(3))) != \
                (start.strftime("%A"), start.strftime("%B"), start.day):
            logger.warning(f"Skipping '{title}' - printed date {lines[:1]} does not match next meeting {start} ({url})")
            return None
        end = None
        span = printed_range(lines[1]) if len(lines) > 1 else None
        if span:
            if span[0] != (start.hour, start.minute):
                logger.warning(f"Skipping '{title}' - printed time {lines[1]!r} does not match {start:%H:%M} ({url})")
                return None
            end = start.replace(hour=span[1][0], minute=span[1][1])
            if end <= start:
                end = None

        place_li = card.select_one(".event-card__content__place")
        fields = [self.text(s.get_text(" ")) for s in place_li.select(".ezstring-field")] if place_li else []
        venue, city = (fields + [None, None])[:2]
        where = self.place(title, venue, None, city)
        if where is None:
            return None

        audience = self.text(card.select_one(".event-card__content__ages").get_text(" ")) \
            if card.select_one(".event-card__content__ages") else ""
        prices = [self.text(li.get_text(" ")) for li in card.select(".cost-li")]
        cost = "; ".join(p for p in prices if p) or None
        description = f"{title}, a Mass Audubon program at {where.venue}, {where.city}."
        if audience:
            description += f" For: {audience}."

        lat = lng = None
        if place_li is not None:
            try:
                lat, lng = float(place_li["data-latitude"]), float(place_li["data-longitude"])
            except (KeyError, ValueError):
                lat = lng = None

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
            latitude=lat,
            longitude=lng,
            category=self.categorize(title),
            cost=cost,
            source_name=self.source_name,
            source_url=url,
        )
