"""MIT Museum — programs and events, from the site's own Next.js data.

mitmuseum.mit.edu is a Next.js front end on Vercel. Plain HTTP gets a Vercel
bot challenge (HTTP 429, `x-vercel-mitigated: challenge`); an ordinary headless
browser is served the page after the challenge's script runs. Nothing here
interacts with the challenge — if the page is still the challenge after the
wait, the source fails (see BasePlaywrightScraper.wait_past_challenge).

The programs page embeds its list in `__NEXT_DATA__`: each program's title,
`uri` and a display date ("November 19, 2026", "Next session: December 1,
2026.", "Weekly, Wednesdays and Saturdays"). The display date is not read.
Each program's own data, fetched from inside the page as the site's router
does (`/_next/data/<buildId>/<uri>.json`), lists its sessions:

    {"sessionStartDate": "Thursday November 19", "sessionEndDate": "Thursday November 19",
     "sessionStartTime": "18:00", "sessionEndTime": "20:30", ...}

One event per session. A session spanning several days is a run, not an
occasion, and is skipped; so is a session with no time.

**Year.** Sessions print weekday, month and day but no year. The year is the
one near today in which that date falls on that weekday (art.py's rule), and
it must land within the coming months — Science Paint Night lists "Thursday
November 19" and "Friday January 29", which are 2026 and 2027. Today is only
the reference for that match, never a date.

**Where.** Programs carry no location; they are at the museum, 314 Main St —
the address the MIT Literature feed's own venue record gives "MIT Museum, E-28".

Be gentle: the site answered 429 to a burst of plain requests. Program data is
fetched one at a time with a pause between.
"""
import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import List, Optional

from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.art import WEEKDAYS, year_for_weekday
from src.scrapers.base_feed import NOT_AN_EVENT, TITLE_CATEGORIES, Place
from src.scrapers.base_playwright_scraper import BasePlaywrightScraper

logger = logging.getLogger(__name__)

SITE = "https://mitmuseum.mit.edu"
HOME = Place("MIT Museum", "314 Main St", "Cambridge", "02142")

MONTHS = {m: i for i, m in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), start=1)}
SESSION_DATE = re.compile(r"^(?P<weekday>[A-Za-z]+),?\s+(?P<month>[A-Za-z]+)\s+(?P<day>\d{1,2})$")
CLOCK = re.compile(r"^(\d{1,2}):(\d{2})$")

# A session resolves to a date in this window around today
WINDOW_BEFORE_DAYS = 7
WINDOW_AFTER_DAYS = 300
# Milliseconds between program fetches
PAUSE_MS = 2000
# Why a session is skipped when its weekday and date fall in no upcoming year
PAST = "an earlier year's session"
# ...and when it has no date: "Past Sessions" headings, prose-scheduled tours
NO_DATE = "no date"


class MITMuseumScraper(BasePlaywrightScraper):

    def __init__(self, today: Optional[date] = None):
        super().__init__(source_name="MIT Museum", source_url=f"{SITE}/on-now/programs-and-events")
        # Reference for the weekday-to-year match only; injectable for tests.
        self.today = today

    # -- fetching (browser) ----------------------------------------------- #

    def fetch_listing(self) -> dict:
        """The programs page's __NEXT_DATA__."""
        self.goto(self.source_url)
        self.wait_past_challenge(timeout_s=30)
        soup = BeautifulSoup(self.get_html(), "html.parser")
        script = soup.find("script", id="__NEXT_DATA__")
        if script is None or not script.string:
            raise ValueError(f"{self.source_name}: no __NEXT_DATA__ on {self.page.url}")
        return json.loads(script.string)

    def fetch_program(self, build_id: str, uri: str) -> Optional[dict]:
        """One program's page data, fetched from inside the page. None if refused."""
        self.page.wait_for_timeout(PAUSE_MS)
        status, body = self.page.evaluate(
            "async (u) => { const r = await fetch(u); return [r.status, await r.text()]; }",
            f"/_next/data/{build_id}/{uri.strip('/')}.json")
        if status != 200:
            logger.warning(f"{self.source_name}: HTTP {status} for program data {uri}")
            return None
        return json.loads(body)

    # -- parsing ---------------------------------------------------------- #

    def scrape_events(self) -> List[EventCreate]:
        listing = self.fetch_listing()
        build_id = listing.get("buildId")
        programs = ((listing.get("props") or {}).get("pageProps") or {}).get("programs")
        if not build_id or not isinstance(programs, list):
            raise ValueError(f"{self.source_name}: __NEXT_DATA__ has no buildId or programs list")
        reference = self.today or date.today()
        events: List[EventCreate] = []
        for program in programs:
            title = self.clean_text(program.get("title") or "")
            uri = program.get("uri")
            if not title or not uri or NOT_AN_EVENT.search(title):
                continue
            detail = self.fetch_program(build_id, uri)
            entry = ((detail or {}).get("pageProps") or {}).get("entry")
            if not entry:
                logger.warning(f"Skipping '{title}' - no program data ({SITE}/{uri})")
                continue
            events.extend(self.parse_program(program, entry, reference))
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_program(self, program: dict, entry: dict, reference: date) -> List[EventCreate]:
        title = self.clean_text(entry.get("title") or program.get("title") or "")
        url = f"{SITE}/{program['uri'].strip('/')}"
        description = self.text(entry.get("introText")) or self.clean_text(program.get("cardTeaserText") or "")
        body = self.text(entry.get("bodyText"))
        if body and body not in description:
            description = f"{description} {body}".strip()
        if len(description) < 20:
            description = f"{title} at the MIT Museum, Cambridge."
        image = next((i.get("url") for i in entry.get("heroImage") or program.get("heroImage") or []
                      if isinstance(i, dict) and i.get("url")), None)

        sessions = entry.get("sessions") or []
        if not sessions:
            logger.info(f"{self.source_name}: skipping '{title}' - no dated sessions ({url})")
        events = []
        for session in sessions:
            when = self.session_times(session, reference)
            if not isinstance(when, tuple):
                # Programs keep past sessions and undated headings ("Past Sessions"); not errors.
                log = logger.info if when in (PAST, NO_DATE) else logger.warning
                log(f"Skipping a session of '{title}' - {session.get('sessionStartDate')!r} "
                    f"{session.get('sessionStartTime')!r}: {when} ({url})")
                continue
            start, end = when
            name = self.clean_text(session.get("sessionTitle") or "")
            events.append(EventCreate(
                title=(f"{title}: {name}" if name and name.lower() not in title.lower() else title)[:200],
                description=description[:2000],
                start_datetime=start,
                end_datetime=end,
                venue_name=HOME.venue,
                street_address=HOME.street,
                city=HOME.city,
                state="MA",
                zip_code=HOME.zip_code,
                category=self.categorize(title),
                source_name=self.source_name,
                source_url=url,
                website_url=session.get("sessionBookingUrl") or None,
                image_url=image,
            ))
        return events

    def session_times(self, session: dict, reference: date):
        """(start, end) for a one-day session with a start time, else the reason it is skipped."""
        first = self.clean_text(session.get("sessionStartDate") or "")
        last = self.clean_text(session.get("sessionEndDate") or "") or first
        if not first:
            return NO_DATE
        if first != last:
            return "spans several days"
        m = SESSION_DATE.match(first)
        clock = CLOCK.match(self.clean_text(session.get("sessionStartTime") or ""))
        if not m or not clock:
            return "unreadable date or time"
        weekday = WEEKDAYS.get(m.group("weekday").lower())
        month = MONTHS.get(m.group("month").lower())
        if weekday is None or month is None:
            return "unreadable date"
        day = int(m.group("day"))
        year = year_for_weekday(month, day, weekday, reference)
        if year is None:
            older = any(self._weekday_of(y, month, day) == weekday for y in range(reference.year - 10, reference.year - 1))
            return PAST if older else "its weekday matches no year near today"
        try:
            start = datetime(year, month, day, int(clock.group(1)), int(clock.group(2)))
            end_clock = CLOCK.match(self.clean_text(session.get("sessionEndTime") or ""))
            end = start.replace(hour=int(end_clock.group(1)), minute=int(end_clock.group(2))) if end_clock else None
        except ValueError:
            return "unreadable time"
        ahead = start.date() - reference
        if ahead < timedelta(days=-WINDOW_BEFORE_DAYS):
            return PAST
        if ahead > timedelta(days=WINDOW_AFTER_DAYS):
            return f"resolves to {start:%Y-%m-%d}, further ahead than the programme lists"
        return start, (end if end and end > start else None)

    @staticmethod
    def _weekday_of(year: int, month: int, day: int) -> Optional[int]:
        try:
            return date(year, month, day).weekday()
        except ValueError:
            return None

    # -- text helpers ------------------------------------------------------ #

    def text(self, value) -> str:
        if not value:
            return ""
        return self.clean_text(BeautifulSoup(str(value), "html.parser").get_text(" "))

    def categorize(self, title: str) -> EventCategory:
        for category, pattern in TITLE_CATEGORIES:
            if pattern.search(title):
                return category
        return EventCategory.ARTS_CULTURE
