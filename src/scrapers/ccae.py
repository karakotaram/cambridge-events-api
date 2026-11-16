"""Cambridge Center for Adult Education — public events and the Blacksmith House readings.

Not the course catalogue: only what CCAE lists under Happenings > Events, which
is where the Blacksmith House Poetry Series readings are posted too. The series
page (/blacksmithpoetry) links the same event pages and is read as a second
listing in case a reading is linked there first.

The listing cards print "November 16 | 8:00pm – 9:00pm" with no year, so each
event page is read instead: its JSON-LD Event carries `startDate`
"2026-11-16T20:00:00-05:00", a full date with its offset.

**Where.** Event pages print CCAE's main address, 42 Brattle Street. The poetry
series says on its own page and in each reading's text that readings are held
at the Blacksmith House, 56 Brattle Street, so its readings are placed there.
"""
import json
import logging
import re
from datetime import datetime
from typing import Iterable, List, Optional

from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

LISTINGS = ("https://ccae.org/happenings/events", "https://ccae.org/blacksmithpoetry")
EVENT_LINK = re.compile(r"^https://ccae\.org/happenings/events/[a-z0-9-]+/?$")

BLACKSMITH_HOUSE = Place("Blacksmith House", "56 Brattle Street", "Cambridge", "02138")
POETRY_SERIES = re.compile(r"^blacksmith house poetry series\b", re.I)
ADMISSION = re.compile(r"\badmission is (\$\d+(?:\.\d{2})?|free)\b", re.I)
STATE_TAIL = re.compile(r"\s*,?\s*\b(MA|Mass\.?|Massachusetts)\b.*$")


class CCAEScraper(BaseFeedScraper):
    HOME = Place("Cambridge Center for Adult Education", "42 Brattle Street", "Cambridge", "02138")
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Cambridge Center for Adult Education", source_url=LISTINGS[0])

    def scrape_events(self) -> List[EventCreate]:
        links: List[str] = []
        for listing in LISTINGS:
            for link in self.event_links(self.fetch_html(listing)):
                if link not in links:
                    links.append(link)
        events: List[EventCreate] = []
        for url in links:
            event = self.parse_event_page(url, self.fetch_html(url))
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def event_links(self, html: str) -> List[str]:
        soup = BeautifulSoup(html, "html.parser")
        main = soup.find("main") or soup
        links = []
        for a in main.find_all("a", href=True):
            href = a["href"].strip()
            if EVENT_LINK.match(href) and href.rstrip("/") not in links:
                links.append(href.rstrip("/"))
        return links

    # -- one event page ------------------------------------------------------ #

    @staticmethod
    def _graph(soup) -> Iterable[dict]:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except ValueError:
                continue
            nodes = data.get("@graph", [data]) if isinstance(data, dict) else data
            for node in nodes if isinstance(nodes, list) else []:
                if isinstance(node, dict):
                    yield node

    @staticmethod
    def _instant(value) -> Optional[datetime]:
        """ISO 8601 with an offset -> naive Eastern. Without an offset it is not trusted."""
        try:
            parsed = datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None
        return to_eastern_naive(parsed) if parsed.tzinfo else None

    def _printed_address(self, soup):
        """(street, city) from the event header's address block, if it has one."""
        for block in soup.select("p.event-header__content-details"):
            lines = [line.strip() for line in block.get_text("\n").split("\n") if line.strip()]
            if len(lines) >= 2 and re.match(r"^\d+\s", lines[0]):
                city = STATE_TAIL.sub("", lines[1]).strip()
                zip_match = re.search(r"\b(\d{5})\b", lines[1])
                return lines[0], (f"{city}, MA {zip_match.group(1)}" if zip_match else city) or None
        return None, None

    def place_for(self, title: str, soup) -> Optional[Place]:
        if POETRY_SERIES.match(title):
            return BLACKSMITH_HOUSE
        street, city = self._printed_address(soup)
        if street and re.match(r"^42 Brattle", street, re.I):
            return self.HOME
        return self.place(title, None, street, city)

    def parse_event_page(self, url: str, html: str) -> Optional[EventCreate]:
        soup = BeautifulSoup(html, "html.parser")
        node = next((n for n in self._graph(soup) if n.get("@type") == "Event"), None)
        if node is None:
            logger.warning(f"Skipping {url} - no JSON-LD Event")
            return None

        title = self.text(node.get("name"))
        if not self.is_event(title):
            return None
        if "cancel" in str(node.get("eventStatus", "")).lower() or re.search(r"\bcancell?ed\b", title, re.I):
            logger.info(f"{self.source_name}: skipping cancelled '{title}'")
            return None

        start = self._instant(node.get("startDate"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no zoned startDate {node.get('startDate')!r} ({url})")
            return None
        end = self._instant(node.get("endDate"))
        if end is not None and end <= start:
            end = None

        where = self.place_for(title, soup)
        if where is None:
            return None

        body = soup.select_one(".rich-text")
        description = self.text(body.get_text(" ")) if body else ""
        if len(description) < 20:
            description = self.text(node.get("description"))
        if len(description) < 20:
            description = self.fallback_description(title, where)

        image = node.get("image")
        image_url = image.get("url") if isinstance(image, dict) else (image if isinstance(image, str) else None)

        category = EventCategory.LECTURES if POETRY_SERIES.match(title) else self.categorize(title)
        admission = ADMISSION.search(description)
        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=category,
            cost=admission.group(1) if admission else None,
            source_name=self.source_name,
            source_url=url,
            image_url=image_url,
        )
