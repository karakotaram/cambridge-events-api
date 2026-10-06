"""Custom scraper for Multicultural Arts Center events.

The /events/ page is Elementor with two post grids: "Upcoming Events" and
"Past Events". Each card carries the event's date ("October 7, 2026" or a run,
"January 15–17, 2027"). The event page adds the time as its own heading
directly under the date ("7:30 PM", "6:00 - 8:00 PM").

The site rate-limits hard (HTTP 429 after a short burst), so this reads one
listing page and one event page per upcoming card — about seven requests —
spaced a second apart. The event page also embeds an "Upcoming Events" grid
of other events' dates, so nothing is read from outside the event's own
header and body.
"""
import logging
import re
import time
from datetime import datetime
from typing import List, Optional, Tuple

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

_MONTHS = {m[:3].lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)}
_MONTH = r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
_DATE = re.compile(
    rf"^{_MONTH}\s+(\d{{1,2}})(?:\s*[-–—]\s*(?:{_MONTH}\s+)?(\d{{1,2}}))?,?\s+(\d{{4}})$", re.I)
_CLOCK = r"(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?\s*m\b\.?)?"
_TIME = re.compile(rf"^{_CLOCK}(?:\s*(?:[-–—]|to)\s*{_CLOCK})?", re.I)


class MulticulturalArtsCenterScraper(BaseScraper):
    """Custom scraper for Multicultural Arts Center events"""

    # Seconds between event-page requests; the site answers 429 to bursts.
    REQUEST_DELAY = 1.0

    def __init__(self):
        super().__init__(
            source_name="Multicultural Arts Center",
            source_url="https://multiculturalartscenter.org/events/",
            use_selenium=False
        )
        self.base_url = "https://multiculturalartscenter.org"

    def scrape_events(self) -> List[EventCreate]:
        cards = self.upcoming_cards(self.parse_html(self.fetch_html(self.source_url)))
        events = []
        for title, url, date_text, image in cards:
            time.sleep(self.REQUEST_DELAY)
            try:
                detail = self.parse_html(self.fetch_html(url))
            except Exception as e:
                logger.warning(f"Skipping '{title}' - event page failed to load ({url}): {e}")
                continue
            event = self.parse_detail(detail, title, url, date_text, image)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} of {len(cards)} upcoming events from Multicultural Arts Center")
        return events

    def upcoming_cards(self, soup) -> List[Tuple[str, str, str, Optional[str]]]:
        """(title, url, date text, image) for each card in the Upcoming grid.

        The old scraper also walked /events/page/2/ and /page/3/, which repeat
        the same grids, and fetched an event page for every past card too.
        """
        articles = soup.select("article.elementor-post.category-event-upcoming")
        if not articles:
            heading = soup.find(lambda t: t.name in ("h2", "h3", "h4")
                                and t.get_text(strip=True).lower() == "upcoming events")
            grid = heading.find_next(class_="elementor-posts-container") if heading else None
            articles = grid.select("article.elementor-post") if grid else []
            if not articles:
                logger.warning("Multicultural Arts Center: no Upcoming Events grid on the listing")

        cards = []
        for article in articles:
            link = article.select_one("h3 a[href]")
            excerpt = article.select_one(".elementor-post__excerpt")
            if not link:
                continue
            title = self.clean_text(link.get_text())
            date_text = self.clean_text(excerpt.get_text(" ")) if excerpt else ""
            img = article.find("img")
            image = (img.get("src") or img.get("data-src")) if img else None
            cards.append((title, link["href"], date_text, image))
        return cards

    @staticmethod
    def parse_date_text(text: str) -> Optional[Tuple[datetime, Optional[datetime]]]:
        """(first day, last day or None) from "October 7, 2026" / "January 15–17, 2027"."""
        m = _DATE.match(" ".join((text or "").split()))
        if not m:
            return None
        month1 = _MONTHS[m.group(1)[:3].lower()]
        month2 = _MONTHS[m.group(3)[:3].lower()] if m.group(3) else month1
        year = int(m.group(5))
        try:
            first = datetime(year, month1, int(m.group(2)))
            last = datetime(year if month2 >= month1 else year + 1, month2, int(m.group(4))) if m.group(4) else None
        except ValueError:
            return None
        return first, last

    @staticmethod
    def parse_time_text(text: str, day: datetime) -> Optional[Tuple[datetime, Optional[datetime]]]:
        """(start, end) from "7:30 PM" or "6:00 - 8:00 PM" on `day`; None if unreadable.

        A start with no meridiem of its own borrows the end's, as the venue
        writes ranges. A time with no meridiem anywhere is unreadable.
        """
        m = _TIME.match(" ".join((text or "").split()))
        if not m:
            return None
        h1, m1, ap1, h2, m2, ap2 = m.groups()
        ap_start = ap1 or ap2
        if not ap_start:
            return None

        def at(hour, minute, ap):
            hour, minute = int(hour), int(minute or 0)
            if not (1 <= hour <= 12 and minute < 60):
                raise ValueError
            hour = hour % 12 + (12 if ap.lower() == "p" else 0)
            return day.replace(hour=hour, minute=minute)

        try:
            start = at(h1, m1, ap_start)
            end = at(h2, m2, ap2) if h2 and ap2 else None
        except ValueError:
            return None
        if end is not None and start > end and not ap1:
            start = start.replace(hour=start.hour - 12)    # "11:00 - 1:00 PM" starts in the morning
        if end is not None and end <= start:
            end = None
        return start, end

    def _header_and_body(self, soup) -> Tuple[List[str], str]:
        """The event's own heading texts, in order, and its body text.

        Stops at the page's "Upcoming Events" grid: the old date search ran
        over the whole page and took that grid's first date, giving a past
        event ("The X-tet") a date that belonged to another event.
        """
        root = soup.select_one("main [data-elementor-type=wp-post]") or soup.find("main")
        if root is None:
            return [], ""
        headings, body = [], ""
        for widget in root.select("div.elementor-widget"):
            kind = widget.get("data-widget_type") or ""
            text = self.clean_text(widget.get_text(" "))
            if kind.startswith("posts") or text.lower() == "upcoming events":
                break
            if kind.startswith("heading") and text:
                headings.append(text)
            elif kind.startswith("text-editor") and text and not body:
                body = text
        return headings, body

    def parse_detail(self, soup, title: str, url: str, card_date: str,
                     image: Optional[str] = None) -> Optional[EventCreate]:
        dates = self.parse_date_text(card_date)
        if dates is None:
            logger.warning(f"Skipping '{title}' - unreadable date {card_date!r} on its card ({url})")
            return None
        first, last = dates

        headings, body = self._header_and_body(soup)
        at = next((i for i, h in enumerate(headings) if self.parse_date_text(h) == dates), None)
        if at is None:
            logger.warning(f"Skipping '{title}' - its page does not repeat the card's date {card_date!r} ({url})")
            return None
        following = headings[at + 1] if at + 1 < len(headings) else ""
        time_text = following if re.search(r"\d|\btba\b|\btbd\b", following, re.I) else ""

        end = None
        if last is not None:
            # A run: one listing for its first day, all-day, with the
            # venue's own schedule ("8pm (Fri + Sat) and 2pm (Sun)") in the text.
            start, all_day, end = first, True, last
        elif not time_text:
            start, all_day = first, True
        else:
            times = self.parse_time_text(time_text, first)
            if times is None:
                logger.warning(f"Skipping '{title}' - unreadable time {time_text!r} ({url})")
                return None
            (start, end), all_day = times, False

        when = f"{card_date}" + (f", {time_text}" if time_text else "")
        description = body or f"{title} at Multicultural Arts Center, 41 Second Street, Cambridge."
        if last is not None:
            description = f"{when}. {description}"

        if not image:
            og = soup.find("meta", property="og:image")
            image = og.get("content") if og else None

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            venue_name="Multicultural Arts Center",
            street_address="41 Second Street",
            city="Cambridge",
            state="MA",
            zip_code="02141",
            category=EventCategory.ARTS_CULTURE,
            image_url=image,
            source_name=self.source_name,
            source_url=url,
        )
