"""Scraper for The Mad Monkfish jazz schedule (Jazz Baroness Room, Central Square).

The site is BentoBox. Two things about it shape this scraper:

- The listing is paged with `?p=N`, ten cards a page. The pager's labels are
  backwards — the link *forward* in time is labelled "Previous" — and "Load More
  Events" is a `<button>` that fetches the same `?p=N` pages by script. Past the
  last page the site wraps round to page 1, so paging stops when a page adds no
  event it has not already seen.
- A card carries only a title such as "10/10 Nick Brust Late Night Jam Session".
  Most titles name no time, and a "12-1am" set is listed under the evening
  before the date it actually starts on. The event page states both plainly
  ("October 10, 2026 10:00 PM until …"), so every event is dated from there.
"""
import html
import json
import logging
import re
from datetime import datetime, timedelta
from typing import List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

# Four pages hold ~5 weeks of shows. The cap only bounds a pager that never
# wraps or ends; the stop condition is "nothing new".
MAX_PAGES = 8

_WHEN = re.compile(
    r"([A-Z][a-z]+ \d{1,2}, \d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)"
    r"(?:\s+until\s+([A-Z][a-z]+ \d{1,2}, \d{4})\s+(\d{1,2}:\d{2}\s*[AP]M))?")

# "7pm", "12-1am", "(3pm-6pm)" — times written into a card title.
_TITLE_TIME = re.compile(
    r"\(?\s*\b\d{1,2}(?::\d{2})?\s*(?:am|pm)?\s*[-–]\s*\d{1,2}(?::\d{2})?\s*(?:am|pm)\b\s*\)?"
    r"|\(?\s*\b\d{1,2}(?::\d{2})?\s*(?:am|pm)\b\s*\)?",
    re.I)


class MadMonkfishScraper(BaseScraper):
    """Scraper for The Mad Monkfish jazz schedule in Cambridge"""

    def __init__(self):
        super().__init__(
            source_name="The Mad Monkfish",
            source_url="https://www.themadmonkfish.com/jazz-schedule/",
            use_selenium=False
        )
        self.base_url = "https://www.themadmonkfish.com"

    def page_url(self, page: int) -> str:
        return self.source_url if page == 1 else f"{self.source_url}?p={page}"

    def scrape_events(self) -> List[EventCreate]:
        listed = self.read_listing()
        events = []
        for url, title, image in listed:
            try:
                detail = self.parse_html(self.fetch_html(url))
            except Exception as e:
                logger.warning(f"Skipping '{title}' - event page failed to load ({url}): {e}")
                continue
            event = self.parse_detail(detail, url, title, image)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} of {len(listed)} listed events from The Mad Monkfish")
        return events

    def read_listing(self) -> List[Tuple[str, str, Optional[str]]]:
        """Every (url, card title, image) across the pager, in listing order."""
        seen: dict = {}
        for page in range(1, MAX_PAGES + 1):
            soup = self.parse_html(self.fetch_html(self.page_url(page)))
            new = [card for card in self.parse_cards(soup) if card[0] not in seen]
            if not new:
                break  # past the last page the site wraps round to page 1
            for card in new:
                seen[card[0]] = card
            logger.info(f"Mad Monkfish page {page}: {len(new)} events")
            if not soup.find("a", href=re.compile(rf"[?&]p={page + 1}\b")):
                break
        return list(seen.values())

    def parse_cards(self, soup: BeautifulSoup) -> List[Tuple[str, str, Optional[str]]]:
        cards = []
        for link in soup.select("ul.card-listing a.card__btn[href*='/event/']"):
            heading = link.select_one(".card__heading")
            title = self.clean_text((heading or link).get_text())
            if not title:
                continue
            image = None
            media = link.select_one(".card__image[style]")
            if media:
                m = re.search(r"url\('([^']+)'\)", media["style"])
                image = m.group(1) if m else None
            cards.append((urljoin(self.base_url, link["href"]), title, image))
        return cards

    @staticmethod
    def clean_title(text: str) -> str:
        """Drop the leading "10/9" and any time written into the title.

        The old pattern removed only the "1am" of "12-1am", publishing titles
        ending in "Quintero 12-".
        """
        title = re.sub(r"^\s*\d{1,2}/\d{1,2}\s+", "", text)
        title = _TITLE_TIME.sub(" ", title)
        title = re.sub(r"\(\s*\)", " ", title)
        return " ".join(title.split())

    @staticmethod
    def read_when(soup: BeautifulSoup) -> Tuple[Optional[datetime], Optional[datetime]]:
        """Start and end from the event page's "October 10, 2026 10:00 PM until …".

        No time on the page means no start: the old fallback put every such
        listing at 7 PM, including a 10 PM late-night jam.
        """
        scope = soup.find("article") or soup
        m = _WHEN.search(" ".join(scope.get_text(" ").split()))
        if not m:
            return None, None
        try:
            start = datetime.strptime(f"{m.group(1)} {m.group(2).replace(' ', '')}", "%B %d, %Y %I:%M%p")
        except ValueError:
            return None, None
        end = None
        if m.group(3):
            try:
                end = datetime.strptime(f"{m.group(3)} {m.group(4).replace(' ', '')}", "%B %d, %Y %I:%M%p")
            except ValueError:
                end = None
            # "10:00 PM until October 10, 2026 12:00 AM" means the midnight that ends the night
            if end is not None and end <= start and end.date() == start.date():
                end += timedelta(days=1)
            if end is not None and end < start:
                end = None
        return start, end

    @staticmethod
    def _event_json_ld(soup: BeautifulSoup) -> dict:
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except (TypeError, ValueError):
                continue
            if isinstance(data, dict) and data.get("@type") == "Event":
                return data
        return {}

    def parse_detail(self, soup: BeautifulSoup, url: str, card_title: str,
                     image: Optional[str] = None) -> Optional[EventCreate]:
        start, end = self.read_when(soup)
        if start is None:
            logger.warning(f"Skipping '{card_title}' - no time on its event page ({url})")
            return None

        structured = self._event_json_ld(soup)
        listed_date = str(structured.get("startDate") or "")[:10]
        if listed_date and listed_date != start.date().isoformat():
            logger.warning(f"Skipping '{card_title}' - page says {start:%Y-%m-%d} but its "
                           f"structured data says {listed_date} ({url})")
            return None

        title = self.clean_title(html.unescape(card_title)) or card_title
        if not image:
            image = (structured.get("image") or {}).get("url") if isinstance(structured.get("image"), dict) else None

        return EventCreate(
            title=title[:200],
            description=f"{title} - Live jazz in the Jazz Baroness Room at The Mad Monkfish"[:2000],
            start_datetime=start,
            end_datetime=end,
            venue_name="The Mad Monkfish - Jazz Baroness Room",
            street_address="524 Massachusetts Ave",
            city="Cambridge",
            state="MA",
            zip_code="02139",
            category=EventCategory.MUSIC,
            source_name=self.source_name,
            source_url=url,
            image_url=image,
        )
