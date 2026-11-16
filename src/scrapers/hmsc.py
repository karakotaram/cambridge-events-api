"""Harvard Museums of Science & Culture — public programs.

HMSC runs the public programmes of the Harvard Museum of Natural History, the
Peabody Museum, the Harvard Museum of the Ancient Near East (Semitic Museum)
and the Collection of Historical Scientific Instruments, on one WordPress site
using the Calendar+ plugin. (hmnh.harvard.edu/calendar answers 403; this is
the same programme.)

The "Upcoming Programs" page lists each program with a full date:

    The Festivals of Drunkenness in the Temple of Mut
    On November 18, 2026 at 6:00 pm till 7:00 pm

and each program's page says where, in a paragraph of its own:

    Location: Geological Lecture Hall, 24 Oxford Street, Cambridge, MA 02138

so one detail page is read per program. That page repeats the date and time in
its header ("November 18, 2026" / "6:00 pm - 7:00 pm"); a program whose two
dates disagree is skipped. A program with no Location line is listed Online
when its type says so ("Online Workshop") and skipped otherwise.

Calendar+'s REST API wants a month and year, which would mean reading the clock
(CLAUDE.md rule 3), so the listing page is read instead.
"""
import logging
import re
from datetime import datetime
from typing import List, Optional, Tuple

from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

PAGE = "https://hmsc.harvard.edu/programs/upcoming-programs/"

DATE = r"[A-Z][a-z]+ \d{1,2}, \d{4}"
TIME = r"\d{1,2}:\d{2}\s*[ap]m"
# "On November 18, 2026 at 6:00 pm till 7:00 pm" / "On November 18, 2026"
ONE_DAY = re.compile(rf"^On ({DATE})(?: at ({TIME})(?: till ({TIME}))?)?$", re.I)
# "From November 18, 2026 at 6:00 pm to November 19, 2026 at 7:00 pm"
SPAN = re.compile(rf"^From ({DATE})(?: at ({TIME}))? (?:to|till) ({DATE})(?: at ({TIME}))?$", re.I)
# "Geological Lecture Hall, 24 Oxford Street, Cambridge, MA 02138"
ADDRESS = re.compile(r"^(?P<head>.+?),\s*(?P<city>[A-Za-z .]+?),\s*(?:MA|Massachusetts)\b\.?\s*(?P<zip>\d{5})?\s*$")
PRICE = re.compile(r"^(?:Price|Cost|Admission):\s*(.+)$", re.I)
LABELLED = re.compile(r"^(?:Location|Speaker|Speakers|Instructor|Price|Cost|Admission)\s*:", re.I)


class HMSCScraper(BaseFeedScraper):
    HOME = None   # four museums and their lecture halls
    CATEGORY = EventCategory.ARTS_CULTURE

    def __init__(self):
        super().__init__(source_name="Harvard Museums of Science & Culture", source_url=PAGE)

    def scrape_events(self) -> List[EventCreate]:
        soup = BeautifulSoup(self.fetch_html(self.source_url), "html.parser")
        items = soup.select(".calendarp .calendar-event")
        if not items and "calendarp" not in str(soup):
            raise ValueError(f"{self.source_name}: no Calendar+ listing on {self.source_url} — has the page changed?")
        events = []
        for item in items:
            event = self.parse_listing(item)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name} ({len(items)} listed)")
        return events

    # -- listing --------------------------------------------------------- #

    def parse_listing(self, item) -> Optional[EventCreate]:
        link = item.select_one("h3 a[href]")
        meta = item.select_one(".calendarp-event-meta")
        if link is None or meta is None:
            return None
        title = self.text(link.get_text(" "))
        url = link["href"].strip()
        if not self.is_event(title):
            return None
        when = self.when(self.clean_text(meta.get_text(" ")))
        if when is None:
            logger.warning(f"Skipping '{title}' - unreadable date {meta.get_text(' ', strip=True)!r} ({url})")
            return None
        start, end, all_day = when

        detail = BeautifulSoup(self.fetch_html(url), "html.parser")
        header_date = detail.select_one(".entry-meta .post-date .meta-text")
        if header_date is not None and self.day(self.clean_text(header_date.get_text())) != start.date():
            logger.warning(f"Skipping '{title}' - listing says {start:%Y-%m-%d}, its page says "
                           f"{header_date.get_text(strip=True)!r} ({url})")
            return None

        body = detail.select_one(".entry-content") or detail
        paragraphs = [self.clean_text(p.get_text(" ")) for p in body.select("p, h4")]
        paragraphs = [p for p in paragraphs if p]
        kinds = self.kinds(detail, body)

        where = self.where(title, paragraphs, kinds)
        if where is None:
            return None

        prose = [p for p in paragraphs if not LABELLED.match(p) and p not in kinds and len(p) > 40]
        description = " ".join(prose)
        if len(description) < 20:
            description = self.fallback_description(title, where)
        cost = next((m.group(1) for m in map(PRICE.match, paragraphs) if m), None)
        if cost is None and any(k.lower().startswith("free") for k in kinds):
            cost = "Free"

        image = detail.select_one(".entry-content img[src]")
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
            category=self.category_for(title, kinds),
            cost=cost,
            source_name=self.source_name,
            source_url=url,
            image_url=image["src"] if image else None,
        )

    # -- dates ----------------------------------------------------------- #

    @staticmethod
    def day(text: str):
        try:
            return datetime.strptime(text, "%B %d, %Y").date()
        except ValueError:
            return None

    @staticmethod
    def at(date_text: str, time_text: Optional[str]) -> datetime:
        if not time_text:
            return datetime.strptime(date_text, "%B %d, %Y")
        return datetime.strptime(f"{date_text} {time_text.replace(' ', '').upper()}", "%B %d, %Y %I:%M%p")

    def when(self, meta: str) -> Optional[Tuple[datetime, Optional[datetime], bool]]:
        """(start, end, all_day) from a listing's meta line, or None."""
        try:
            one = ONE_DAY.match(meta)
            if one:
                date_text, start_time, end_time = one.groups()
                start = self.at(date_text, start_time)
                end = self.at(date_text, end_time) if end_time else None
                return start, (end if end and end > start else None), start_time is None
            span = SPAN.match(meta)
            if span:
                first, start_time, last, end_time = span.groups()
                start = self.at(first, start_time)
                end = self.at(last, end_time)
                return start, (end if end > start else None), start_time is None
        except ValueError:
            return None
        return None

    # -- what and where -------------------------------------------------- #

    def kinds(self, detail, body) -> List[str]:
        """The program's type: header categories ("Lecture , Special Event") and
        the body's heading ("Free Hybrid Lecture Event")."""
        found = []
        categories = detail.select_one(".entry-meta .event-categories .meta-text")
        if categories:
            found += [self.clean_text(c) for c in categories.get_text().split(",") if self.clean_text(c)]
        heading = body.select_one("h4")
        if heading:
            found.append(self.clean_text(heading.get_text(" ")))
        return found

    def where(self, title: str, paragraphs: List[str], kinds: List[str]) -> Optional[Place]:
        location = next((p.split(":", 1)[1].strip() for p in paragraphs
                         if p.lower().startswith("location:") and ":" in p), "")
        if not location:
            # "Location:" in its own <strong>, the address in the next paragraph
            labels = [i for i, p in enumerate(paragraphs) if p.rstrip(":").lower() == "location"]
            if labels and labels[0] + 1 < len(paragraphs):
                location = paragraphs[labels[0] + 1]
        if not location:
            if any("online" in k.lower() for k in kinds):
                return Place("Online", None, "Cambridge", None)
            logger.info(f"{self.source_name}: skipping '{title}' - its page names no location")
            return None
        match = ADDRESS.match(location)
        if match is None:
            logger.info(f"{self.source_name}: skipping '{title}' - no city in location {location!r}")
            return None
        parts = [p.strip() for p in match["head"].split(",") if p.strip()]
        street_at = next((i for i, p in enumerate(parts) if p[:1].isdigit()), None)
        if street_at is None:
            venue, street = ", ".join(parts), None
        else:
            venue, street = ", ".join(parts[:street_at]) or parts[street_at], ", ".join(parts[street_at:])
        return self.place(title, venue, street, match["city"], match["zip"])

    def category_for(self, title: str, kinds: List[str]) -> EventCategory:
        category = self.categorize(title)
        if category != self.CATEGORY:
            return category
        if any("lecture" in k.lower() for k in kinds):
            return EventCategory.LECTURES
        return self.CATEGORY
