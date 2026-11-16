"""Calendars published on Trumba, read as JSON.

Trumba hosts calendars for Harvard (the Gazette's university-wide calendar, and
the departmental calendars that feed it). Every Trumba calendar is served as
JSON at

    https://www.trumba.com/calendars/<web name>.json?days=N

one item per occurrence from today, N days ahead. The web name is the one in
the page's `$Trumba.addSpud({webName: ...})` embed. A subclass names its web
name and page and says which items are not events; see base_feed for the shared
rules on place, placeholders and category.

**Times.** `startDateTime` is the calendar's wall clock and `startTimeZoneOffset`
says which offset that was ("-0500"). An Eastern offset (-0400 or -0500) means
the wall clock is already what CLAUDE.md rule 2 wants; any other offset is a
calendar in another zone, and its items are skipped rather than converted.
Each item also carries the date as the reader sees it, `dateTimeFormatted`
("Monday, November 16, 2026, 12 – 1:30pm EST"); an item whose machine date and
printed date disagree is skipped, since one of them is wrong and nothing says
which.

**Runs.** "Ongoing through Sunday, March 21, 2027" is how Trumba prints an item
that began before the window and is still running — an exhibition, not an
occurrence. Those are skipped here; subclasses add their calendar's own signs
of a run.

**Where.** `location` is free HTML: lines separated by <br>, usually wrapped
in a Google Maps link whose `q=` holds the address ("32 Quincy St, Cambridge,
MA 02138"). The city comes from that address when there is one, else from the
lines themselves. `locationType` says In-Person, Hybrid or Online; an Online
item is listed as Online, the others go through base_feed's place rules
(Cambridge and Somerville only).
"""
import html
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import parse_qs, unquote_plus, urlparse

import requests

from src.models.event import EventCreate
from src.scrapers.base_feed import ZIP, BaseFeedScraper, Place

logger = logging.getLogger(__name__)

FEED = "https://www.trumba.com/calendars/{web_name}.json"

EASTERN_OFFSETS = {"-0400", "-0500"}

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August",
          "September", "October", "November", "December")
# The leading date of `dateTimeFormatted`: "Monday, November 16, 2026, ..." or,
# for an item spanning days, "Thursday, November 19 – Friday, November 20, 2026".
PRINTED_DATE = re.compile(
    r"^(?P<weekday>" + "|".join(WEEKDAYS) + r"),\s+(?P<month>" + "|".join(MONTHS) + r")\s+(?P<day>\d{1,2})"
    r"(?:,\s*(?P<year>\d{4}))?")
ONGOING = re.compile(r"^\s*ongoing\b", re.I)

# "..., Cambridge, MA 02138" / "42 Francis Ave., Cambridge, MA" / "Concord, MA, 01742"
CITY_BEFORE_STATE = re.compile(r"(?:^|,)\s*([A-Za-z][A-Za-z .'-]*?)\s*,\s*(?:MA|Massachusetts)\b\.?,?\s*(\d{5})?")
BARE_CITY = re.compile(r"^[A-Za-z][A-Za-z .'-]{2,40}$")
# "24 Oxford St.", "6-8 Garden St" — not "4th Floor"
STREET = re.compile(r"^\d+(?:-\d+)?[A-Za-z]?\s+\S")
LINE_BREAK = re.compile(r"<br\s*/?>", re.I)
MAP_LINK = re.compile(r"""href=["'](https?://maps\.google\.[^"']+)["']""", re.I)
# A hybrid room as Radcliffe writes it: "Knafel Center OR Online on Zoom". The
# room is where a reader goes, and the venue's own source names it alone.
OR_ONLINE = re.compile(r"\s+OR\s+Online\b.*$")


def split_lines(raw: str) -> List[str]:
    """A Trumba HTML field's <br>-separated lines, as plain text."""
    lines = []
    for part in LINE_BREAK.split(raw or ""):
        text = " ".join(html.unescape(re.sub(r"<[^>]+>", " ", part)).split())
        if text:
            lines.append(text)
    return lines


def parse_location(raw: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """(venue, street, city, zip) from a Trumba `location` field. Any may be None.

    The venue is the first line. The city and zip come from the map link's
    address when there is one, else from a "City, MA" line, else from a last
    line that is only a name ("Cambridge"). The street is the first part that
    starts with a number.
    """
    if not raw or not raw.strip():
        return None, None, None, None
    lines = split_lines(raw)
    if not lines:
        return None, None, None, None
    venue = OR_ONLINE.sub("", lines[0]) or lines[0]

    address_parts: List[str] = []
    match = MAP_LINK.search(raw)
    if match:
        q = parse_qs(urlparse(html.unescape(match.group(1))).query).get("q")
        if q:
            address_parts = [unquote_plus(q[0])]
    address_parts += lines[1:] + [venue]

    city = zip_code = None
    for part in address_parts:
        found = CITY_BEFORE_STATE.search(part)
        if found:
            city, zip_code = found.group(1).strip(), found.group(2)
            break
    if city is None:
        # "Peabody Museum<br />11 Divinity Ave<br />Cambridge": a last line that
        # is a bare name. Only after the first line, which is the venue.
        tail = [ln for ln in lines[1:] if not ln.startswith("*")]
        if tail and BARE_CITY.match(tail[-1]) and not any(ch.isdigit() for ch in tail[-1]):
            city = tail[-1]
    if zip_code is None:
        zips = [ZIP.search(p) for p in address_parts]
        zip_code = next((z.group(1) for z in zips if z), None)

    street = None
    for part in lines[1:] + [venue]:
        for piece in part.split(","):
            piece = piece.strip()
            if STREET.match(piece) and not ZIP.fullmatch(piece):
                street = piece
                break
        if street:
            break
    return venue, street, city, zip_code


class BaseTrumbaScraper(BaseFeedScraper):
    """One Trumba calendar's JSON feed."""

    # Days ahead to ask for. The feed caps a response (1,000 items for the
    # Gazette without a window, which ran out mid-February), so the window is
    # explicit rather than whatever fits under the cap.
    DAYS = 60

    # Trumba lists an exhibition as one item per day it is open, and a show's
    # run as one all-day item per day. A title on at least this many days that
    # is all-day, or open RUN_MIN_HOURS or more, is such a run. Tours, services
    # and performances recur too, but each is a short timed occurrence.
    RUN_MIN_DAYS = 4
    RUN_MIN_HOURS = 4

    # Online items have no place; they are listed under this city, as the
    # MIT and library calendars list theirs under their home city.
    ONLINE_CITY: Optional[str] = "Cambridge"

    def __init__(self, source_name: str, web_name: str, page: str):
        super().__init__(source_name=source_name, source_url=page)
        self.web_name = web_name
        self._days_listed: Dict[str, int] = {}

    @property
    def feed_url(self) -> str:
        return FEED.format(web_name=self.web_name)

    def fetch_feed(self) -> list:
        response = requests.get(self.feed_url, params={"days": self.DAYS},
                                headers=self.get_browser_headers(), timeout=60)
        response.raise_for_status()
        items = response.json()
        if not isinstance(items, list):
            raise ValueError(f"{self.source_name}: Trumba feed {self.feed_url} did not return a list")
        return items

    def scrape_events(self) -> List[EventCreate]:
        items = self.fetch_feed()
        self.prepare(items)
        events = []
        for item in items:
            try:
                event = self.parse_item(item)
            except Exception as e:  # one malformed item must not sink the calendar
                logger.warning(f"{self.source_name}: failed to parse Trumba item {item.get('eventID')}: {e}")
                continue
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name} ({len(items)} feed items)")
        return events

    # -- hooks ------------------------------------------------------------ #

    def prepare(self, items: List[dict]) -> None:
        """Count the days each title is listed on; see RUN_MIN_DAYS."""
        days: Dict[str, set] = {}
        for item in items:
            days.setdefault(self.text(item.get("title")).lower(), set()).add(str(item.get("startDateTime"))[:10])
        self._days_listed = {title: len(d) for title, d in days.items()}

    def is_run(self, item: dict) -> bool:
        """An item that is a span of opening hours or days, not an occurrence."""
        if ONGOING.match(self.formatted(item)):
            return True
        if self._days_listed.get(self.text(item.get("title")).lower(), 0) < self.RUN_MIN_DAYS:
            return False
        if item.get("allDay"):
            return True
        start = self.wall_clock(item, "startDateTime", "startTimeZoneOffset")
        end = self.wall_clock(item, "endDateTime", "endTimeZoneOffset")
        return bool(start and end and (end - start).total_seconds() >= self.RUN_MIN_HOURS * 3600)

    def is_public(self, item: dict) -> bool:
        return True

    def categorize_item(self, item: dict, title: str):
        return self.categorize(title)

    # -- fields ----------------------------------------------------------- #

    @staticmethod
    def custom_fields(item: dict) -> Dict[str, str]:
        return {f.get("label"): html.unescape(str(f.get("value") or ""))
                for f in item.get("customFields") or [] if f.get("label")}

    def field(self, item: dict, label: str) -> str:
        return self.text(self.custom_fields(item).get(label))

    @staticmethod
    def formatted(item: dict) -> str:
        return " ".join(html.unescape(item.get("dateTimeFormatted") or "").split())

    def wall_clock(self, item: dict, key: str, offset_key: str) -> Optional[datetime]:
        raw = item.get(key)
        if not raw or item.get(offset_key) not in EASTERN_OFFSETS:
            return None
        try:
            return datetime.fromisoformat(str(raw)).replace(second=0, microsecond=0)
        except ValueError:
            return None

    def printed_date_agrees(self, item: dict, start: datetime) -> bool:
        """The printed weekday, month and day (and year, when printed) match `start`."""
        match = PRINTED_DATE.match(self.formatted(item))
        if not match:
            return False
        return (WEEKDAYS[start.weekday()] == match["weekday"]
                and MONTHS[start.month - 1] == match["month"]
                and start.day == int(match["day"])
                and (match["year"] is None or start.year == int(match["year"])))

    def where(self, item: dict, title: str) -> Optional[Place]:
        if (item.get("locationType") or "").lower() == "online":
            return Place("Online", None, self.ONLINE_CITY, None)
        venue, street, city, zip_code = parse_location(item.get("location"))
        return self.place(title, venue, street, city, zip_code)

    # -- one item --------------------------------------------------------- #

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        title = self.text(item.get("title"))
        url = item.get("permaLinkUrl") or self.source_url
        if not self.is_event(title) or item.get("canceled"):
            return None
        if self.is_run(item):
            logger.debug(f"{self.source_name}: skipping '{title}' - a run, not an occurrence")
            return None
        if not self.is_public(item):
            logger.info(f"{self.source_name}: skipping '{title}' - not open to the public ({url})")
            return None

        start = self.wall_clock(item, "startDateTime", "startTimeZoneOffset")
        if start is None:
            logger.warning(f"Skipping '{title}' - no Eastern start (startDateTime={item.get('startDateTime')!r}, "
                           f"offset={item.get('startTimeZoneOffset')!r}) ({url})")
            return None
        if not self.printed_date_agrees(item, start):
            logger.warning(f"Skipping '{title}' - start {start} disagrees with the printed date "
                           f"{self.formatted(item)!r} ({url})")
            return None

        all_day = bool(item.get("allDay"))
        end = None if all_day else self.wall_clock(item, "endDateTime", "endTimeZoneOffset")
        if all_day:
            start = start.replace(hour=0, minute=0)
        if end is not None and end <= start:
            end = None

        where = self.where(item, title)
        if where is None:
            return None

        description = self.text(item.get("description"))
        if len(description) < 20:
            description = self.fallback_description(title, where)

        image = item.get("eventImage") or item.get("detailImage") or {}
        return EventCreate(
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
            category=self.categorize_item(item, title),
            cost=self.field(item, "Cost")[:200] or None,
            source_name=self.source_name,
            source_url=url,
            image_url=image.get("url") if isinstance(image, dict) else None,
        )
