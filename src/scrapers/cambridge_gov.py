"""Custom scraper for Cambridge.gov events"""
import logging
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from typing import List, Optional
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

# Number of detail pages to enrich concurrently. The listing pages already carry
# the authoritative date, so this pass only adds images/descriptions.
DETAIL_WORKERS = 6


class CambridgeGovScraper(BaseScraper):
    """Custom scraper for City of Cambridge events"""

    def __init__(self):
        super().__init__(
            source_name="City of Cambridge",
            source_url="https://www.cambridgema.gov/citycalendar",
            use_selenium=False  # Week listings render server-side
        )

    def fetch_event_details(self, event_url: str) -> tuple:
        """Fetch description, image, and location from an event detail page

        Returns: (description, image_url, venue_name, street_address)

        Deliberately does NOT return a date. The listing page is the only source
        of truth for when an event happens, so a failure here can never move an
        event to the wrong day.
        """
        try:
            soup = self.parse_html(self.fetch_html(event_url, retries=2))
        except Exception as e:
            logger.warning(f"Could not fetch detail page {event_url}: {e}")
            return "", None, None, None

        try:
            # Extract image URL
            image_url = None
            og_image = soup.find('meta', {'property': 'og:image'})
            if og_image and og_image.get('content'):
                image_url = og_image.get('content')
            if not image_url:
                main_content = soup.find('main') or soup.find('article') or soup.find('div', class_='content')
                if main_content:
                    img = main_content.find('img')
                    if img and img.get('src'):
                        src = img.get('src')
                        if src.startswith('/'):
                            image_url = f"https://www.cambridgema.gov{src}"
                        elif src.startswith('http'):
                            image_url = src

            # Extract location - look for "Get directions" link or text after "Location" heading
            venue_name = None
            street_address = None

            # Try to find Google Maps "Get directions" link which has full address in daddr parameter
            maps_link = soup.find('a', href=lambda x: x and 'maps.google.com' in x if x else False)
            if maps_link and 'daddr=' in maps_link.get('href', ''):
                href = maps_link.get('href')
                daddr_match = re.search(r'daddr=([^&]+)', href)
                if daddr_match:
                    full_address = urllib.parse.unquote_plus(daddr_match.group(1))
                    venue_name, street_address = self._split_address(full_address)

            # If that didn't work, try finding text near "Location" heading
            if not venue_name:
                location_heading = soup.find(string=re.compile(r'Location', re.IGNORECASE))
                if location_heading:
                    parent = location_heading.find_parent()
                    if parent:
                        for sibling in parent.find_next_siblings(limit=3):
                            text = self.clean_text(sibling.get_text())
                            if text and len(text) < 200 and any(word in text.lower() for word in ['street', 'avenue', 'road', 'blvd', 'drive', 'square', 'place', 'cambridge']):
                                venue_name, street_address = self._split_address(text)
                                break

            # Extract description
            description = ""
            meta_desc = soup.find('meta', {'property': 'og:description'})
            if meta_desc and meta_desc.get('content'):
                desc_text = meta_desc.get('content')
                if len(desc_text) > 20 and 'Thursday,' not in desc_text and 'Monday,' not in desc_text:
                    description = self.clean_text(desc_text)[:2000]

            # Look for paragraphs in main content if no meta description
            if not description:
                main_content = soup.find('main') or soup.find('article')
                if main_content:
                    description_parts = []
                    for p in main_content.find_all('p'):
                        text = self.clean_text(p.get_text())
                        # Skip short text and government boilerplate
                        if len(text) > 50 and not any(skip in text.lower() for skip in [
                            'official website', '.gov website', 'secure .gov',
                            'quick links', 'contact', 'calendar'
                        ]):
                            description_parts.append(text)

                    if description_parts:
                        description = ' '.join(description_parts[:3])[:2000]

            return description, image_url, venue_name, street_address
        except Exception as e:
            logger.warning(f"Could not parse detail page {event_url}: {e}")
            return "", None, None, None

    @staticmethod
    def _split_address(text: str) -> tuple:
        """Split "Venue, 1 Main St, Cambridge, MA" into (venue, street address)"""
        parts = [p.strip() for p in text.split(',')]
        venue_name = parts[0][:200] if parts else None
        street_address = ', '.join(parts[1:])[:200] if len(parts) >= 2 else None
        return venue_name or None, street_address or None

    def parse_item_datetime(self, item, day_heading: Optional[str], week_start: datetime) -> Optional[datetime]:
        """Read an event's start time from a listing row.

        Each row carries a `<time datetime="YYYY-MM-DD HH:MM:SS">` attribute
        plus visible text like "5:00 PM". The attribute's *date* is reliable but
        its *time* is a 12-hour clock with no meridiem - a 5 PM event is written
        `05:00:00` - so the time always comes from the visible text.

        Returns None when no date can be read. Callers must skip the event
        rather than invent one.
        """
        time_elem = item.find('time')
        if time_elem is None:
            return None

        time_text = self.clean_text(time_elem.get_text())
        time_of_day = self._parse_time_of_day(time_text)

        raw = (time_elem.get('datetime') or '').strip()
        if raw:
            try:
                parsed = date_parser.parse(raw)
            except (ValueError, OverflowError):
                logger.warning(f"Unparseable time datetime attribute: {raw!r}")
            else:
                if time_of_day is None:
                    # No meridiem to correct with - keep the attribute as-is
                    logger.warning(f"No readable time text for {raw!r}, using attribute time")
                    return parsed
                return parsed.replace(
                    hour=time_of_day[0], minute=time_of_day[1], second=0, microsecond=0
                )

        # Fallback: day heading ("Monday September 14") + visible time.
        # Headings carry no year, so borrow it from the week being scraped and
        # correct for a December -> January rollover.
        if day_heading:
            heading = self.clean_text(day_heading)
            for year in (week_start.year, week_start.year + 1):
                try:
                    parsed = date_parser.parse(f"{heading} {year} {time_text}".strip(), fuzzy=True)
                except (ValueError, OverflowError):
                    continue
                # Listings only ever span the requested week
                if -1 <= (parsed.date() - week_start.date()).days <= 8:
                    return parsed

        return None

    @staticmethod
    def _parse_time_of_day(text: str) -> Optional[tuple]:
        """Parse "5:00 PM" into (17, 0). Returns None if there is no meridiem."""
        match = re.search(r'\b(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]', text or '')
        if not match:
            return None
        hour = int(match.group(1)) % 12
        minute = int(match.group(2))
        if match.group(3).upper() == 'P':
            hour += 12
        if hour > 23 or minute > 59:
            return None
        return hour, minute

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Cambridge.gov city calendar starting from today"""
        events = []
        seen = set()  # (url, start_datetime) - a recurring event has one entry per date
        skipped_no_date = 0

        # Start from today to avoid scraping old data
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        # Scrape for next 60 days
        end_date = today + timedelta(days=60)

        # Scrape week by week using Week view (more efficient than day by day)
        current_date = today
        while current_date <= end_date:
            # Format: YYYYMMDDTHHMMSS
            date_str = current_date.strftime("%Y%m%dT000000")
            # Use Week view with 200 results per page
            week_url = f"{self.source_url}?start={date_str}&view=Week&page=1&resultsperpage=200"

            try:
                soup = self.parse_html(self.fetch_html(week_url))
            except Exception as e:
                logger.error(f"Failed to fetch week of {current_date.date()}: {e}")
                current_date += timedelta(days=7)
                continue

            # The listing alternates <li class="date"> headings with the
            # <li class="eventItem"> rows that fall under them.
            day_heading = None
            for node in soup.find_all('li', class_=['date', 'eventItem']):
                classes = node.get('class') or []
                if 'date' in classes:
                    day_heading = self.clean_text(node.get_text())
                    continue

                try:
                    link = node.find('a', href=lambda x: x and '/citycalendar/view.aspx?guid=' in x if x else False)
                    if not link:
                        continue

                    title = self.clean_text(link.get_text())
                    if len(title) < 5:
                        continue

                    # Skip cancelled events
                    if 'CANCELLED' in title.upper() or 'CANCELED' in title.upper():
                        continue

                    event_url = link.get('href', '')
                    if event_url.startswith('/'):
                        event_url = f"https://www.cambridgema.gov{event_url}"
                    elif not event_url.startswith('http'):
                        event_url = self.source_url

                    start_datetime = self.parse_item_datetime(node, day_heading, current_date)
                    if start_datetime is None:
                        # Never guess. An event with an unknown date is worse
                        # than a missing one - it pollutes another day.
                        skipped_no_date += 1
                        logger.warning(f"Skipping '{title}' - no parseable date ({event_url})")
                        continue

                    key = (event_url, start_datetime)
                    if key in seen:
                        continue
                    seen.add(key)

                    location = node.find('em', class_='location')
                    venue_name, street_address = (
                        self._split_address(self.clean_text(location.get_text()))
                        if location else (None, None)
                    )

                    desc_elem = node.find('p')
                    description = self.clean_text(desc_elem.get_text()) if desc_elem else ""

                    events.append(EventCreate(
                        title=title[:200],
                        description=(description or title)[:2000],
                        start_datetime=start_datetime,
                        source_url=event_url,
                        source_name=self.source_name,
                        venue_name=venue_name,
                        street_address=street_address,
                        city="Cambridge",
                        state="MA",
                        category=self.categorize_event(title, description),
                    ))
                except Exception as e:
                    logger.warning(f"Failed to parse listing row: {e}")
                    continue

            # Move to next week
            current_date += timedelta(days=7)

        if skipped_no_date:
            logger.warning(f"Skipped {skipped_no_date} Cambridge.gov events with no parseable date")

        self.enrich_from_detail_pages(events)
        return events

    def enrich_from_detail_pages(self, events: List[EventCreate]) -> None:
        """Add images and fuller descriptions from each event's detail page.

        Best-effort: every event already has a valid date, title, and location
        from the listing, so a failed detail fetch just means a plainer card.
        """
        if not events:
            return

        # One detail page per URL - recurring events share a listing page
        by_url = {}
        for event in events:
            by_url.setdefault(event.source_url, []).append(event)

        with ThreadPoolExecutor(max_workers=DETAIL_WORKERS) as pool:
            details = pool.map(self.fetch_event_details, by_url.keys())

            for url, (description, image_url, venue_name, street_address) in zip(by_url, details):
                for event in by_url[url]:
                    if image_url:
                        event.image_url = image_url
                    if description and len(description) > len(event.description):
                        event.description = description[:2000]
                    if venue_name:
                        event.venue_name = venue_name
                        event.street_address = street_address
                    event.category = self.categorize_event(event.title, event.description)

    def categorize_event(self, title: str, description: str) -> EventCategory:
        """Categorize event based on keywords"""
        text = f"{title} {description}".lower()

        # Fitness/exercise classes and dance training should be sports (check before generic "game" keyword)
        if any(word in text for word in ['zumba', 'yoga', 'pilates', 'tai chi', 'exercise', 'workout', 'dance class', 'aerobics', 'line dancing', 'swing dance', 'swing training', 'swing class']):
            return EventCategory.SPORTS
        # Concert/music events
        elif any(word in text for word in ['concert', 'music', 'band', 'orchestra', 'jazz', 'rock', 'folk music']):
            return EventCategory.MUSIC
        # Lectures and educational (check before art/culture to catch information sessions)
        elif any(word in text for word in ['lecture', 'talk', 'presentation', 'seminar', 'workshop', 'information session', 'training program']):
            return EventCategory.LECTURES
        # Children's activities (crafts, story time, sing-alongs)
        elif any(word in text for word in ['story time', 'storytime', 'sing-along', 'craft', 'children', 'kids activity']):
            return EventCategory.ARTS_CULTURE
        # Trivia and games
        elif any(word in text for word in ['trivia', 'quiz', 'jeopardy', 'bingo']):
            return EventCategory.ARTS_CULTURE
        # Art and culture (matches after lectures to avoid false matches on "art" substring)
        elif any(word in text for word in ['art ', ' art', 'gallery', 'exhibit', 'museum', 'painting', 'sculpture']):
            return EventCategory.ARTS_CULTURE
        # Theater
        elif any(word in text for word in ['theater', 'play', 'performance', 'drama', 'acting']):
            return EventCategory.THEATER
        # Sports (check after fitness classes)
        elif any(word in text for word in ['sport', 'tournament', 'competition', 'athletics']):
            return EventCategory.SPORTS
        # Food and drink (be specific to avoid false matches)
        elif any(word in text for word in ['tasting', 'brewery', 'wine', 'beer', 'cocktail', 'dinner', 'brunch', 'lunch']):
            return EventCategory.FOOD_DRINK
        # Community events
        elif any(word in text for word in ['community', 'meeting', 'council', 'public hearing', 'town hall']):
            return EventCategory.COMMUNITY
        else:
            return EventCategory.OTHER
