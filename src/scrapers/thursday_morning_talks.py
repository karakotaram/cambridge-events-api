"""Thursday Morning Talks — a winter lecture series benefiting Mount Auburn Hospital.

Eight Thursday mornings each January and February at Holy Trinity Armenian
Church, 145 Brattle Street. The site is a static page that fills itself in the
browser from `data.yml`: one entry per speaker with an ISO `date`, and under
`meta` the season's time ("10:15am - 12:00pm") and place.

**Seasonal.** Between seasons the file still holds the last one (the 2026
season on 2026-11-16), whose dates EventValidator drops as past. The next
season appears when the organisers post its speakers.

**The time is the season's**, printed once for every talk ("Thursdays Jan &
Feb 2026 • 10:15 AM"); each date is the speaker's own. A date that is not a
Thursday is skipped as a typo rather than published. The place is likewise
stated once, so it is checked: if `meta.location` stops naming 145 Brattle
Street the scrape fails rather than send readers to the old address.
"""
import logging
import re
from datetime import date, datetime
from typing import List, Optional

import yaml

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

SITE = "https://thursdaymorningtalks.org/"
THURSDAY = 3
SEASON_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])m\s*-\s*(\d{1,2}):(\d{2})\s*([ap])m", re.I)


def _clock(hour: str, minute: str, meridiem: str) -> tuple:
    h = int(hour) % 12 + (12 if meridiem.lower() == "p" else 0)
    return h, int(minute)


class ThursdayMorningTalksScraper(BaseFeedScraper):
    HOME = Place("Holy Trinity Armenian Church", "145 Brattle Street", "Cambridge", "02138")
    CATEGORY = EventCategory.LECTURES

    def __init__(self):
        super().__init__(source_name="Thursday Morning Talks", source_url=SITE)

    def scrape_events(self) -> List[EventCreate]:
        return self.parse_data(self.fetch_html(f"{SITE}data.yml"))

    def parse_data(self, text: str) -> List[EventCreate]:
        data = yaml.safe_load(text) or {}
        meta = data.get("meta") or {}

        location = str(meta.get("location") or "")
        if "145 Brattle" not in location:
            raise ValueError(f"{self.source_name}: season location is now {location!r}, "
                             f"not {self.HOME.venue} at {self.HOME.street}; update HOME")

        match = SEASON_TIME.search(str(meta.get("time") or ""))
        if not match:
            raise ValueError(f"{self.source_name}: unreadable season time {meta.get('time')!r}")
        start_clock = _clock(*match.group(1, 2, 3))
        end_clock = _clock(*match.group(4, 5, 6))

        events = []
        for speaker in data.get("speakers") or []:
            event = self.parse_speaker(speaker, start_clock, end_clock)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_speaker(self, speaker: dict, start_clock: tuple, end_clock: tuple) -> Optional[EventCreate]:
        name = self.text(speaker.get("name"))
        title = self.text(speaker.get("cal_title")) or (
            f"{name}: {self.text(speaker.get('topic'))}" if name else "")
        if not self.is_event(title):
            return None

        raw = speaker.get("date")
        try:
            day = raw if isinstance(raw, date) else datetime.strptime(str(raw), "%Y-%m-%d").date()
        except ValueError:
            logger.warning(f"Skipping '{title}' - unreadable date {raw!r}")
            return None
        if day.weekday() != THURSDAY:
            logger.warning(f"Skipping '{title}' - {day} is a {day:%A}, and every talk is on a Thursday")
            return None

        parts = (self.text(speaker.get("description")), self.text(speaker.get("bio")))
        description = " ".join(p for p in parts if p)
        description = (f"{description} Thursday Morning Talks, at {self.HOME.venue}, "
                       f"supports Mount Auburn Hospital.").strip()

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=datetime(day.year, day.month, day.day, *start_clock),
            end_datetime=datetime(day.year, day.month, day.day, *end_clock),
            venue_name=self.HOME.venue,
            street_address=self.HOME.street,
            city=self.HOME.city,
            state="MA",
            zip_code=self.HOME.zip_code,
            category=self.CATEGORY,
            source_name=self.source_name,
            source_url=SITE,
            image_url=speaker.get("image") or None,
        )
