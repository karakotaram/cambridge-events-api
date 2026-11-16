"""The Boston Calendar (thebostoncalendar.com) — a Boston-wide listings site.

An aggregator: registered with kind "aggregator" so that the venues it copies
win deduplication, and filtered to Cambridge and Somerville like bostonshows.

**Where the data comes from.** The site is a Rails app whose day view also
answers as JSON: `/events.json?day=D&month=M&year=Y` returns the site's own
records for that day — `event_date`, `start_time` ("7:30pm"), `end_date`,
`end_time`, a street address with its `city`, the admission. There is no week
or month JSON (both 406), so the scraper asks for one day at a time over
WINDOW_DAYS. Each JSON response is ~3 MB and takes the server ~6 s, because
of the standing listings below; that is why the window is two weeks.

**The JSON is checked against the page.** A recurring series is stored as one
record per occurrence, and the page does not always show the occurrence's own
fields. On 2026-11-16 the record for "7PM Detention" on Nov 21 said 8:00pm at
Cambridge Community Center, Cambridge; the listing and the event page said
7:00p at Goofs Comedy Club, Somerville — a different time, venue and city.
2 of ~60 local rows over three days disagreed like that. So each day's HTML
listing (cached by the site; ~0.1 s) is read as well, and a row is kept only
if the listing shows it on the same date, at the same start time, at the same
venue. A row the listing does not show at all — created since the listing was
cached, or hidden — is left for a later run.

**Most rows are not events.** Of the ~620 rows in a day, ~550 are standing
listings stored as one record spanning months or years — "Faro Cafe |
Cambridge", "20 Trivia Nights in Boston for Every Day of the Week", museum
hours (2023-01-01 → 2026-12-31). The listing prints them under the day you
asked for ("Monday, Nov 16, 2026 goes until 06/06"), which is a query
parameter, not a date anything happens. So only rows that start and end on
the same day are kept, plus a row that ends the next morning, earlier on the
clock than it began (karaoke 11pm–2am). Multi-day runs — a play from 10/27 to
11/22, a dumpling class "(Fridays)" from 11/06 to 12/25, open studios noon
Saturday to 5pm Sunday — are dropped: the site does not say which days or
hours of the run are open, and picking some would be fabrication (CLAUDE.md
rule 1).

Recurring listings arrive as one record per occurrence, each with its own id
and slug (rubies--127 on the 16th, rubies--177 on the 17th), so a weekly
trivia night arrives as dated rows. No recurrence is expanded here.

**Times.** `start_time` is a 12-hour clock with its meridiem. "12:00am" is
how the site writes "no time given" (Volunteer Opportunities Everyday!
12:00am–11:30pm), so such a row is published as an all-day event on its date.
"""
import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import LOCAL_CITIES, BaseFeedScraper

logger = logging.getLogger(__name__)

SITE = "https://www.thebostoncalendar.com"
WINDOW_DAYS = 14

CLOCK = re.compile(r"^\s*(\d{1,2}):(\d{2})\s*([ap])m?\s*$", re.I)
# A listing row's time: "Saturday, Nov 21, 2026 7:00p". Standing listings
# read "... goes until 06/06" and do not match.
SHOWN = re.compile(r"^\w+, (\w{3} \d{1,2}, \d{4}) (\d{1,2}:\d{2}[ap])$")
PRICE = re.compile(r"^\$?\s*(\d+(?:\.\d{2})?)$")
CLOUDINARY = re.compile(r"^image/upload/(v\d+)/([A-Za-z0-9_-]+)\.(\w+)")

# The site's own tags, for titles that name no kind of event. First match wins.
TAG_CATEGORIES = (
    ("Music", EventCategory.MUSIC),
    ("Movies", EventCategory.FILM),
    ("Shows", EventCategory.THEATER),
    ("Lectures & Conferences", EventCategory.LECTURES),
    ("Art", EventCategory.ARTS_CULTURE),
    ("History", EventCategory.ARTS_CULTURE),
    ("Sports & Active Life", EventCategory.SPORTS),
    ("Food", EventCategory.FOOD_DRINK),
    ("Drinks", EventCategory.FOOD_DRINK),
)

# slug -> (date, (hour, minute), venue) as the HTML listing shows it
Shown = Dict[str, Tuple[date, Tuple[int, int], str]]


def read_clock(text: Optional[str]) -> Optional[Tuple[int, int]]:
    """"7:30pm" or "7:30p" -> (19, 30); anything else -> None."""
    match = CLOCK.match(text or "")
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (1 <= hour <= 12 and 0 <= minute <= 59):
        return None
    if match.group(3).lower() == "p" and hour != 12:
        hour += 12
    elif match.group(3).lower() == "a" and hour == 12:
        hour = 0
    return hour, minute


def same_name(a: Optional[str], b: Optional[str]) -> bool:
    return " ".join((a or "").split()).lower() == " ".join((b or "").split()).lower()


class BostonCalendarScraper(BaseFeedScraper):
    """thebostoncalendar.com, Cambridge and Somerville single-day listings."""

    CATEGORY = EventCategory.COMMUNITY

    def __init__(self, today: Optional[date] = None):
        super().__init__(source_name="The Boston Calendar", source_url=f"{SITE}/events")
        # The window's first day. It only chooses which days to ask for;
        # every event's date comes from its own record.
        self.today = today

    # -- fetching --------------------------------------------------------- #

    @staticmethod
    def day_query(day: date) -> str:
        return f"day={day.day}&month={day.month}&year={day.year}"

    def fetch_day(self, day: date) -> Tuple[list, str]:
        """(the day's JSON records, the day's HTML listing)."""
        rows = json.loads(self.fetch_html(f"{SITE}/events.json?{self.day_query(day)}"))
        if not isinstance(rows, list):
            raise ValueError(f"{self.source_name}: events.json for {day} is not a list")
        return rows, self.fetch_html(f"{SITE}/events?{self.day_query(day)}")

    def scrape_events(self) -> List[EventCreate]:
        first = self.today or date.today()
        rows_by_id: Dict[object, dict] = {}
        shown: Shown = {}
        for offset in range(WINDOW_DAYS):
            rows, listing = self.fetch_day(first + timedelta(days=offset))
            for row in rows:
                rows_by_id.setdefault(row.get("id"), row)  # pinned rows repeat daily
            shown.update(self.parse_listing(listing))
        events = self.parse_rows(rows_by_id.values(), shown)
        logger.info(f"Scraped {len(events)} events from {self.source_name} "
                    f"({len(rows_by_id)} records over {WINDOW_DAYS} days)")
        return events

    def parse_listing(self, html: str) -> Shown:
        """What the day's HTML listing shows for each dated row, by slug."""
        shown: Shown = {}
        for item in BeautifulSoup(html, "html.parser").select("ul#events li.event"):
            link, when = item.select_one("h3 a[href]"), item.select_one("p.time")
            if not link or not when:
                continue
            match = SHOWN.match(self.clean_text(when.get_text(" ")))
            if not match:
                continue
            clock = read_clock(match.group(2))
            try:
                day = datetime.strptime(match.group(1), "%b %d, %Y").date()
            except ValueError:
                continue
            where = item.select_one("p.location")
            slug = link["href"].rstrip("/").rsplit("/", 1)[-1]
            shown[slug] = (day, clock, self.clean_text(where.get_text(" ")) if where else "")
        return shown

    def parse_rows(self, rows, shown: Shown) -> List[EventCreate]:
        events = []
        for row in rows:
            try:
                event = self.parse_row(row, shown)
            except Exception as e:  # one malformed record must not sink the rest
                logger.warning(f"{self.source_name}: could not read record {row.get('slug')!r}: {e}")
                continue
            if event:
                events.append(event)
        return events

    # -- one record ------------------------------------------------------- #

    @staticmethod
    def _day(value) -> Optional[date]:
        try:
            return datetime.strptime(str(value or "").strip(), "%Y-%m-%d").date()
        except ValueError:
            return None

    def parse_row(self, row: dict, shown: Shown) -> Optional[EventCreate]:
        title = self.text(row.get("title"))
        if not self.is_event(title):
            return None
        slug = row.get("slug") or ""
        url = f"{SITE}/events/{slug}" if slug else self.source_url

        # Where first: most records are elsewhere in Greater Boston.
        if (row.get("location_type") or "physical") != "physical":
            return None
        city = self.text(row.get("city")).title()
        if city not in LOCAL_CITIES:
            return None

        day, last_day = self._day(row.get("event_date")), self._day(row.get("end_date"))
        if day is None:
            logger.warning(f"Skipping '{title}' - no parseable event_date {row.get('event_date')!r} ({url})")
            return None
        if last_day is not None and (last_day - day).days > 1:
            return None  # a standing listing or a multi-day run; see module docstring

        clock = read_clock(row.get("start_time"))
        if clock is None:
            logger.warning(f"Skipping '{title}' - no parseable start_time {row.get('start_time')!r} ({url})")
            return None
        venue = self.text(row.get("address1")) or None

        listed = shown.get(slug)
        if listed is None:
            logger.info(f"{self.source_name}: skipping '{title}' - not on the day's listing yet ({url})")
            return None
        if listed[0] != day or listed[1] != clock or not same_name(listed[2], venue):
            logger.warning(f"Skipping '{title}' - record says {day} {row.get('start_time')} at {venue!r}, "
                           f"listing shows {listed[0]} {listed[1]} at {listed[2]!r} ({url})")
            return None

        all_day = clock == (0, 0)
        end_clock = read_clock(row.get("end_time"))
        if last_day is not None and last_day > day and (all_day or end_clock is None or end_clock > clock):
            # Ending tomorrow is one night out only if it ends earlier on the
            # clock than it began (11pm-2am). Noon Saturday to 5pm Sunday is
            # a two-day run, and which hours of it are open is not said.
            return None

        start = datetime(day.year, day.month, day.day, *clock)
        end = None
        if not all_day and end_clock is not None and last_day is not None:
            end = datetime(last_day.year, last_day.month, last_day.day, *end_clock)
            if end <= start:
                end = None

        street = self.text(row.get("address2")) or None
        zip_match = re.search(r"\b(\d{5})\b", str(row.get("zip") or ""))

        description = self.text(row.get("description"))
        if len(description) < 20:
            description = f"{title} at {venue or city}, {city}."

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            venue_name=(venue or city)[:150],
            street_address=street[:200] if street else None,
            city=city,
            state="MA",
            zip_code=zip_match.group(1) if zip_match else None,
            category=self.category_of(title, row.get("tag_list") or []),
            cost=self.cost_of(row),
            source_name=self.source_name,
            source_url=url,
            website_url=(row.get("event_website") or None),
            image_url=self.image_of(row),
        )

    # -- details ---------------------------------------------------------- #

    def category_of(self, title: str, tags: list) -> EventCategory:
        from_title = self.categorize(title)
        if from_title != self.CATEGORY:
            return from_title
        for tag, category in TAG_CATEGORIES:
            if tag in tags:
                return category
        return self.CATEGORY

    def cost_of(self, row: dict) -> Optional[str]:
        """"19" -> "$19"; "0" -> "Free"; prose kept as written; empty + FREE tag -> "Free"."""
        admission = self.text(row.get("admission"))
        match = PRICE.match(admission)
        if match:
            return "Free" if float(match.group(1)) == 0 else f"${match.group(1)}"
        if admission:
            return admission[:100]
        return "Free" if "FREE" in (row.get("tag_list") or []) else None

    @staticmethod
    def image_of(row: dict) -> Optional[str]:
        """The site's own og:image form of the record's Cloudinary upload."""
        match = CLOUDINARY.match(str(row.get("cloudinary_url") or ""))
        if not match or not row.get("slug"):
            return None
        version, public_id, ext = match.groups()
        return f"https://media.thebostoncalendar.com/images/q_auto,fl_lossy/{version}/{public_id}/{row['slug']}.{ext}"
