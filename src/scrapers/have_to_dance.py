"""Have to Dance (havetodance.com) — Boston's swing dance calendar.

A hand-maintained page of special events, one `<table>` per month headed
"December, 2026" (with an anchor "Dec26"), one row per event: the day of the
month, then a paragraph naming everything in prose —

    The Love Dogs Holiday Potluck Special at Swing City, at the West Cambridge
    Youth Center, 680 Huron Avenue, Cambridge, featuring The Love Dogs.
    9:00pm-midnight (lesson: 8:00)

Weekly and monthly dances live on other pages as recurrences in prose and are
not read (CLAUDE.md rule 1: never expand a recurrence into dates).

**Year** comes from the month heading, **day** from the row, **place** from the
address — a comma-separated segment starting with a street number, followed
by the town. Rows in other towns (most of them: Chestnut Hill, Framingham...)
are dropped. **Time**: the first clock time, or a range whose meridiem is
written once at its end ("7:00-9:00pm" starts at 7pm). A row without a
readable start is skipped; the "(lesson: 8:00)" aside has no meridiem and is
never read as the start.
"""
import logging
import re
from datetime import datetime
from typing import List, Optional

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import LOCAL_CITIES, BaseFeedScraper

logger = logging.getLogger(__name__)

PAGE = "http://havetodance.com/calendar.html"

MONTH_HEADING = re.compile(r"^([A-Za-z]+),\s*(\d{4})$")
# "9:00pm-midnight", "7:00-9:00pm", "8pm"
TIME_RANGE = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*([ap]m)?\s*[-–]\s*(?:(\d{1,2})(?::(\d{2}))?\s*([ap]m)|midnight|noon)", re.I)
TIME_SINGLE = re.compile(r"\b(\d{1,2})(?::(\d{2}))?\s*([ap]m)\b", re.I)
STREET = re.compile(r"^\d+[A-Za-z]?\s+\S")


def _hour(hour: str, meridiem: str) -> int:
    return int(hour) % 12 + (12 if meridiem.lower() == "pm" else 0)


def read_start(text: str) -> Optional[tuple]:
    """(hour, minute) of the row's start, or None."""
    match = TIME_RANGE.search(text)
    if match:
        hour, minute, meridiem = match.group(1), match.group(2) or "00", match.group(3)
        if meridiem is None:
            end_meridiem = match.group(6)
            if end_meridiem is None:
                return None  # "8-midnight": no meridiem anywhere for the start
            # "7:00-9:00pm": the start shares the end's meridiem, if that keeps
            # it before the end; otherwise it is ambiguous and not guessed.
            if int(hour) % 12 >= int(match.group(4)) % 12:
                return None
            meridiem = end_meridiem
        return _hour(hour, meridiem), int(minute)
    match = TIME_SINGLE.search(text)
    if match:
        return _hour(match.group(1), match.group(3)), int(match.group(2) or 0)
    return None


class HaveToDanceScraper(BaseFeedScraper):
    CATEGORY = EventCategory.SPORTS  # social dances, as Boston Swing Central files them

    def __init__(self):
        super().__init__(source_name="Have to Dance", source_url=PAGE)

    def scrape_events(self) -> List[EventCreate]:
        return self.parse_page(self.fetch_html(self.source_url))

    def parse_page(self, html: str) -> List[EventCreate]:
        events = []
        for table in self.parse_html(html).find_all("table"):
            heading = table.find("th")
            match = MONTH_HEADING.match(self.clean_text(heading.get_text())) if heading else None
            if not match:
                continue
            try:
                month = datetime.strptime(match.group(1), "%B").month
            except ValueError:
                continue
            year = int(match.group(2))
            anchor = heading.find("a", attrs={"name": True})
            url = f"{PAGE}#{anchor['name']}" if anchor else PAGE
            for row in table.find_all("tr"):
                if row.find_parent("table") is not table:
                    continue  # a row of a table nested in this one belongs to its own month
                # Direct cells only: the page never closes its <tr>s, so the
                # parser nests each row inside the one before it.
                cells = row.find_all("td", recursive=False)
                if len(cells) < 2:
                    continue
                event = self.parse_row(cells, year, month, url)
                if event:
                    events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_row(self, cells, year: int, month: int, url: str) -> Optional[EventCreate]:
        day_text = self.clean_text(cells[0].get_text())
        text = self.clean_text(cells[1].get_text(" "))
        text = re.sub(r"\s+([,.])", r"\1", text)
        if not day_text.isdigit() or not text:
            return None
        segments = [s.strip() for s in text.split(",")]
        title = segments[0]

        # The town is the segment after the street address.
        town_at = next((i for i in range(1, len(segments)) if STREET.match(segments[i - 1])), None)
        if town_at is None:
            logger.info(f"{self.source_name}: skipping '{title}' - no street address to place it")
            return None
        city = segments[town_at].title()
        if city not in LOCAL_CITIES:
            return None
        street = segments[town_at - 1]
        venue = re.sub(r"^at\s+(the\s+)?", "", segments[town_at - 2], flags=re.I).strip() if town_at >= 2 else ""
        venue = venue or title

        try:
            day = datetime(year, month, int(day_text))
        except ValueError:
            logger.warning(f"Skipping '{title}' - no such date {year}-{month}-{day_text}")
            return None
        clock = read_start(text)
        if clock is None:
            logger.warning(f"Skipping '{title}' on {day:%Y-%m-%d} - no readable start time ({url})")
            return None

        link = cells[1].find("a", href=True)
        return EventCreate(
            title=title[:200],
            description=text[:2000],
            start_datetime=day.replace(hour=clock[0], minute=clock[1]),
            venue_name=venue[:150],
            street_address=street,
            city=city,
            state="MA",
            category=self.categorize(title),
            source_name=self.source_name,
            source_url=url,
            website_url=link["href"] if link else None,
        )
