"""MIT List Visual Arts Center — talks, tours, screenings and public programs.

listart.mit.edu is Drupal. `/calendar` lists upcoming events as cards; each
event's own page carries the rest. Neither has a feed.

**When.** The event page's sidebar has one "Day & Time" line per occurrence:

    December 3, 5:30 - 6:30PM          one evening
    December 9, 12 - 7PM               } a drop-in programme on three days,
    December 10, 12 - 7PM              } each with its own hours
    December 11, 12 - 6PM              }
    June 23 - July 12, all day         a screening programme spanning weeks

Every occurrence becomes an event; a span is not one occasion and is skipped.
The lines print no year. The year comes from the calendar card, which prints
each date in full ("December 9, 2026") or as `<time datetime>`; a line whose
date is not on its card is skipped. Where the card has a `<time datetime>`,
the line's start must agree with it, or the event is skipped as unreadable.

Times follow Smart Date's format, which writes the meridiem once when both
ends share it ("5:30 - 6:30PM" is 5:30 PM to 6:30 PM; "12 - 7PM" is noon to
7 PM).

**Who.** Cards are tagged with event types. "Member Event" (members'
exhibition previews) and "MIT Community" are not open to the public and are
skipped.

**Where.** The sidebar's "Location": the List itself ("MIT List Visual Arts
Center, 20 Ames Street, Building E15, Cambridge, MA 02139"), one of its rooms
("Bakalar Gallery"), or a public-art tour's meeting point ("77 Massachusetts
Ave Cambridge, MA 02139"). A place with no city is in the List's building, as
base_feed treats any single-home source.
"""
import logging
import re
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import LOCAL_CITIES, BaseFeedScraper, Place

logger = logging.getLogger(__name__)

SITE = "https://listart.mit.edu"

NOT_PUBLIC = {"Member Event", "MIT Community"}

MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), start=1)}

# "December 3, 5:30 - 6:30PM" / "December 9, 2026" / "December 9"
DATE_LINE = re.compile(r"^(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})(?:,\s*(?P<year>\d{4}))?(?:,\s*(?P<rest>.*))?$")
TIMES = re.compile(
    r"^(?P<h1>\d{1,2})(?::(?P<m1>\d{2}))?\s*(?P<a1>[AP]M)?"
    r"(?:\s*[-–]\s*(?P<h2>\d{1,2})(?::(?P<m2>\d{2}))?\s*(?P<a2>[AP]M))?$", re.I)
ALL_DAY = re.compile(r"^all\s*day$", re.I)

# "..., Cambridge, MA 02139" / "77 Massachusetts Ave Cambridge, MA 02139" / "..., Boston, MA"
CITY_STATE = re.compile(r"(?:^|[\s,])(?P<city>[A-Z][A-Za-z]+)\s*,\s*(?:MA|Massachusetts)\b\.?\s*(?P<zip>\d{5})?")
STREET = re.compile(r"^\d+[A-Za-z]?\s+\S")

CATEGORY_BY_TYPE = {
    "Talk / Lecture": EventCategory.LECTURES,
    "Screening": EventCategory.FILM,
    "Exhibition Tour": EventCategory.ARTS_CULTURE,
    "Public Art Tour": EventCategory.ARTS_CULTURE,
}


def clock(hour: int, minute: int, meridiem: str) -> Tuple[int, int]:
    hour %= 12
    return (hour + 12 if meridiem.upper() == "PM" else hour), minute


def parse_times(text: str) -> Optional[Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]]:
    """'5:30 - 6:30PM' -> ((17, 30), (18, 30)); 'all day' -> (None, None); unreadable -> None."""
    text = " ".join(text.split())
    if ALL_DAY.match(text):
        return None, None
    m = TIMES.match(text)
    if not m:
        return None
    a2 = m.group("a2")
    a1 = m.group("a1") or a2
    if not a1:
        return None  # a bare "6" is not a time
    start = clock(int(m.group("h1")), int(m.group("m1") or 0), a1)
    if not m.group("h2"):
        return start, None
    end = clock(int(m.group("h2")), int(m.group("m2") or 0), a2)
    if not m.group("a1") and start > end:
        # Smart Date writes one meridiem only when both ends share it; this
        # guards a hand-typed "11 - 1PM" all the same.
        start = clock(int(m.group("h1")), int(m.group("m1") or 0), "AM")
    return start, end


class MITListCenterScraper(BaseFeedScraper):
    HOME = Place("MIT List Visual Arts Center", "20 Ames Street", "Cambridge", "02139")
    CATEGORY = EventCategory.ARTS_CULTURE
    # The calendar page also shows a few recent past events, in their own section
    SECTION = ".section--view--events-upcoming"

    def __init__(self):
        super().__init__(source_name="MIT List Visual Arts Center", source_url=f"{SITE}/calendar")

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        for card in self.parse_listing(self.fetch_html(self.source_url)):
            if NOT_PUBLIC & set(card["types"]):
                logger.info(f"{self.source_name}: skipping '{card['title']}' - {', '.join(card['types'])}")
                continue
            try:
                detail = self.fetch_html(card["url"])
            except Exception as e:
                logger.warning(f"{self.source_name}: could not read {card['url']}: {e}")
                continue
            events.extend(self.parse_event(card, detail))
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- the calendar page ------------------------------------------------ #

    def parse_listing(self, html: str) -> List[dict]:
        """Upcoming cards: title, url, event types, dates in full, and any <time> starts."""
        soup = BeautifulSoup(html, "html.parser")
        section = soup.select_one(self.SECTION)
        if section is None:
            raise ValueError(f"{self.source_name}: no '{self.SECTION}' section on {self.source_url}")
        cards = []
        for card in section.select("article.card--event"):
            link = card.select_one("a.card__link[href]")
            title = card.select_one(".card__title")
            if not (link and title):
                continue
            subtitle = card.select_one(".card__subtitle")
            days: Dict[Tuple[int, int], int] = {}
            starts: Dict[date, datetime] = {}
            for t in (subtitle.select("time[datetime]") if subtitle else []):
                try:
                    when = datetime.fromisoformat(t["datetime"])
                except ValueError:
                    continue
                days[(when.month, when.day)] = when.year
                if "T" in t["datetime"]:
                    local = to_eastern_naive(when)
                    starts.setdefault(local.date(), local)
            for line in (subtitle.get_text("\n").split("\n") if subtitle else []):
                m = DATE_LINE.match(line.strip())
                if m and m.group("year") and m.group("month").lower() in MONTHS:
                    days[(MONTHS[m.group("month").lower()], int(m.group("day")))] = int(m.group("year"))
            cards.append({
                "title": self.clean_text(title.get_text(" ")),
                "url": urljoin(SITE, link["href"]),
                "types": [self.clean_text(x.get_text(" ")) for x in card.select(".tags__item")],
                "years": days,
                "starts": starts,
            })
        return cards

    # -- one event page --------------------------------------------------- #

    def sidebar(self, soup: BeautifulSoup) -> Dict[str, List[str]]:
        fields: Dict[str, List[str]] = {}
        for field in soup.select(".node__details__aside .field--sidebar-display"):
            label = field.select_one(".field--sidebar-display__label")
            values = [self.clean_text(v.get_text(" ")) for v in field.select(".field--sidebar-display__text")]
            if label:
                fields[self.clean_text(label.get_text(" "))] = [v for v in values if v]
        return fields

    def parse_event(self, card: dict, html: str) -> List[EventCreate]:
        soup = BeautifulSoup(html, "html.parser")
        fields = self.sidebar(soup)
        title = card["title"]
        if not self.is_event(title):
            return []

        where = self.place_of(title, (fields.get("Location") or [""])[0], "Virtual" in card["types"])
        if where is None:
            return []

        body = soup.select_one(".field--node--body")
        description = self.clean_text(body.get_text(" ")) if body else ""
        if len(description) < 20:
            description = self.fallback_description(title, where)
        admission = " ".join(fields.get("Admission") or []) or None
        image = soup.select_one('meta[property="og:image"]')
        category = next((CATEGORY_BY_TYPE[t] for t in card["types"] if t in CATEGORY_BY_TYPE), None)

        events = []
        lines = fields.get("Day & Time") or []
        if not lines:
            logger.warning(f"Skipping '{title}' - no Day & Time on {card['url']}")
        for line in lines:
            when = self.occurrence(card, line)
            if when is None:
                continue
            start, end, all_day = when
            events.append(EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start,
                end_datetime=end,
                all_day=all_day,
                venue_name=where.venue,
                street_address=where.street,
                city=where.city,
                state="MA",
                zip_code=where.zip_code,
                category=category or self.categorize(title),
                cost=admission[:100] if admission else None,
                registration_required=bool(admission and "registration required" in admission.lower()),
                source_name=self.source_name,
                source_url=card["url"],
                image_url=(image.get("content") if image else None) or None,
            ))
        return events

    def occurrence(self, card: dict, line: str) -> Optional[Tuple[datetime, Optional[datetime], bool]]:
        """One 'Day & Time' line -> (start, end, all_day), or None to skip it."""
        title, url = card["title"], card["url"]
        m = DATE_LINE.match(line)
        if not m or m.group("month").lower() not in MONTHS or not m.group("rest"):
            logger.warning(f"Skipping '{title}' {line!r} - not one dated occasion ({url})")
            return None
        month, day = MONTHS[m.group("month").lower()], int(m.group("day"))
        year = int(m.group("year")) if m.group("year") else card["years"].get((month, day))
        if year is None:
            logger.warning(f"Skipping '{title}' {line!r} - its card gives no year for that date ({url})")
            return None
        times = parse_times(m.group("rest"))
        if times is None:
            logger.warning(f"Skipping '{title}' {line!r} - unreadable time ({url})")
            return None
        start_hm, end_hm = times
        try:
            day_start = datetime(year, month, day)
            start = day_start.replace(hour=start_hm[0], minute=start_hm[1]) if start_hm else day_start
            end = day_start.replace(hour=end_hm[0], minute=end_hm[1]) if end_hm else None
        except ValueError:
            logger.warning(f"Skipping '{title}' {line!r} - no such date or time in {year} ({url})")
            return None
        if start_hm is None:
            return day_start, None, True
        listed = card["starts"].get(start.date())
        if listed is not None and listed != start:
            logger.warning(f"Skipping '{title}' {line!r} - the calendar card says {listed} ({url})")
            return None
        return start, (end if end and end > start else None), False

    def place_of(self, title: str, location: str, virtual: bool) -> Optional[Place]:
        """'Name, 20 Ames Street, Building E15, Cambridge, MA 02139' -> Place."""
        if virtual and not location:
            return Place("Online", None, None, None)
        if not location:
            return self.HOME
        m = CITY_STATE.search(location)
        if m is None:
            # A room with no address ("Bakalar Gallery"): the List's building
            return self.place(title, location, None, None)
        city = m.group("city")
        head = location[:m.start("city")].strip(" ,")
        parts = [p.strip() for p in head.split(",") if p.strip()]
        street = next((p for p in parts if STREET.match(p)), None)
        venue = parts[0] if parts else (street or city)
        if city not in LOCAL_CITIES:
            logger.info(f"{self.source_name}: skipping '{title}' - in {city}, outside the calendar's area")
            return None
        return self.place(title, venue, street, city, m.group("zip"))
