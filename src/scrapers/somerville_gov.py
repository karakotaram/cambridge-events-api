"""Custom scraper for the City of Somerville calendar

somervillema.gov is Drupal and renders server-side: `/calendar` lists every
upcoming event from today, 20 to a page, as `.views-row` blocks. Each row holds
one `<time>` for the start and usually a second for the end.

Every `<time>` carries the moment twice, as a UTC instant in its `datetime`
attribute and as Eastern wall-clock text ("Mon, October 5, 2026 - 10:00am"). A
start is only believed when the two agree. On 2026-10-05 all ~400 did; a
disagreement means one of them changed meaning, and dropping the event beats
guessing which one to trust.

The listing has no venue, so each event's detail page is fetched for its
address. That pass never touches a date.
"""
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Optional, Tuple

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory, to_eastern_naive

logger = logging.getLogger(__name__)

BASE = "https://www.somervillema.gov"

# ~14 pages cover nine months as of 2026-10. The cap only stops a pager that
# never ends.
MAX_PAGES = 40
DETAIL_WORKERS = 6

# Rows the city publishes that are not events a reader can attend:
#   "Holiday: ..."           city office closures, listed at 12:00am
#   "... Executive Session"  closed to the public under the Open Meeting Law
#   "Example Meeting Event"  a CMS placeholder that is live on the calendar
NOT_EVENTS = re.compile(r"^holiday:|executive session|^example meeting event$", re.IGNORECASE)

# "Mon, October 5, 2026 - 10:00am"
VISIBLE_TIME = re.compile(r"([A-Z][a-z]+ \d{1,2}, \d{4})\s*-\s*(\d{1,2}:\d{2}\s*[ap]m)", re.IGNORECASE)


class SomervilleGovScraper(BaseScraper):
    """Custom scraper for City of Somerville events"""

    def __init__(self):
        super().__init__(
            source_name="City of Somerville",
            source_url=f"{BASE}/calendar",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        seen = set()  # (url, start) - a recurring event shares one URL across dates

        for page in range(MAX_PAGES):
            url = f"{self.source_url}?page={page}"
            try:
                soup = self.parse_html(self.fetch_html(url))
            except Exception as e:
                logger.error(f"Failed to fetch {url}: {e}")
                break

            new = 0
            for event in self.parse_listing(soup):
                key = (event.source_url, event.start_datetime)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
                    new += 1

            # A page that adds nothing means the pager is repeating itself
            if not new or not soup.select_one(".pager__item--next a"):
                break

        self.enrich_from_detail_pages(events)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_listing(self, soup) -> List[EventCreate]:
        """Every attendable, dateable event on one listing page."""
        events = []
        for row in soup.select(".views-row"):
            title_el = row.select_one(".views-field-title")
            times = row.select(".views-field-field-event-date time")
            if not title_el or not times:
                continue

            title = self.clean_text(title_el.get_text())
            if len(title) < 3 or NOT_EVENTS.search(title):
                continue
            if "CANCELLED" in title.upper() or "CANCELED" in title.upper():
                continue

            start = self.read_time(times[0])
            if start is None:
                # Never guess. An event with an unknown date is worse than a
                # missing one - it pollutes another day.
                logger.warning(f"Skipping '{title}' - start time unreadable or self-contradictory")
                continue
            end = self.read_time(times[1]) if len(times) > 1 else None

            link = title_el.find("a", href=True)
            if link:
                event_url = link["href"]
                if event_url.startswith("/"):
                    event_url = f"{BASE}{event_url}"
            else:
                # A few rows have no detail page; link to that day's listing
                event_url = f"{self.source_url}?event_date={start:%Y-%m-%d}"

            body = row.select_one(".views-field-body")
            description = self.clean_text(body.get_text()) if body else ""

            image = row.select_one(".views-field-field-preview-image img[src]")
            image_url = self._normalize_image_url(image["src"], BASE) if image else None

            events.append(EventCreate(
                title=title[:200],
                description=(description or title)[:2000],
                start_datetime=start,
                end_datetime=end if end and end > start else None,
                source_url=event_url,
                source_name=self.source_name,
                venue_name="Online" if "virtual" in title.lower() else None,
                city="Somerville",
                state="MA",
                image_url=image_url,
                category=self.categorize_event(title, description),
            ))
        return events

    @classmethod
    def read_time(cls, time_el) -> Optional[datetime]:
        """Naive Eastern time from a `<time>`, or None unless both renderings agree."""
        raw = (time_el.get("datetime") or "").strip()
        match = VISIBLE_TIME.search(" ".join(time_el.get_text().split()))
        if not raw or not match:
            return None
        try:
            instant = to_eastern_naive(datetime.fromisoformat(raw.replace("Z", "+00:00")))
            visible = datetime.strptime(f"{match.group(1)} {match.group(2).replace(' ', '').upper()}",
                                        "%B %d, %Y %I:%M%p")
        except ValueError:
            return None
        if instant != visible:
            logger.warning(f"<time> disagrees with itself: {raw!r} vs {match.group(0)!r}")
            return None
        return visible

    def enrich_from_detail_pages(self, events: List[EventCreate]) -> None:
        """Add venue and address from each event's detail page.

        Best-effort: every event already has its date, title, and description
        from the listing, so a failed fetch just means a card with no venue.
        """
        by_url = {}
        for event in events:
            if "/events/" in event.source_url:
                by_url.setdefault(event.source_url, []).append(event)
        if not by_url:
            return

        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            for url, details in zip(by_url, pool.map(self.fetch_event_details, by_url)):
                venue, street, city, zip_code, description = details
                for event in by_url[url]:
                    if venue:
                        event.venue_name = venue
                        event.street_address = street
                        event.city = city or event.city
                        event.zip_code = zip_code
                    if description and len(description) > len(event.description):
                        event.description = description[:2000]

    def fetch_event_details(self, url: str) -> Tuple[Optional[str], ...]:
        """(venue, street, city, zip, description) from a detail page.

        Deliberately does NOT return a date. The listing is the only source of
        truth for when an event happens.
        """
        try:
            soup = self.parse_html(self.fetch_html(url, retries=2))
        except Exception as e:
            logger.warning(f"Could not fetch detail page {url}: {e}")
            return None, None, None, None, None

        venue = street = city = zip_code = None
        address = soup.select_one(".field--name-field-address .address")
        if address:
            def part(cls):
                el = address.select_one(f".{cls}")
                return self.clean_text(el.get_text()) if el else None

            lines = [p for p in (part("address-line1"), part("address-line2")) if p]
            street = ", ".join(lines) or None
            venue = part("organization") or street
            city = part("locality")
            zip_code = part("postal-code")

        # The page has several body fields (accessibility notice, feedback
        # form); the event's own comes first. Inside it, the inline <style> is
        # not prose, and the accessibility and interpreter notices are laid out
        # as an icon beside text in a table - boilerplate, not the event.
        description = None
        body = soup.select_one("main .field--name-body")
        if body:
            for tag in body.find_all(["style", "script"]):
                tag.decompose()
            for table in body.find_all("table"):
                if table.find("img"):
                    table.decompose()
            description = self.clean_text(body.get_text(" "))

        return venue, street, city, zip_code, description

    @staticmethod
    def categorize_event(title: str, description: str) -> EventCategory:
        """Categorize by keyword. Order matters: "Zumba Gold (Council on Aging)"
        is a fitness class, not a council meeting."""
        text = f"{title} {description}".lower()
        if any(w in text for w in ["exercise", "yoga", "zumba", "walking club", "healthy steps",
                                   "eastern flow", "tai chi", "fitness"]):
            return EventCategory.SPORTS
        if any(w in text for w in ["meeting", "committee", "commission", "board", "hearing",
                                   "subcommittee", "office hours"]):
            return EventCategory.COMMUNITY
        if any(w in text for w in ["concert", "music", "band", "festival of activist street bands"]):
            return EventCategory.MUSIC
        if any(w in text for w in ["theater", "theatre", "shakespeare", "performance"]):
            return EventCategory.THEATER
        if any(w in text for w in ["farmers market", "luncheon", "tea party", "dinner", "brunch"]):
            return EventCategory.FOOD_DRINK
        if any(w in text for w in ["workshop", "training", "class", "lecture", "talk", "seminar"]):
            return EventCategory.LECTURES
        if any(w in text for w in ["festival", "celebration", "heritage", "art", "movie", "bingo",
                                   "knitting", "crochet", "mahjong", "halloween"]):
            return EventCategory.ARTS_CULTURE
        return EventCategory.COMMUNITY
