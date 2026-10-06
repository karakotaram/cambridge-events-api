"""Custom scraper for Boston Swing Central"""
import logging
import re
from typing import List, Optional
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

# Each post states its times under a capitalised heading: "🗓 EVENING SCHEDULE"
# for dances, "TIME + PRICE:" for the boot camp. Matched case-sensitively so
# that prose ("don't have time to commit") is not mistaken for one.
TIME_HEADING = re.compile(r'🗓|\bSCHEDULE\b|\bTIME\b')
# Colon optional: the boot camp lists "11:00am- 1:30 PM", and its blurb "11am".
START_TIME = re.compile(r'(\d{1,2}(?::\d{2})?\s*[ap]m)|\bnoon\b', re.IGNORECASE)


class BostonSwingCentralScraper(BaseScraper):
    """Custom scraper for Boston Swing Central dance events"""

    def __init__(self):
        super().__init__(
            source_name="Boston Swing Central",
            source_url="https://www.bostonswingcentral.org/",
            use_selenium=False  # Static HTML
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Boston Swing Central"""
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        events = []

        # Find date spans (e.g., "Nov, 28" or "Dec, 5"). Every one is read:
        # there used to be a [:10] cap here.
        date_spans = soup.find_all('span', string=re.compile(r'[A-Z][a-z]{2},\s+\d{1,2}'))

        for date_span in date_spans:
            try:
                # Extract date from span (e.g., "Nov, 28")
                date_text = self.clean_text(date_span.get_text())

                # The year is printed beside it (<span class="day"> 2026 </span>).
                # It used to be inferred from the clock, which mis-dates a
                # January event listed in December and makes fixtures age.
                year_span = date_span.find_next_sibling('span', class_='day')
                year_text = self.clean_text(year_span.get_text()) if year_span else ''
                if not re.fullmatch(r'\d{4}', year_text):
                    logger.warning(f"Skipping {date_text!r} - no year printed beside it")
                    continue
                try:
                    event_date = date_parser.parse(f"{date_text} {year_text}", fuzzy=False)
                except (ValueError, OverflowError):
                    logger.warning(f"Skipping {date_text!r} - unparseable date")
                    continue

                # Find the next h3 element (contains event title)
                title_elem = date_span.find_next('h3')
                if not title_elem:
                    continue

                title = self.clean_text(title_elem.get_text())

                # Skip non-event entries (closures, announcements, etc.)
                if any(word in title.lower() for word in ['closed', 'remodeling', 'holiday hours', 'announcement']):
                    continue

                # Get link if available
                event_url = self.source_url
                link = title_elem.find('a', href=True)
                if link and link.get('href'):
                    href = link.get('href')
                    if href.startswith('http'):
                        event_url = href

                # Find the content after the title
                current = title_elem.find_next_sibling()

                description_parts = []
                body_nodes = []
                venue_info = None
                cost = None

                # Gather content until we hit another date span or h3 or run out
                while current and current.name not in ['h3']:
                    # Also stop if we find another date span
                    if current.find('span', string=re.compile(r'[A-Z][a-z]{2},\s+\d{1,2}')):
                        break
                    body_nodes.append(current)
                    text = self.clean_text(current.get_text())

                    # Look for venue/address information
                    if '26 New St' in text or 'New Street' in text:
                        venue_info = text

                    # Look for admission/cost (🎟 ADMISSION INFORMATION)
                    if '🎟' in text or 'ADMISSION' in text.upper() or '$' in text:
                        cost_match = re.search(r'\$\d+(?:\.\d{2})?', text)
                        if cost_match:
                            cost = cost_match.group()

                    # Collect description parts (but not emoji headers)
                    if text and len(text) > 20 and not text.startswith('🎵') and not text.startswith('🗓') and not text.startswith('🎟'):
                        if text not in description_parts:
                            description_parts.append(text)

                    current = current.find_next_sibling()

                # Special case: Boot Camp registers through Wufoo. The form's
                # slug changes each season, so it is read from the post.
                if 'boot camp' in title.lower():
                    event_url = self._signup_link(body_nodes) or event_url

                # Build start datetime. No time, no event: the old fallback
                # published the boot camp at 00:00 instead of 11:00 AM.
                time_str = self._start_time(' '.join(n.get_text(' ') for n in body_nodes))
                if time_str is None:
                    logger.warning(f"Skipping '{title}' on {event_date:%Y-%m-%d} - no start time found")
                    continue
                try:
                    start_datetime = date_parser.parse(f"{event_date:%Y-%m-%d} {time_str}", fuzzy=False)
                except (ValueError, OverflowError):
                    logger.warning(f"Skipping '{title}' - unparseable time {time_str!r}")
                    continue

                # Build description
                description = ' '.join(description_parts[:3])[:2000] if description_parts else title

                # Venue information
                venue_name = "Boston Swing Central"
                street_address = "26 New St, Suite 3"
                city = "Cambridge"
                state = "MA"
                zip_code = "02138"

                # All Boston Swing Central events are dance/sports
                category = EventCategory.SPORTS

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    source_url=event_url,
                    source_name=self.source_name,
                    venue_name=venue_name,
                    street_address=street_address,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    category=category,
                    cost=cost
                )
                events.append(event)

            except Exception as e:
                # Log error but continue processing other events
                continue

        return events

    @staticmethod
    def _start_time(text: str) -> Optional[str]:
        """The first time after the post's time heading, e.g. "11:00am"."""
        heading = TIME_HEADING.search(text)
        if heading is None:
            return None
        match = START_TIME.search(text, heading.end())
        if match is None:
            return None
        return match.group(1) or '12:00 pm'

    @staticmethod
    def _signup_link(nodes) -> Optional[str]:
        for node in nodes:
            if not hasattr(node, 'select_one'):
                continue
            link = node.select_one('a[href*="wufoo.com/forms/"]')
            if link:
                return link['href']
        return None
