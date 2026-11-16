"""Cambridge KiOSK, Harvard Square — the city-run kiosk's own events pages.

cambridgekiosk.org is WordPress with a custom `event` post type whose REST
records carry no dates, so the pages are read instead:

  /events/   an "upcoming" grid of links; any past the first four sit in a
             JSON array (`additionalEvents_<grid id>`) for its Load More button
  /events/<slug>/   "When" ("November 29, 2026" / "5:00 pm–7:00 pm"), "Where",
             "Partner", and an Add-to-Google-Calendar link carrying UTC times

**Two clocks must agree.** The printed time is the one readers see; the Google
Calendar link's `dates=20261129T220000Z/...` is the instant behind it. An event
whose two disagree is skipped rather than published at either.

**Runs are not sessions.** "November 2–November 30, 2026" is an installation
(the Harvard Square Gift Shop), not a time to turn up, and is skipped.

Many KiOSK events also appear on the City of Cambridge calendar (the Community
Development Department runs the KiOSK); not all do. Cross-source deduplication
merges the shared ones.
"""
import json
import logging
import re
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

SINGLE_DATE = re.compile(r"^[A-Z][a-z]+\.? \d{1,2}, \d{4}$")
TIME = re.compile(r"\b(\d{1,2}):(\d{2})\s*([ap])\.?m\b", re.I)
EVENT_LINK = re.compile(r"^https://cambridgekiosk\.org/events/[^/?#]+/?$")


class CambridgeKioskScraper(BaseFeedScraper):
    HOME = Place("Cambridge KiOSK", "0 Harvard Square", "Cambridge", "02138")
    CATEGORY = EventCategory.ARTS_CULTURE

    def __init__(self):
        super().__init__(source_name="Cambridge KiOSK", source_url="https://cambridgekiosk.org/events/")

    def scrape_events(self) -> List[EventCreate]:
        links = self.upcoming_links(self.fetch_html(self.source_url))
        events: List[EventCreate] = []
        for url in links:
            event = self.parse_event_page(url, self.fetch_html(url))
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- listing ----------------------------------------------------------- #

    def upcoming_links(self, html: str) -> List[str]:
        """Event pages in the "upcoming" grid, including its Load More overflow."""
        soup = BeautifulSoup(html, "html.parser")
        heading = next((h for h in soup.find_all("h2") if self.clean_text(h.get_text()).lower() == "upcoming"),
                       None)
        if heading is None:
            raise ValueError(f"{self.source_name}: no 'upcoming' heading on {self.source_url}")
        grid = heading.find_next(class_="event-grid")
        if grid is None:
            return []

        links = [a.get("href") for a in grid.find_all("a", class_="event-item")]
        grid_id = (grid.get("id") or "").replace("event-list-", "")
        overflow = re.search(rf"additionalEvents_{re.escape(grid_id)}\s*=\s*(\[.*?\]);", html, re.S) if grid_id else None
        if overflow:
            try:
                links += [e.get("permalink") for e in json.loads(overflow.group(1))]
            except (ValueError, AttributeError):
                logger.warning(f"{self.source_name}: unreadable Load More list for the upcoming grid")

        seen, ordered = set(), []
        for link in links:
            if link and EVENT_LINK.match(link) and link not in seen:
                seen.add(link)
                ordered.append(link)
        return ordered

    # -- one event page ------------------------------------------------------ #

    @staticmethod
    def _block(soup, label: str) -> List[str]:
        """Lines of the <p> under the details heading `label` ("When", "Where")."""
        heading = next((h for h in soup.find_all("h4") if h.get_text(strip=True).lower() == label.lower()), None)
        para = heading.find_next_sibling("p") if heading else None
        if para is None:
            return []
        return [line.strip() for line in para.get_text("\n").split("\n") if line.strip()]

    @staticmethod
    def _google_start(soup) -> Optional[datetime]:
        """The UTC start in the page's Add-to-Google-Calendar link, as Eastern wall clock."""
        link = soup.find("a", href=re.compile(r"google\.com/calendar/render"))
        if link is None:
            return None
        dates = (parse_qs(urlparse(link["href"]).query).get("dates") or [""])[0]
        match = re.match(r"(\d{8}T\d{6})Z", dates)
        if not match:
            return None
        instant = datetime.strptime(match.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return to_eastern_naive(instant)

    def parse_event_page(self, url: str, html: str) -> Optional[EventCreate]:
        soup = BeautifulSoup(html, "html.parser")
        heading = soup.find("h1")
        title = self.text(heading.get_text(" ")) if heading else ""
        if title.islower():
            title = title[0].upper() + title[1:]  # the site styles every title in lower case
        if not self.is_event(title):
            return None

        when = self._block(soup, "When")
        day_text = when[0] if when else ""
        if not SINGLE_DATE.match(day_text):
            if day_text and re.search(r"[–-]", day_text):
                logger.info(f"{self.source_name}: skipping '{title}' - runs {day_text!r}, an installation "
                            f"rather than a session")
            else:
                logger.warning(f"Skipping '{title}' - no parseable date {day_text!r} ({url})")
            return None
        try:
            day = date_parser.parse(day_text).date()
        except (ValueError, OverflowError):
            logger.warning(f"Skipping '{title}' - no parseable date {day_text!r} ({url})")
            return None

        times = [(int(h) % 12 + (12 if m.lower() == "p" else 0), int(mm))
                 for h, mm, m in TIME.findall(" ".join(when[1:]))]
        all_day = not times
        start = datetime(day.year, day.month, day.day, *(times[0] if times else (0, 0)))
        end = datetime(day.year, day.month, day.day, *times[1]) if len(times) > 1 else None
        if end is not None and end <= start:
            end = None

        if not all_day:
            google = self._google_start(soup)
            if google is not None and google != start:
                logger.warning(f"Skipping '{title}' - page says {start:%Y-%m-%d %H:%M}, its calendar link "
                               f"says {google:%Y-%m-%d %H:%M} ({url})")
                return None

        where_lines = self._block(soup, "Where")
        venue = where_lines[0] if where_lines else None
        street, city = None, None
        if len(where_lines) > 1:
            parts = [p.strip() for p in where_lines[1].split(",")]
            street, city = parts[0], ", ".join(parts[1:]) or None
        where = self.place(title, venue, street, city)
        if where is None:
            return None

        body = soup.select_one("div.entry-content.wp-block-post-content:not(.event-info-block)")
        paragraphs = [self.text(p.get_text(" ")) for p in body.find_all("p")] if body else []
        description = " ".join(p for p in paragraphs if p)
        partner = " ".join(self._block(soup, "Partner"))
        if partner:
            description = f"{description} Partner: {partner}.".strip()
        if len(description) < 20:
            description = self.fallback_description(title, where)

        image = soup.find("meta", property="og:image")
        free = re.search(r"\bfree\b", description, re.I) is not None

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
            category=self.categorize(title),
            cost="Free" if free else None,
            source_name=self.source_name,
            source_url=url,
            image_url=image.get("content") if image else None,
        )
