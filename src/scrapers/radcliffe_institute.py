"""Harvard Radcliffe Institute — lectures, fellows' presentations, gallery events.

radcliffe.harvard.edu/events-and-exhibitions renders every upcoming event and
current exhibition as a card, server-side:

    The Expertise Economy                       Lectures • Dean's Lecture …
    Date & Time   Thursday, December 3, 2026 / 4 PM ET
    Location      Knafel Center OR Online on Zoom / 10 Garden Street /
                  Cambridge, MA 02138

The date carries its year and weekday; a card whose weekday does not match its
date is skipped. Exhibitions are cards too, dated as a range ("Monday, March 2,
2026 / through Friday, January 8, 2027"); a range is not an occurrence and is
skipped.

**Where.** Many events are hybrid ("Knafel Center OR Online on Zoom"): they are
listed at the room, which is where a reader would go. "Online on Zoom" alone is
listed Online. "Radcliffe on the Road" takes events to San Francisco and New
York; those cards name their city and are dropped by base_feed's place rules,
and a card naming a room but no city is skipped rather than assumed local.
"""
import logging
import re
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

PAGE = "https://www.radcliffe.harvard.edu/events-and-exhibitions"

DATE = re.compile(r"^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday),\s+([A-Z][a-z]+ \d{1,2}, \d{4})$")
# "4 PM ET", "12 PM ET", "10:30 AM ET", "4–6 PM ET" (the first time is the start)
TIME = re.compile(r"^(\d{1,2})(?::(\d{2}))?\s*(?:([AP])\.?M\.?)?(?:\s*[-–]\s*\d{1,2}(?::\d{2})?)?\s*([AP])?\.?M?\.?\s*E[SD]?T\b", re.I)
CITY_LINE = re.compile(r"^([A-Za-z][A-Za-z .'-]+),\s*([A-Z]{2})\b\s*(\d{5})?")
OR_ONLINE = re.compile(r"\s+OR\s+Online\b.*$", re.I)
ONLINE = re.compile(r"^Online\b", re.I)
SHOWING = re.compile(r"\b\d+\s*-\s*(\d+)\s+of\s+(\d+)\b")

CATEGORY_BY_LABEL = (
    ("Film", EventCategory.FILM),
    ("Lecture", EventCategory.LECTURES),
    ("Fellows' Presentation", EventCategory.LECTURES),
    ("Conference", EventCategory.LECTURES),
    ("Gallery", EventCategory.ARTS_CULTURE),
    ("Performance", EventCategory.MUSIC),
)


class RadcliffeInstituteScraper(BaseFeedScraper):
    HOME = None   # Knafel Center, Fay House, Byerly Hall, Schlesinger Library — and on the road
    CATEGORY = EventCategory.LECTURES

    def __init__(self):
        super().__init__(source_name="Radcliffe Institute", source_url=PAGE)

    def scrape_events(self) -> List[EventCreate]:
        soup = BeautifulSoup(self.fetch_html(self.source_url), "html.parser")
        cards = soup.select("div.m-card.m-card--event")
        if not cards:
            raise ValueError(f"{self.source_name}: no event cards on {self.source_url} — has the page changed?")
        showing = SHOWING.search(soup.get_text(" "))
        if showing and int(showing.group(1)) < int(showing.group(2)):
            logger.warning(f"{self.source_name}: page shows {showing.group(1)} of {showing.group(2)} — "
                           f"the rest are on later pages, which are not read")
        events = []
        for card in cards:
            event = self.parse_card(card)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name} ({len(cards)} cards)")
        return events

    def parse_card(self, card) -> Optional[EventCreate]:
        heading = card.select_one(".m-card__title")
        link = card.select_one(".m-card__left a[href]") or card.select_one("a[href]")
        if heading is None or link is None:
            return None
        title = self.clean_text(heading.get_text(""))
        url = link["href"].strip()
        if not self.is_event(title):
            return None
        status = card.select_one(".m-card__status")
        if status is not None and "cancel" in status.get_text().lower():
            logger.info(f"{self.source_name}: skipping cancelled '{title}'")
            return None

        info = self.info(card)
        when = info.get("date & time", [])
        if any(line.lower().startswith("through") for line in when):
            logger.debug(f"{self.source_name}: skipping '{title}' - a date range (exhibition)")
            return None
        start = self.start(title, url, when)
        if start is None:
            return None

        where = self.where(title, info.get("location", []))
        if where is None:
            return None

        teaser = card.select_one(".m-card__text")
        description = self.clean_text(teaser.get_text(" ")) if teaser else ""
        if len(description) < 20:
            description = self.fallback_description(title, where)

        label = card.select_one(".m-card__left > .m-card__label")
        image = card.select_one(".m-card__img img[src]")
        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            all_day=len(when) == 1,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.category_for(title, self.clean_text(label.get_text(" ")) if label else ""),
            source_name=self.source_name,
            source_url=url,
            image_url=image["src"] if image else None,
        )

    # -- helpers --------------------------------------------------------- #

    def info(self, card) -> dict:
        """{"date & time": [lines], "location": [lines]} from the card's right column."""
        found = {}
        for block in card.select(".m-card__info"):
            label = block.select_one(".m-card__label")
            if label is None:
                continue
            lines = [self.clean_text(s) for s in block.stripped_strings]
            found[self.clean_text(label.get_text()).lower()] = [ln for ln in lines[1:] if ln]
        return found

    def start(self, title: str, url: str, when: List[str]) -> Optional[datetime]:
        if not when:
            logger.warning(f"Skipping '{title}' - no date on its card ({url})")
            return None
        date = DATE.match(when[0])
        if date is None:
            logger.warning(f"Skipping '{title}' - unreadable date {when[0]!r} ({url})")
            return None
        day = datetime.strptime(date.group(2), "%B %d, %Y")
        if day.strftime("%A") != date.group(1):
            logger.warning(f"Skipping '{title}' - {date.group(2)} is not a {date.group(1)} ({url})")
            return None
        if len(when) == 1:
            return day  # date only: listed all day
        time = TIME.match(when[1])
        if time is None:
            logger.warning(f"Skipping '{title}' - unreadable time {when[1]!r} ({url})")
            return None
        hour, minute = int(time.group(1)), int(time.group(2) or 0)
        meridiem = (time.group(3) or time.group(4) or "").upper()
        if not meridiem or not 1 <= hour <= 12:
            logger.warning(f"Skipping '{title}' - time {when[1]!r} has no AM/PM ({url})")
            return None
        return day.replace(hour=hour % 12 + (12 if meridiem == "P" else 0), minute=minute)

    def where(self, title: str, lines: List[str]) -> Optional[Place]:
        if not lines:
            logger.info(f"{self.source_name}: skipping '{title}' - no location on its card")
            return None
        venue = OR_ONLINE.sub("", lines[0]).strip()
        if ONLINE.match(venue):
            return Place("Online", None, "Cambridge", None)
        street = next((ln for ln in lines[1:] if ln[:1].isdigit()), None)
        city = zip_code = None
        for line in reversed(lines[1:]):
            found = CITY_LINE.match(line)
            if found:
                city = found.group(1) if found.group(2) == "MA" else f"{found.group(1)}, {found.group(2)}"
                zip_code = found.group(3)
                break
        return self.place(title, venue, street, city, zip_code)

    def category_for(self, title: str, label: str) -> EventCategory:
        category = self.categorize(title)
        if category != self.CATEGORY:
            return category
        for word, mapped in CATEGORY_BY_LABEL:
            if word.lower() in label.lower():
                return mapped
        return self.CATEGORY
