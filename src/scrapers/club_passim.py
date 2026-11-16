"""Club Passim, Harvard Square — and the free summer series Passim runs outdoors.

**The club calendar.** /live-music/ draws a fullCalendar per month from a
JavaScript array written into the page: one object per performance with
`date` ("2026-11-16"), `showtime` ("7:30PM"), `start`, title, subtitle,
genres and the ticket button. The array is repeated once per month on the
page, so performances are deduplicated by (post, date, showtime).

`start` reads "2026-11-16T19:30:00+0000". The offset is a mislabel: 19:30 is
the 7:30 PM show the page prints, not 2:30 PM Eastern (the Harvard Square
listing of the same shows agrees). So the time comes from `date` + `showtime`
as printed, and `start`'s wall clock must agree with them or the performance
is skipped. `showtime` is the show, not the doors.

**Where.** Most performances are in the club, but Passim also presents
elsewhere, and the calendar has no venue field:

  - a title ending "… at <place>" names it ("Tall Heights at Arrow Street
    Arts", "… at the Crane Estate"). A place in Cambridge or Somerville that we
    know is used; any other is skipped — the Crane Estate is in Ipswich;
  - a show whose tickets are sold by someone other than Passim's own box office
    is not a club show. "Scenic Songs: A Hiking Concert with …" names no place
    and sells through thetrustees.org; it is a hike on a Trustees reservation;
  - a festival names its rooms only in the post's body. The Boston Celtic Music
    Festival hangs performances in different buildings off one calendar post,
    so each festival post's page is read and each performance is placed by the
    schedule line with its date and start ("Friday, January 15th, 8pm-10pm …
    @ Arts at the Armory"). A performance no line accounts for is skipped.

**The summer series** (Kendall Square, Danehy Park, Palmer Street, Science
Plaza) live on their own pages, not in the club calendar. Each lists
performances as weekday + "Aug 26" + "12PM", with no year; the year is the one
whose calendar puts that date on that weekday (as in art.py), and a performance
whose weekday matches no nearby year is skipped. Between seasons the pages hold
last summer's leftovers, which EventValidator drops as past.
"""
import html
import logging
import re
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.art import MONTHS, WEEKDAYS, year_for_weekday
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

SITE = "https://www.passim.org"
CLUB = Place("Club Passim", "47 Palmer Street", "Cambridge", "02138")

# Places Passim presents in, as titles and festival schedules name them.
# Matched as substrings of the lowercased name, most specific first.
KNOWN_PLACES: Tuple[Tuple[str, Place], ...] = (
    ("club passim", CLUB),
    ("arrow street arts", Place("Arrow Street Arts", "2 Arrow Street", "Cambridge", "02138")),
    ("crystal ballroom", Place("Crystal Ballroom at Somerville Theatre", "55 Davis Square", "Somerville", "02144")),
    ("somerville theat", Place("Somerville Theatre", "55 Davis Square", "Somerville", "02144")),
    ("armory", Place("Arts at the Armory", "191 Highland Avenue", "Somerville", "02143")),
    ("the rockwell", Place("The Rockwell", "255 Elm Street", "Somerville", "02144")),
    ("first parish", Place("First Parish in Cambridge", "3 Church Street", "Cambridge", "02138")),
    ("sanders theatre", Place("Sanders Theatre", "45 Quincy Street", "Cambridge", "02138")),
    ("the sinclair", Place("The Sinclair", "52 Church Street", "Cambridge", "02138")),
    ("passim", CLUB),
)

# Passim's own box office. A show sold anywhere else is not a club show
# unless its title names the place.
OWN_BOX_OFFICE = "passim.my.salesforce-sites.com"

# "… at Arrow Street Arts", "… at the Crane Estate": a capitalised place at the end.
TITLE_PLACE = re.compile(r"\s+at\s+((?:the\s+)?[A-Z][\w'’&.-]*(?:\s+[A-Z][\w'’&.-]*)*)\s*$")
FESTIVAL = re.compile(r"\bfestival\b", re.I)
CLUB_CLOSED = re.compile(r"^\s*(club\s+)?closed\b", re.I)

FIELD = re.compile(r"(\w+): (?:\"((?:[^\"\\]|\\.)*)\"|'((?:[^'\\]|\\.)*)')")
CALENDAR_EVENT = re.compile(r"\{title: \".*?\}(?=,\s*\{title: |,?\s*\])", re.S)
SHOWTIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*([AP]M)$", re.I)

# "Friday, January 15th, 8pm-10pm. $30 … @ Arts at the Armory, with …"
SCHEDULE_LINE = re.compile(
    r"(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day,\s+([A-Z][a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,\s+"
    r"(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\.?[^@]{0,200}?@\s*([^,\n]+)")


def place_named(name: str) -> Optional[Place]:
    key = html.unescape(name).lower()
    for needle, place in KNOWN_PLACES:
        if needle in key:
            return place
    return None


def clock(text: str) -> Optional[Tuple[int, int]]:
    """'7:30PM' / '7PM' / '12PM' -> (19, 30) / (19, 0) / (12, 0)."""
    m = SHOWTIME.match((text or "").strip())
    if not m:
        return None
    hour = int(m.group(1)) % 12 + (12 if m.group(3).upper() == "PM" else 0)
    return hour, int(m.group(2) or 0)


def fetch(url: str, headers: dict) -> str:
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    return response.text


class ClubPassimScraper(BaseFeedScraper):
    """The club's calendar page, plus the page of each festival on it."""

    HOME = CLUB
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Club Passim", source_url=f"{SITE}/live-music/")

    def fetch_page(self, url: str) -> str:
        return fetch(url, self.get_browser_headers())

    def scrape_events(self) -> List[EventCreate]:
        records = self.calendar_records(self.fetch_page(self.source_url))
        if not records:
            raise ValueError(f"{self.source_name}: no calendar entries on {self.source_url} "
                             f"— has the page stopped embedding its fullCalendar data?")

        festival_pages: Dict[str, Optional[str]] = {}
        events = []
        for record in records:
            if self._needs_schedule(record) and record["link"] not in festival_pages:
                try:
                    festival_pages[record["link"]] = self.fetch_page(record["link"])
                except Exception as e:
                    logger.warning(f"{self.source_name}: could not read festival page {record['link']}: {e}")
                    festival_pages[record["link"]] = None
            event = self.parse_record(record, festival_pages.get(record["link"]))
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- the page's data -------------------------------------------------- #

    @staticmethod
    def calendar_records(page: str) -> List[dict]:
        """Every distinct performance in the page's fullCalendar arrays."""
        records, seen = [], set()
        for match in CALENDAR_EVENT.finditer(page):
            record = {key: (dq if dq is not None and dq != "" else sq)
                      for key, dq, sq in FIELD.findall(match.group(0))}
            key = (record.get("postID"), record.get("date"), record.get("showtime"))
            if None in key or key in seen:
                continue
            seen.add(key)
            records.append(record)
        return records

    def _needs_schedule(self, record: dict) -> bool:
        title = html.unescape(record.get("title", ""))
        return bool(FESTIVAL.search(title)) and not TITLE_PLACE.search(title) and bool(record.get("link"))

    # -- one performance ---------------------------------------------------- #

    def start_of(self, record: dict) -> Optional[datetime]:
        """`date` + `showtime`, checked against `start`'s wall clock."""
        hm = clock(record.get("showtime", ""))
        try:
            day = date.fromisoformat(record.get("date", ""))
        except ValueError:
            return None
        if hm is None:
            return None
        start = datetime(day.year, day.month, day.day, *hm)
        if (record.get("start") or "")[:16] != start.strftime("%Y-%m-%dT%H:%M"):
            return None
        return start

    def where(self, record: dict, title: str, start: datetime, festival_page: Optional[str]) -> Optional[Place]:
        named = TITLE_PLACE.search(title)
        if named:
            place = place_named(named.group(1))
            if place is None:
                logger.info(f"{self.source_name}: skipping '{title}' - '{named.group(1)}' is not a "
                            f"Cambridge/Somerville place we know")
            return place

        seller = re.search(r'href="([^"]+)"', html.unescape(record.get("ticketButton") or ""))
        host = urlparse(seller.group(1)).netloc if seller else ""
        if host and host != OWN_BOX_OFFICE:
            logger.info(f"{self.source_name}: skipping '{title}' - sold by {host}, venue not stated")
            return None

        if self._needs_schedule(record):
            return self.festival_place(title, start, festival_page)
        return CLUB

    def festival_place(self, title: str, start: datetime, page: Optional[str]) -> Optional[Place]:
        """The schedule line on a festival's page with this performance's date and start."""
        if page is None:
            return None
        text = " ".join(BeautifulSoup(page, "html.parser").get_text(" ").split())
        lines = list(SCHEDULE_LINE.finditer(text))
        if not lines:
            return CLUB  # a festival that names no other rooms is in the club
        for m in lines:
            month = MONTHS.get(m.group(1).lower())
            hour = int(m.group(3)) % 12 + (12 if m.group(5).lower() == "p" else 0)
            if (month, int(m.group(2)), hour, int(m.group(4) or 0)) == (start.month, start.day, start.hour, start.minute):
                place = place_named(m.group(6))
                if place is None:
                    logger.info(f"{self.source_name}: skipping '{title}' {start} - schedule names "
                                f"'{m.group(6).strip()}', not a place we know")
                return place
        logger.info(f"{self.source_name}: skipping '{title}' {start} - no schedule line on its page")
        return None

    def is_event(self, title: str) -> bool:
        if CLUB_CLOSED.search(title):
            return False
        return super().is_event(title)

    def parse_record(self, record: dict, festival_page: Optional[str] = None) -> Optional[EventCreate]:
        title = self.text(record.get("title"))
        if not self.is_event(title):
            return None
        url = record.get("link") or self.source_url

        start = self.start_of(record)
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date (date={record.get('date')!r}, "
                           f"showtime={record.get('showtime')!r}, start={record.get('start')!r}) ({url})")
            return None

        place = self.where(record, title, start, festival_page)
        if place is None:
            return None

        detail = self.text(record.get("detail"))
        genres = ", ".join(g for g in (record.get("categories") or "").split(",") if g)
        sold_out = "sold-out" in (record.get("ticketButton") or "")
        parts = [f"{detail}." if detail else "",
                 f"{genres}." if genres else "",
                 f"Show at {record.get('showtime')}.",
                 "Sold out." if sold_out else ""]
        description = " ".join(p for p in parts if p)
        if len(description) < 40:
            lead = (f"{title} at Club Passim, Harvard Square." if place is CLUB
                    else f"{title}, presented by Club Passim ({place.venue}, {place.city}).")
            description = f"{lead} {description}"

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            venue_name=place.venue,
            street_address=place.street,
            city=place.city,
            state="MA",
            zip_code=place.zip_code,
            category=self.categorize(title),
            tags=["sold out"] if sold_out else [],
            source_name=self.source_name,
            source_url=url,
            image_url=record.get("imageurl") or None,
        )


# --------------------------------------------------------------------------- #
# Summer series
# --------------------------------------------------------------------------- #

SERIES: Tuple[Tuple[str, str, Place], ...] = (
    ("/live-music/festivals_summer_series/kendall-square-series/", "Kendall Square Lunchtime Concerts",
     Place("Urban Park Roof Garden, Kendall Square", None, "Cambridge", "02142")),
    ("/live-music/festivals_summer_series/danehy-park/", "Danehy Park Summer Concert Series",
     Place("Danehy Park", "99 Sherman Street", "Cambridge", "02138")),
    ("/live-music/festivals_summer_series/harvard-square-series-2/", "Science Plaza Summer Concert Series",
     Place("Harvard Science Center Plaza", None, "Cambridge", "02138")),
    ("/live-music/festivals_summer_series/palmer-street-series/", "Palmer Street Summer Series",
     Place("Palmer Street, Harvard Square", "Palmer Street", "Cambridge", "02138")),
)


class PassimSummerSeriesScraper(BaseFeedScraper):
    """Passim's free outdoor series, one page each. Summer only."""

    CATEGORY = EventCategory.MUSIC

    def __init__(self, today: Optional[date] = None):
        super().__init__(source_name="Passim Summer Series",
                         source_url=f"{SITE}/live-music/festivals_summer_series/")
        # Only the year is inferred from it, and only through the weekday match.
        self.today = today

    def fetch_page(self, url: str) -> str:
        return fetch(url, self.get_browser_headers())

    def scrape_events(self) -> List[EventCreate]:
        reference = self.today or date.today()
        events, failures = [], []
        for path, series, place in SERIES:
            url = f"{SITE}{path}"
            try:
                page = self.fetch_page(url)
            except Exception as e:
                logger.warning(f"{self.source_name}: could not read {url}: {e}")
                failures.append(url)
                continue
            events.extend(self.parse_series(page, url, series, place, reference))
        if len(failures) == len(SERIES):
            raise ValueError(f"{self.source_name}: every series page failed")
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_series(self, page: str, url: str, series: str, place: Place,
                     reference: date) -> List[EventCreate]:
        soup = BeautifulSoup(page, "html.parser")
        events = []
        for item in soup.select("ul.events > li"):
            title_link = item.select_one("h3 a.title")
            day = item.select_one("div.date")
            when = item.select_one("div.time")
            if not (title_link and day and when):
                continue
            bio_tag = title_link.find("em")
            bio = self.clean_text(bio_tag.get_text(" ")) if bio_tag else ""
            if bio_tag:
                bio_tag.extract()
            name = self.clean_text(title_link.get_text(" "))
            if len(name) < 2:
                logger.info(f"{self.source_name}: skipping an unnamed {series} slot ({url})")
                continue

            start = self.start_of(day, when, reference)
            if start is None:
                logger.warning(f"Skipping '{name}' - no parseable date "
                               f"({self.clean_text(day.get_text(' '))!r} {self.clean_text(when.get_text(' '))!r}) ({url})")
                continue

            title = f"Passim Presents: {name}"
            tickets = item.select_one("a.tickets")
            cost = self.clean_text(tickets.get_text(" ")) if tickets else None
            description = f"{series}, presented by Club Passim. {bio}".strip()
            events.append(EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start,
                venue_name=place.venue,
                street_address=place.street,
                city=place.city,
                state="MA",
                zip_code=place.zip_code,
                category=EventCategory.MUSIC,
                cost=cost or None,
                source_name=self.source_name,
                source_url=url,
            ))
        return events

    @staticmethod
    def start_of(day_tag, time_tag, reference: date) -> Optional[datetime]:
        """'Wednesday' + 'Aug 26' + '12PM' -> the year whose Aug 26 is a Wednesday."""
        weekday_tag = day_tag.find(class_="day")
        weekday = WEEKDAYS.get(weekday_tag.get_text(strip=True).lower()) if weekday_tag else None
        md = re.search(r"([A-Za-z]{3,9})\.?\s+(\d{1,2})\s*$", " ".join(day_tag.get_text(" ").split()))
        label = time_tag.find(class_="label")
        time_text = time_tag.get_text(" ")
        if label:
            time_text = time_text.replace(label.get_text(), "")
        hm = clock(time_text.replace(" ", ""))
        if weekday is None or md is None or hm is None:
            return None
        month = next((n for name, n in MONTHS.items() if name.startswith(md.group(1).lower())), None)
        if month is None:
            return None
        year = year_for_weekday(month, int(md.group(2)), weekday, reference)
        if year is None:
            return None
        return datetime(year, month, int(md.group(2)), *hm)
