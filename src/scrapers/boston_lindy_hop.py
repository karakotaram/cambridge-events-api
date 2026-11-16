"""Boston Lindy Hop — swing dance socials.

The site's calendar page (FullCalendar) reads `/events/feed/json/?start=&end=`,
which lists classes and events with ISO starts that carry their offset
("2026-10-21T19:00:00-04:00"); its `location` is always null.

**Classes are not read.** Entries under /classes/ are multi-week series sold
by registration, held in Brookline and Harvard Square — a course catalogue,
not events. Entries under /events/ are the public socials, chiefly the
monthly "Somerville Stomp" at Samba Bar. Notices filed as events ("Summer
Break - No BLH Classes") are dropped by title.

**Where** is on each event's own page, in `p.address` (venue, street,
"Somerville, MA 02143"), so one page is read per event in the window. Only
Cambridge and Somerville are kept. That page also prints the date and time
("Wednesday, October 21, 07:00 PM - 11:00 PM"); it must agree with the feed's
start, or the event is skipped.

Socials are posted a month or so ahead, so the source is often empty between
postings; nothing was upcoming on 2026-11-16 (the last was October 21).
"""
import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import List, Optional

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import BaseFeedScraper

logger = logging.getLogger(__name__)

SITE = "https://www.bostonlindyhop.com"
WINDOW_DAYS = 120
NOTICE = re.compile(r"\bno\b.*\bclass(es)?\b|\bbreak\b|\bcancel(l?ed)?\b|\bclosed\b", re.I)
HEADING = re.compile(r"^[A-Za-z]+,\s+([A-Za-z]+ \d{1,2}),\s+(\d{1,2}:\d{2}\s*[AP]M)", re.I)


class BostonLindyHopScraper(BaseFeedScraper):
    CATEGORY = EventCategory.SPORTS  # social dances, as Boston Swing Central files them

    def __init__(self, today: Optional[date] = None):
        super().__init__(source_name="Boston Lindy Hop", source_url=f"{SITE}/calendar/")
        self.today = today  # chooses the window only; dates come from the feed

    def feed_url(self) -> str:
        first = self.today or date.today()
        return f"{SITE}/events/feed/json/?start={first:%Y-%m-%d}&end={first + timedelta(days=WINDOW_DAYS):%Y-%m-%d}"

    def scrape_events(self) -> List[EventCreate]:
        items = json.loads(self.fetch_html(self.feed_url()))
        events = []
        for item in items:
            path = item.get("url") or ""
            if not path.startswith("/events/"):
                continue  # a class session; see module docstring
            title = self.text(item.get("title"))
            if not self.is_event(title) or NOTICE.search(title):
                continue
            event = self.parse_event(item, self.fetch_html(f"{SITE}{path}"))
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_event(self, item: dict, page_html: str) -> Optional[EventCreate]:
        title = self.text(item.get("title"))
        url = f"{SITE}{item.get('url')}"
        try:
            start = datetime.fromisoformat(str(item.get("start")))
        except ValueError:
            logger.warning(f"Skipping '{title}' - unreadable start {item.get('start')!r} ({url})")
            return None
        if start.tzinfo is None:
            logger.warning(f"Skipping '{title}' - start {item.get('start')!r} has no offset ({url})")
            return None
        try:
            end = datetime.fromisoformat(str(item.get("end"))) if item.get("end") else None
        except ValueError:
            end = None

        page = self.parse_html(page_html)
        heading = next((h for h in page.find_all("h3") if HEADING.match(self.clean_text(h.get_text()))), None)
        printed = HEADING.match(self.clean_text(heading.get_text())) if heading else None
        local = to_eastern_naive(start)
        try:
            printed_clock = datetime.strptime(re.sub(r"\s+", "", printed.group(2)).upper(),
                                              "%I:%M%p").time() if printed else None
        except ValueError:
            printed_clock = None
        if not printed or printed.group(1) != f"{local:%B} {local.day}" or printed_clock != local.time():
            logger.warning(f"Skipping '{title}' - feed start {item.get('start')!r} does not match "
                           f"the page's {heading.get_text(strip=True) if heading else None!r} ({url})")
            return None

        address = page.select_one("p.address")
        if address is None:
            logger.info(f"{self.source_name}: skipping '{title}' - its page names no address ({url})")
            return None
        lines = [line for line in (self.clean_text(s) for s in address.stripped_strings)
                 if line and line.lower() != "map this"]
        town_at = next((i for i, line in enumerate(lines) if re.search(r",\s*MA\b", line)), None)
        if town_at is None:
            logger.info(f"{self.source_name}: skipping '{title}' - no town in its address ({url})")
            return None
        venue = lines[0] if town_at >= 1 else None
        street = lines[town_at - 1] if town_at >= 2 else None
        where = self.place(title, venue, street, lines[town_at])
        if where is None:
            return None

        description = self.text(item.get("description"))
        if len(description) < 20:
            description = self.fallback_description(title, where)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end if end and end > start else None,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.categorize(title),
            source_name=self.source_name,
            source_url=url,
        )
