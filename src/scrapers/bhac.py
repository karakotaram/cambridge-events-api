"""Black History in Action for Cambridge — its events page, timed from the ticket pages.

bhacambridge.org/events (Webflow) lists the organization's events newest
first, each with a printed date and an "Event info" link to its ticket page.
Through mid-2026 those were Eventbrite pages; the fall 2026 season moved to
Qgiv. Both ticketing sites publish a schema.org Event in JSON-LD with an
offset start and a full address, which base_eventbrite reads.

Which events are upcoming is the site's own call: each card has a "PAST EVENT"
tag that Webflow hides (class `w-condition-invisible`) until the event's date
has passed. Only cards whose tag is hidden are followed — usually one to three
ticket pages a day, not the forty historical links on the page. A card whose
printed date ("November 29, 2026") names a different day from its ticket
page is skipped as inconsistent. A card linking an Eventbrite collection
(a film's three screenings) is read as a collection.

The series is "a nomad" (Eitz Chayim, History Cambridge, Western Avenue
Baptist Church, St. Augustine's), so there is no HOME.
"""
import logging
import re
from datetime import datetime
from typing import List, Tuple

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_eventbrite import BaseEventbriteScraper, jsonld_events

logger = logging.getLogger(__name__)

LISTING = "https://www.bhacambridge.org/events"
TICKET_PAGE = re.compile(r"^https://(secure\.qgiv\.com/for/[^/]+/event/|www\.eventbrite\.com/e/)")
COLLECTION = re.compile(r"^https://www\.eventbrite\.com/cc/")


class BHACScraper(BaseEventbriteScraper):
    HOME = None
    CATEGORY = EventCategory.ARTS_CULTURE

    def __init__(self):
        super().__init__(source_name="Black History in Action", source_url=LISTING)

    def scrape_events(self) -> List[EventCreate]:
        soup = self.parse_html(self.fetch(self.source_url))
        cards = soup.select("div.event_list-item")
        if not cards:
            raise ValueError(f"{self.source_name}: no event cards at {self.source_url} — has the page changed?")
        events: List[EventCreate] = []
        for card in cards:
            events.extend(self.read_card(card))
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def tags(self, card) -> List[Tuple[str, bool]]:
        """[(text, shown)] for the card's tags: type, date, "PAST EVENT"."""
        return [(self.text(t.get_text(" ")), "w-condition-invisible" not in (t.get("class") or []))
                for t in card.select(".news_tag-wrapper .tag")]

    @staticmethod
    def upcoming(tags: List[Tuple[str, bool]]) -> bool:
        """Webflow shows the date and hides "PAST EVENT" until the date passes.

        Cards from 2024 have no date at all, which hides both tags; they are
        not upcoming, so a shown date is required as well as a hidden tag.
        """
        past_hidden = any("PAST" in text.upper() and not shown for text, shown in tags)
        date_shown = any(shown and text and text not in ("Events", "News") and "PAST" not in text.upper()
                         for text, shown in tags)
        return past_hidden and date_shown

    def read_card(self, card) -> List[EventCreate]:
        heading = card.select_one(".event_heading")
        title = self.text(heading.get_text(" ")) if heading else ""
        all_tags = self.tags(card)
        tags = [text for text, shown in all_tags if shown]
        if "Events" not in tags or not self.upcoming(all_tags):
            return []
        link = card.select_one("a.small-button")
        href = (link.get("href") or "").strip() if link else ""

        if COLLECTION.match(href):
            return self.read_page(self.fetch(href))
        if not TICKET_PAGE.match(href):
            logger.warning(f"Skipping '{title}' - its link {href!r} is not a ticket page with a machine-readable time")
            return []

        items = jsonld_events(self.fetch(href))
        if len(items) != 1:
            logger.warning(f"Skipping '{title}' - {len(items)} JSON-LD events at {href}")
            return []
        event = self.from_jsonld(items[0])
        if event is None:
            return []

        printed = self.printed_date(tags)
        if printed is not None and printed != event.start_datetime.date():
            logger.warning(f"Skipping '{title}' - card says {printed}, ticket page says "
                           f"{event.start_datetime:%Y-%m-%d} ({href})")
            return []
        return [event]

    @staticmethod
    def printed_date(tags: List[str]):
        """'November 29, 2026' -> date; None for anything less definite."""
        for tag in tags:
            try:
                return datetime.strptime(tag.strip(), "%B %d, %Y").date()
            except ValueError:
                continue
        return None
