"""Scraper for Skip the Small Talk events

Skip the Small Talk is a national organisation: its public-events page carries
roughly 150 listings across Austin, Baltimore, Chicago, Denver, Detroit, London,
Los Angeles, Nashville, New York, Portland, Providence, Raleigh, San Diego,
Seattle, Toronto and Washington, alongside the Boston ones.

The page ships every listing in the DOM and hides non-matching ones client-side,
so which URL you request does not narrow what a parser sees — each item's own
`category-<city>` classes do. This filters on those.

That distinction cost the calendar its geography. The scraper used to fetch
`?category=Boston` and then also `?category=Cambridge`; "Cambridge" is not one of
the site's categories, so that second request returned the unfiltered national
list, and all 153 listings were appended. Events at Crank Arm Brewing in
Raleigh, Monument City Brewing in Baltimore and kibbitznest in Chicago were
published on a Cambridge calendar.
"""
import logging
import re
import json
from datetime import datetime
from typing import List, Optional

from src.scrapers.base_playwright_scraper import BasePlaywrightScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

# Squarespace stamps each listing with a `category-<city>` class per city it is
# offered in. These are the ones this calendar covers.
LOCAL_CATEGORY_CLASSES = ("category-boston",)


class SkipSmallTalkScraper(BasePlaywrightScraper):
    """Scraper for Skip the Small Talk - conversation events"""

    def __init__(self):
        super().__init__(
            source_name="Skip the Small Talk",
            source_url="https://www.skipthesmalltalk.com/public-events?category=Boston",
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape the Boston-area listings."""
        try:
            self.goto(self.source_url, wait_until="networkidle", timeout=60000)
            self.page.wait_for_timeout(3000)
            soup = self.get_soup()
        except Exception as e:
            logger.error(f"Error scraping Skip the Small Talk: {e}")
            return []

        events = self._parse_events(soup)
        logger.info(f"Scraped {len(events)} events from Skip the Small Talk")
        return events

    def _parse_events(self, soup) -> List[EventCreate]:
        """Parse events from page"""
        events = []
        seen_urls = set()

        # Find event containers - Squarespace summary items
        event_items = soup.find_all('div', class_=re.compile(r'summary-item'))
        if not event_items:
            event_items = soup.find_all('article')

        skipped_elsewhere = 0
        for item in event_items:
            try:
                # The page carries every city's listings; keep only the ones the
                # site itself tags for this area.
                classes = item.get('class') or []
                if not any(c in classes for c in LOCAL_CATEGORY_CLASSES):
                    skipped_elsewhere += 1
                    continue

                # Get link
                link = item.find('a', href=True)
                if not link:
                    continue

                url = link.get('href', '')
                if not url.startswith('http'):
                    url = f"http://www.skipthesmalltalk.com{url}"

                if url in seen_urls:
                    continue
                seen_urls.add(url)

                # Get title - extract just the event type and date
                text = item.get_text()

                # Extract event type (Dating, Open to Everyone, LGBTQIA+, etc.)
                event_type_match = re.search(r'(Dating|Open to Everyone|LGBTQIA\+|Ages \d+-\d+|Women|Men)', text, re.I)
                event_type = event_type_match.group(1) if event_type_match else "Social"

                # Extract venue
                venue_match = re.search(r',\s*([^,]+),\s*\(map\)', text)
                venue = venue_match.group(1).strip() if venue_match else ""

                # Build a clean title
                title = f"Skip the Small Talk - {event_type}"
                if venue:
                    title += f" at {venue}"

                if not title or len(title) < 5:
                    continue

                # Parse date from metadata
                # Format: "Thursday, January 29, 2026, 6:30 pm"
                text = item.get_text()
                start_datetime = self._parse_date_time(text)
                if start_datetime is None:
                    # Never guess a date — see docs/ARCHITECTURE.md "Layer 1".
                    logger.warning(f"Skipping '{title}' - no parseable date")
                    continue

                # Venue comes from the "..., <venue>, (map)" line when present;
                # an online session has no map link.
                venue_name = venue or ("Online" if re.search(r'\bonline\b', text, re.I)
                                       else "Skip the Small Talk Event")
                location_match = re.search(r'\b(Cambridge|Somerville|Boston)\b', text, re.I)
                city = location_match.group(1).title() if location_match else "Boston"

                # Build a detailed description
                base_desc = f"{title}. Skip the Small Talk events help strangers really get to know each other using conversation methods grounded in psychology research. Come meet interesting people and have meaningful conversations in a fun, structured environment."

                # Try to get additional details from the page
                desc_elem = item.find(class_=re.compile(r'excerpt|description|summary'))
                if desc_elem:
                    extra = self.clean_text(desc_elem.get_text())
                    if extra and len(extra) > 20 and extra.lower() not in ['featured', 'sale']:
                        base_desc += f" {extra}"

                description = base_desc

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    venue_name=venue_name,
                    city=city,
                    state="MA",
                    category=EventCategory.COMMUNITY,
                    source_name=self.source_name,
                    source_url=url,
                )
                events.append(event)

            except Exception as e:
                logger.debug(f"Error parsing event: {e}")
                continue

        if skipped_elsewhere:
            logger.info(f"Skipped {skipped_elsewhere} listings tagged for other cities")
        return events

    def _parse_date_time(self, text: str) -> Optional[datetime]:
        """Parse date/time from text like 'Thursday, January 29, 2026, 6:30 pm'.

        Returns None when nothing parses. This used to fall back to
        `datetime.now()` at 18:30, which is the same defect that put 117
        Cambridge.gov events on one day — see docs/ROADMAP.md item 0.
        """
        try:
            # Pattern: Day, Month DD, YYYY, HH:MM am/pm
            pattern = r'(\w+),\s+(\w+)\s+(\d{1,2}),\s+(\d{4}),?\s+(\d{1,2}):(\d{2})\s*(am|pm)'
            match = re.search(pattern, text, re.I)

            if match:
                _, month_str, day, year, hour, minute, ampm = match.groups()
                month = datetime.strptime(month_str, '%B').month
                hour = int(hour)
                minute = int(minute)
                if ampm.lower() == 'pm' and hour != 12:
                    hour += 12
                elif ampm.lower() == 'am' and hour == 12:
                    hour = 0

                return datetime(int(year), month, int(day), hour, minute)

            # Try simpler pattern
            pattern2 = r'(\w+)\s+(\d{1,2}),?\s+(\d{4})'
            match2 = re.search(pattern2, text)
            if match2:
                month_str, day, year = match2.groups()
                month = datetime.strptime(month_str, '%B').month
                return datetime(int(year), month, int(day), 18, 30)

        except Exception as e:
            logger.debug(f"Error parsing date: {e}")

        return None
