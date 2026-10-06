"""Custom scraper for Grolier Poetry Book Shop events"""
import logging
import re
from typing import List
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

# Page chrome that shares the reading blocks' markup. Matched against the whole
# heading: a substring match on "grolier" also dropped real readings such as
# "Pawn Shop of Coincidence by Andrea L. Fry (Grolier Poetry Press) book launch"
# and every series the shop co-presents — 3 of the 11 listed on 2026-10-06.
NAV_HEADINGS = {
    'upcoming readings', 'upcoming events', 'grolier poetry book shop',
    'about', 'contact', 'home',
}

TIME_PATTERN = r'\d{1,2}:\d{2}\s*(AM|PM|am|pm)'


class GrolierPoetryBookshopScraper(BaseScraper):
    """Custom scraper for Grolier Poetry Book Shop readings"""

    def __init__(self):
        super().__init__(
            source_name="Grolier Poetry Book Shop",
            source_url="https://www.grolierpoetrybookshop.org/upcoming-events",  # renamed from /upcoming-readings
            use_selenium=False
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Grolier Poetry Book Shop"""
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        events = []

        # Find all content blocks with event info
        content_blocks = soup.find_all('div', class_='sqs-html-content')

        for block in content_blocks:
            try:
                # Look for h2 (title) followed by p tags (date, time)
                h2 = block.find('h2')
                if not h2:
                    continue

                title = self.clean_text(h2.get_text())
                if not title or len(title) < 3:
                    continue

                # Skip non-event headings
                if title.lower().strip(' :') in NAV_HEADINGS:
                    continue

                # Get all p tags after the h2
                p_tags = block.find_all('p')
                if len(p_tags) < 2:
                    continue

                # Extract date and time from p tags
                date_str = None
                time_str = None

                for p in p_tags:
                    text = self.clean_text(p.get_text())
                    if not text:
                        continue

                    # Check if this looks like a date (contains month name)
                    month_pattern = r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s*\d{4}'
                    if re.search(month_pattern, text, re.IGNORECASE):
                        date_str = text
                    # Check if this looks like a time
                    elif re.search(TIME_PATTERN, text):
                        time_str = text

                if not date_str:
                    continue

                # Parse datetime. A reading listed with a date but no time is
                # an all-day listing, never a guessed hour.
                all_day = not (time_str or re.search(TIME_PATTERN, date_str))
                try:
                    datetime_str = date_str
                    if time_str:
                        datetime_str = f"{date_str} {time_str}"
                    start_datetime = date_parser.parse(datetime_str, fuzzy=True)
                except Exception as e:
                    logger.warning(f"Failed to parse datetime '{datetime_str}': {e}")
                    continue
                if all_day:
                    start_datetime = start_datetime.replace(hour=0, minute=0, second=0, microsecond=0)

                # No clock filter: past readings are EventValidator's to drop.
                # Comparing to datetime.now() here made the saved fixture lose
                # a reading every week until its test failed.

                # Build description
                description = f"Poetry reading featuring {title} at Grolier Poetry Book Shop, the oldest continuous poetry bookshop in the United States."

                # Check for "introduction by" in title
                if "introduction by" in title.lower():
                    description = f"Poetry reading: {title}. Grolier Poetry Book Shop is the oldest continuous poetry bookshop in the United States."

                # Check for book launch
                if "book launch" in title.lower():
                    description = f"Book launch event: {title}. Join us at Grolier Poetry Book Shop for this special reading and celebration."

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    all_day=all_day,
                    source_url=self.source_url,
                    source_name=self.source_name,
                    venue_name="Grolier Poetry Book Shop",
                    street_address="6 Plympton St",
                    city="Cambridge",
                    state="MA",
                    zip_code="02138",
                    category=EventCategory.ARTS_CULTURE,
                    cost="Free"
                )
                events.append(event)
                logger.info(f"Scraped event: {title}")

            except Exception as e:
                logger.warning(f"Failed to parse event block: {e}")
                continue

        # Remove duplicates based on title and date
        seen = set()
        unique_events = []
        for event in events:
            key = (event.title, event.start_datetime.date())
            if key not in seen:
                seen.add(key)
                unique_events.append(event)

        logger.info(f"Scraped {len(unique_events)} unique events from Grolier Poetry Book Shop")
        return unique_events
