"""Scraper for the Museum of Science special events

The museum's `/events` page lists a handful of dated special events — the rest of
its programme is daily exhibits and shows, which are not calendar entries. About
a dozen listings is the real number, not a truncated one.

The page renders client-side from a JSON endpoint, `/api/v1/event-listing/...`,
and this reads that endpoint directly with plain HTTP. Each result is one
pre-rendered listing card; its date is a free-text field written by hand:

    "Saturday, November 14, 2026 | 10:00 am – 4:00 pm"
    "Sunday, October 11 | 6:00 – 9:00 pm"                     no year
    "Friday, October 16 | Doors open at 7:30 pm; Performance starts at 8:00 pm"
    "Thursday, October 29 at 7:30 pm; Friday, October 30 at 6:30 pm and 8:00 pm"
    "Masked Access Hours, Saturday, November 7th | 8–9 am"

The previous scraper handed dateutil everything after the "|", which rejected
the last three shapes outright: four of eleven listings were dropped. Here the
date and the first time are each found with a regex.

A missing year is decided by the printed weekday, never by rolling forward.
The previous scraper moved any date that had passed into next year, which
published "Strange Land" (venue: "Wednesday, September 23 | 7:30 pm", i.e.
2026, already over) as 2027-09-23 — a Thursday. Of last year, this year and
next, at most one puts a month and day on a given weekday; that year is used,
and with no match (or no weekday to check against) the event is skipped.
"""
import logging
import re
from datetime import date, datetime
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper, USER_AGENT

logger = logging.getLogger(__name__)

BASE = "https://www.mos.org"
# The request the /events page makes for its listing. 96 and 101 are the two
# event-type terms the page filters on ("Event" and the Public Science Common's).
API_URL = f"{BASE}/api/v1/event-listing/event_detail/96+101/all/all/all/all"
PER_PAGE = 18          # the largest page size the endpoint offers
MAX_PAGES = 5

# An honest client identity. Never a browser's — see CLAUDE.md "Traps".

VENUE = "Museum of Science"
ADDRESS = "1 Science Park"

_MONTHS = ("january", "february", "march", "april", "may", "june", "july",
           "august", "september", "october", "november", "december")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

# "Saturday, November 7th", "Sunday, October 11", "Saturday, November 14, 2026".
# The weekday may be absent; the month must be a month name.
DATE = re.compile(
    r"(?:\b(?P<weekday>mon(?:day)?|tue(?:s(?:day)?)?|wed(?:nesday)?|thu(?:r(?:s(?:day)?)?)?"
    r"|fri(?:day)?|sat(?:urday)?|sun(?:day)?)\b\.?,?\s+)?"
    r"\b(?P<month>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?"
    r"|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?\s+"
    r"(?P<day>\d{1,2})(?:st|nd|rd|th)?\b"
    r"(?:,?\s+(?P<year>\d{4})\b)?",
    re.I)

# A clock time with a meridiem of its own ("7:30 pm", "8 a.m."), or a bare one
# that opens a range ending in one ("6:00 – 9:00 pm", "8–9 am").
_MERIDIEM = r"(?:\s*(?P<{0}>[ap])\.?\s?m\b\.?)"
TIME = re.compile(
    r"(?<![\d:])(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?(?!\d)" + _MERIDIEM.format("mer") + "?"
    r"(?:\s*(?:–|—|-|to)\s*(?P<end_hour>\d{1,2})(?::(?P<end_minute>\d{2}))?(?!\d)"
    + _MERIDIEM.format("end_mer") + ")?",
    re.I)


def _to_24h(hour: int, minute: int, meridiem: str) -> Optional[Tuple[int, int]]:
    if not (1 <= hour <= 12 and 0 <= minute <= 59):
        return None
    hour = hour % 12 + (12 if meridiem.lower() == "p" else 0)
    return hour, minute


def _time_of(m) -> Optional[Tuple[int, int]]:
    hour, minute = int(m["hour"]), int(m["minute"] or 0)
    if m["mer"]:
        return _to_24h(hour, minute, m["mer"])
    if m["end_mer"]:
        # "6:00 – 9:00 pm" borrows the end's meridiem, unless that would put
        # the start after the end: "11:00 – 1:00 pm" starts in the morning.
        start = _to_24h(hour, minute, m["end_mer"])
        end = _to_24h(int(m["end_hour"]), int(m["end_minute"] or 0), m["end_mer"])
        if start and end and start > end and m["end_mer"].lower() == "p":
            start = _to_24h(hour, minute, "a")
        return start
    return None


def first_time(text: str) -> Optional[Tuple[int, int]]:
    """The first clock time in `text` that carries, or borrows, a meridiem.

    A bare number with no meridiem anywhere near it is not taken as a time. A
    doors-open time is passed over when a later time follows it: "Doors open at
    7:30 pm; Performance starts at 8:00 pm" starts at 8.
    """
    found = [(m, t) for m in TIME.finditer(text) if (t := _time_of(m))]
    if not found:
        return None
    m, t = found[0]
    if len(found) > 1 and re.search(r"\bdoors?\b[^;.|]*$", text[:m.start()], re.I):
        return found[1][1]
    return t


def parse_when(text: str, today: date) -> Optional[Tuple[datetime, bool]]:
    """Read a listing's date text as (start, all_day), or None if it cannot be dated.

    `today` only bounds which years are candidates when none is printed; the
    printed weekday chooses among them.
    """
    match = DATE.search(text or "")
    if not match:
        return None
    month = _MONTHS.index(next(m for m in _MONTHS if m.startswith(match["month"].lower()))) + 1
    day = int(match["day"])

    if match["year"]:
        years = [int(match["year"])]
    else:
        years = [today.year - 1, today.year, today.year + 1]

    candidates = []
    for year in years:
        try:
            candidates.append(date(year, month, day))
        except ValueError:          # 31 November, 29 February in a common year
            continue

    if match["weekday"]:
        weekday = next(i for i, w in enumerate(_WEEKDAYS) if w.startswith(match["weekday"].lower()))
        candidates = [c for c in candidates if c.weekday() == weekday]
    elif not match["year"]:
        # No year and no weekday: nothing printed decides the year.
        return None
    if len(candidates) != 1:
        return None
    day_ = candidates[0]

    clock = first_time(text[match.end():])
    if clock is None:
        return datetime(day_.year, day_.month, day_.day), True
    return datetime(day_.year, day_.month, day_.day, *clock), False


class MuseumOfScienceScraper(BaseScraper):
    """Scraper for Museum of Science events"""

    def __init__(self):
        super().__init__(source_name=VENUE, source_url=f"{BASE}/events", use_selenium=False)

    def scrape_events(self) -> List[EventCreate]:
        cards = []
        for page in range(MAX_PAGES):
            # No try/except: a refusal or a non-JSON body (a challenge page)
            # must fail the source, not read as a museum with nothing on.
            response = requests.get(
                API_URL,
                params={"items_per_page": PER_PAGE, "sort_by": "date_asc", "page": page},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            cards += [r.get("event_listing") or "" for r in payload.get("results") or []]
            if page + 1 >= (payload.get("pager") or {}).get("total_pages", 1):
                break

        events = self.parse_cards(cards, self.today())
        logger.info(f"Scraped {len(events)} events from {VENUE} ({len(cards)} listed)")
        return events

    @staticmethod
    def today() -> date:
        """Only bounds the candidate years for a date printed without one."""
        return date.today()

    def parse_cards(self, cards: List[str], today: date) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()
        for card in cards:
            item = BeautifulSoup(card, "html.parser").find(class_="listing-item")
            event = self._parse_item(item, today) if item else None
            if event is None or event.source_url in seen:
                continue
            seen.add(event.source_url)
            events.append(event)
        return events

    def _parse_item(self, item, today: date) -> Optional[EventCreate]:
        link = item.find("a", class_="listing-item__image", href=True) or item.find("a", href=True)
        if not link:
            return None
        path = link["href"]
        if "/events/" not in path:
            return None

        title = ""
        title_el = item.find(class_=re.compile(r"listing-item__(title|heading)"))
        if title_el:
            title = self.clean_text(title_el.get_text())
        if not title:
            # Fall back to the anchor whose text is not the image alt
            for anchor in item.find_all("a", href=True):
                text = self.clean_text(anchor.get_text())
                if text and text.lower() != "image":
                    title = text
                    break
        if len(title) < 3:
            return None

        date_el = item.find(class_="listing-item__date")
        date_text = self.clean_text(date_el.get_text()) if date_el else ""
        when = parse_when(date_text, today)
        if when is None:
            # Never guess — see docs/ARCHITECTURE.md "Layer 1 — Scrapers".
            logger.warning(f"Skipping '{title}' - no parseable date ({date_text!r})")
            return None
        start, all_day = when

        body = item.find(class_=re.compile(r"listing-item__(summary|description|content-body)"))
        description = self.clean_text(body.get_text()) if body else ""
        if len(description) < 20:
            description = f"{title} at the {VENUE}, Science Park, Boston."

        image = item.find("img", src=True)
        image_url = image["src"] if image else None
        if image_url and image_url.startswith("/"):
            image_url = f"{BASE}{image_url}"

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            all_day=all_day,
            source_url=f"{BASE}{path}" if path.startswith("/") else path,
            source_name=self.source_name,
            venue_name=VENUE,
            street_address=ADDRESS,
            city="Boston",
            state="MA",
            zip_code="02114",
            category=EventCategory.ARTS_CULTURE,
            image_url=image_url,
        )
