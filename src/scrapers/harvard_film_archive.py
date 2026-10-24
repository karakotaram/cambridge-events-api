"""Harvard Film Archive screenings from its calendar page.

Each screening card carries a `<time datetime="2026-10-24 19:00:00">` on a
24-hour clock, sits under a day heading `<time datetime="2026-10-24">`, and
shows its time as text ("7:00 pm"). A start is kept only when all three agree:
Cambridge.gov's machine-readable time once turned out to be a 12-hour clock
with no meridiem, and a disagreement here is the signal that something like
that has happened. A day with more than three screenings wraps onto a row whose
heading is empty; that row belongs to the day above it.

The listing is paged ("View more" links to /calendar?page=2, rel="next").
"""
import logging
import re
from datetime import date, datetime
from typing import Dict, List, Optional
from urllib.parse import urljoin

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

BASE_URL = "https://harvardfilmarchive.org"
MAX_PAGES = 8  # the calendar runs about two months ahead, two pages in Oct 2026


class HarvardFilmArchiveScraper(BaseScraper):
    """Screenings at the Harvard Film Archive cinematheque"""

    def __init__(self):
        super().__init__(
            source_name="Harvard Film Archive",
            source_url=f"{BASE_URL}/calendar",
            use_selenium=False  # server-rendered
        )
        self._details: Dict[str, dict] = {}

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        url: Optional[str] = self.source_url
        visited = set()

        while url and url not in visited and len(visited) < MAX_PAGES:
            visited.add(url)
            soup = self.parse_html(self.fetch_html(url))
            events.extend(self.parse_page(soup))
            more = soup.find('a', rel='next', href=True)
            url = urljoin(BASE_URL, more['href']) if more else None

        if url and url not in visited:
            logger.warning(f"Stopped after {MAX_PAGES} pages with more to read ({url})")

        logger.info(f"Scraped {len(events)} screenings from {len(visited)} Harvard Film Archive page(s)")
        return events

    def parse_page(self, soup) -> List[EventCreate]:
        """Every screening on one calendar page, in page order."""
        events = []
        day: Optional[date] = None

        for node in soup.select('h4.m-calendar__spot--day, div.m-calendar__spot--event'):
            if node.name == 'h4':
                heading = node.find('time', attrs={'datetime': True})
                if heading:  # an empty heading continues the day above
                    try:
                        day = date.fromisoformat(heading['datetime'])
                    except ValueError:
                        day = None
                continue

            event = self._parse_card(node, day)
            if event:
                events.append(event)

        return events

    def _parse_card(self, card, day: Optional[date]) -> Optional[EventCreate]:
        title_elem = card.select_one('.event__title')
        title = self.clean_text(title_elem.get_text(' ')) if title_elem else ''
        if not title:
            return None

        link = card.select_one('a.event__link[href]')
        event_url = urljoin(BASE_URL, link['href']) if link else self.source_url

        time_elem = card.select_one('.event__time time[datetime]')
        start = self.parse_start(time_elem['datetime'] if time_elem else '',
                                 time_elem.get_text(' ', strip=True) if time_elem else '')
        if start is None:
            logger.warning(f"Skipping '{title}' - no start whose attribute and label agree ({event_url})")
            return None
        if day is not None and start.date() != day:
            logger.warning(f"Skipping '{title}' - starts {start} under the heading for {day} ({event_url})")
            return None

        info_elem = card.select_one('.event__info')
        info = self.clean_text(info_elem.get_text(' ')) if info_elem else ''
        info = re.sub(r'\s+,', ',', info).rstrip(',')  # "Directed by Alain Resnais , 1968"
        labels = [self.clean_text(t.get_text()) for t in card.select('.event__labels .tooltip')]

        series_elem = card.select_one('.event__series')
        series = self.clean_text(series_elem.get_text(' ')) if series_elem else ''
        if series.endswith(('...', '…')):
            series = ''  # the listing truncates long names; the detail page has it whole

        details = self._fetch_details(event_url)
        series = details.get('series') or series

        parts = [f"{info}." if info else '', details.get('description', '')]
        if series:
            parts.append(f"Part of the series {series}.")
        if 'Screening on Film' in labels:
            parts.append("Screening on film.")
        if 'Director in Person' in labels:
            parts.append("Director in person.")
        description = ' '.join(p for p in parts if p) or f"{title} at the Harvard Film Archive"

        img = card.select_one('.event__image img[src]')
        image_url = urljoin(BASE_URL, img['src']) if img else None

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            venue_name="Harvard Film Archive",
            street_address="24 Quincy Street",
            city="Cambridge",
            state="MA",
            zip_code="02138",
            category=EventCategory.FILM,
            cost="Free" if 'Free Admission' in labels else None,
            image_url=image_url,
            source_name=self.source_name,
            source_url=event_url,
        )

    @staticmethod
    def parse_start(attribute: str, label: str) -> Optional[datetime]:
        """The start from `datetime="2026-10-24 19:00:00"`, if the visible
        label ("7:00 pm") names the same clock time; otherwise None."""
        try:
            start = datetime.strptime(attribute.strip()[:16], '%Y-%m-%d %H:%M')
        except ValueError:
            return None

        clock = re.search(r'(\d{1,2}):(\d{2})\s*([ap])\.?m', label, re.IGNORECASE)
        if not clock:
            return None
        hour, minute = int(clock.group(1)), int(clock.group(2))
        if not (1 <= hour <= 12 and minute <= 59):
            return None
        hour = hour % 12 + (12 if clock.group(3).lower() == 'p' else 0)
        if (hour, minute) != (start.hour, start.minute):
            return None
        return start

    def _fetch_details(self, url: str) -> dict:
        """Full series name and first paragraph from a screening's own page.

        Optional: on any failure the listing's own text stands. Cached, as one
        page is fetched per screening.
        """
        if url in self._details:
            return self._details[url]

        details = {}
        try:
            soup = self.parse_html(self.fetch_html(url, retries=1))
            series = soup.select_one('.m-series .series__title')
            if series:
                details['series'] = self.clean_text(series.get_text(' '))
            for p in soup.select('article p'):
                text = self.clean_text(p.get_text(' '))
                if len(text) > 80:
                    details['description'] = text
                    break
        except Exception as e:
            logger.warning(f"No detail page for {url}: {e}")

        self._details[url] = details
        return details
