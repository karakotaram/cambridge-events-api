"""Central Square Business Improvement District — its calendar, one day at a time.

centralsq.org/miscellaneous/calendar is a Kirby site. Its month grid is drawn
by `calendarajax.json`, and clicking a day fetches that day's listings from
`eventsajax.json?day=YYYY-MM-DD`, which answers a JSON string of HTML. The grid
does not mark which days have anything, so every day in the window is asked
for. An empty day is ~170 bytes.

Each listing gives a tag ("Music"), a title, a 12-hour time ("5:00pm"), the
member business that posted it, and a location whose link target is sometimes
its street address ("632 Massachusetts Ave, Cambridge, MA 02139") and sometimes
empty.

**The date** is the day asked for. The list's own heading ("Friday, November
20, 2026") must agree, or the whole day is refused: a server that answered
with some other day's list would otherwise move every event on it.

**The time** must carry am/pm. A listing with no time at all is kept as an
all-day listing on its day; one with a time that cannot be read is skipped.
"""
import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup, Comment
from dateutil import parser as date_parser

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

DAY_URL = "https://www.centralsq.org/miscellaneous/eventsajax.json?day={day:%Y-%m-%d}"

# Four weeks. The BID posts about a week ahead; this is one small request a day.
WINDOW_DAYS = 28

TIME = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap])\.?m\b", re.I)
ADDRESS = re.compile(r"^\s*\d+[A-Za-z]?\s+\S")

TAG_CATEGORIES = {
    "music": EventCategory.MUSIC,
    "comedy": EventCategory.THEATER,
    "theater": EventCategory.THEATER,
    "dance": EventCategory.ARTS_CULTURE,
    "arts & culture": EventCategory.ARTS_CULTURE,
    "eat & drink": EventCategory.FOOD_DRINK,
    "education": EventCategory.LECTURES,
    "health & fitness": EventCategory.SPORTS,
}


class CentralSquareBIDScraper(BaseFeedScraper):
    # Every listing is in the district; an unplaced one is "in Central Square".
    HOME = Place("Central Square", None, "Cambridge", "02139")
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self, first_day: Optional[date] = None):
        super().__init__(source_name="Central Square BID",
                         source_url="https://www.centralsq.org/miscellaneous/calendar")
        # Which days to ask for — a choice of requests, not a filter on what
        # they contain. Tests pin it; a run starts today.
        self.first_day = first_day

    def scrape_events(self) -> List[EventCreate]:
        first = self.first_day or date.today()
        events: List[EventCreate] = []
        for offset in range(WINDOW_DAYS):
            day = first + timedelta(days=offset)
            # A failed day fails the source: a silent gap would read as
            # "nothing on" and drop that day's listings from the calendar.
            raw = self.fetch_html(DAY_URL.format(day=day))
            events.extend(self.parse_day(day, json.loads(raw)))
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- one day ----------------------------------------------------------- #

    def parse_day(self, day: date, html: str) -> List[EventCreate]:
        soup = BeautifulSoup(html or "", "html.parser")
        items = soup.select("li.calendar-event")
        if not items:
            return []

        heading = soup.find(class_="calendar-list-title")
        heading_text = self.clean_text(heading.get_text(" ")) if heading else ""
        try:
            listed = date_parser.parse(heading_text).date() if heading_text else None
        except (ValueError, OverflowError):
            listed = None
        if listed != day:
            logger.warning(f"{self.source_name}: asked for {day}, list is headed {heading_text!r} - "
                           f"skipping its {len(items)} listing(s)")
            return []

        events = []
        for item in items:
            event = self.parse_item(day, item)
            if event:
                events.append(event)
        return events

    @staticmethod
    def _times(text: str) -> Tuple[Optional[Tuple[int, int]], Optional[Tuple[int, int]]]:
        found = []
        for hour, minute, meridiem in TIME.findall(text):
            hour, minute = int(hour), int(minute or 0)
            if not (1 <= hour <= 12 and minute < 60):
                return None, None
            found.append((hour % 12 + (12 if meridiem.lower() == "p" else 0), minute))
        if not found:
            return None, None
        return found[0], (found[1] if len(found) > 1 else None)

    def parse_item(self, day: date, item) -> Optional[EventCreate]:
        title_el = item.find(class_="calendar-event-title")
        title = self.text(title_el.get_text(" ")) if title_el else ""
        if not self.is_event(title):
            return None

        details = item.find(class_="calendar-event-details")
        links = details.find_all("a") if details else []
        location = next((a for a in links if a.get("target") == "_blank"), None)
        member = next((a for a in links if a is not location), None)

        # The time is the text before the first link ("12:00pm"). The location
        # is a link after the member's, or plain text when it has no link.
        lead, trailing, seen_link = "", "", False
        for node in (details.children if details else []):
            if getattr(node, "name", None) == "a":
                seen_link, trailing = True, ""
            elif isinstance(node, str) and not isinstance(node, Comment):
                if seen_link:
                    trailing += node
                else:
                    lead += node
        lead = self.clean_text(lead)
        trailing = self.clean_text(trailing).lstrip("|").strip()

        start_hm, end_hm = self._times(lead)
        all_day = False
        if start_hm is None:
            if re.search(r"\d", lead):
                logger.warning(f"Skipping '{title}' on {day} - unreadable time {lead!r}")
                return None
            all_day = True  # no time printed: the day is all that is known
            start = datetime.combine(day, datetime.min.time())
            end = None
        else:
            start = datetime(day.year, day.month, day.day, *start_hm)
            end = datetime(day.year, day.month, day.day, *end_hm) if end_hm else None
            if end is not None and end <= start:
                end = None

        if location is not None:
            venue = self.text(location.get_text(" "))
            address = (location.get("href") or "").strip()
        else:
            venue, address = "", trailing
        street, city = None, None
        if ADDRESS.match(address):
            parts = [p.strip() for p in address.split(",")]
            street, city = parts[0], ", ".join(parts[1:]) or None
            venue = venue or street
        where = self.place(title, venue, street, city)
        if where is None:
            return None

        host = self.text(member.get_text(" ")).lstrip("|").strip() if member else ""
        tags = [self.text(t.get_text(" ")) for t in item.select(".calendar-event-tag")]

        description = self.fallback_description(title, where)
        if host and "improvement district" not in host.lower():
            description += f" Posted by {host}."

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
            category=self.category_for(title, tags),
            source_name=self.source_name,
            source_url=self.event_url(title_el),
        )

    def category_for(self, title: str, tags: List[str]) -> EventCategory:
        by_title = self.categorize(title)
        if by_title != self.CATEGORY:
            return by_title
        for tag in tags:
            if tag.lower() in TAG_CATEGORIES:
                return TAG_CATEGORIES[tag.lower()]
        return self.CATEGORY

    def event_url(self, title_el) -> str:
        """The listing's own link when it goes somewhere other than the BID's home page."""
        anchor = title_el.find_parent("a") if title_el else None
        href = (anchor.get("href") or "").strip() if anchor else ""
        if href.startswith("www."):
            href = f"https://{href}"
        if not href.startswith("http") or href.rstrip("/") in ("https://www.centralsq.org", "http://www.centralsq.org"):
            return self.source_url
        return href
