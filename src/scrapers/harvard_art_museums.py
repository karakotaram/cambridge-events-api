"""Custom scraper for Harvard Art Museums events"""
import logging
import re
import json
from datetime import datetime
from typing import List, Optional
from dateutil import parser as date_parser
import html as html_module

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)


class HarvardArtMuseumsScraper(BaseScraper):
    """Custom scraper for Harvard Art Museums calendar events"""

    def __init__(self):
        super().__init__(
            source_name="Harvard Art Museums",
            source_url="https://harvardartmuseums.org/calendar",
            use_selenium=False  # Events are in embedded JSON, no JS needed
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Harvard Art Museums calendar"""
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        events = []
        seen_ids = set()

        # Find the script containing initialEvents JSON
        for script in soup.find_all('script'):
            if script.string and 'initialEvents' in script.string:
                # Extract the JSON array from: var initialEvents = [].concat([...]);
                match = re.search(r'var initialEvents = \[\]\.concat\((\[.*?\])\);', script.string, re.DOTALL)
                if match:
                    try:
                        events_json = match.group(1)
                        event_data_list = json.loads(events_json)
                        logger.info(f"Found {len(event_data_list)} events in JSON data")

                        for event_data in event_data_list:
                            try:
                                event = self._parse_event(event_data, seen_ids)
                                if event:
                                    events.append(event)
                            except Exception as e:
                                logger.warning(f"Failed to parse event: {e}")
                                continue

                    except json.JSONDecodeError as e:
                        logger.error(f"Failed to parse events JSON: {e}")
                break

        return events

    def _parse_event(self, data: dict, seen_ids: set) -> Optional[EventCreate]:
        """Parse a single event from JSON data"""
        # Skip disabled events
        if not data.get('enabled', 1):
            return None

        # Get event ID
        event_id = data.get('id')
        if event_id in seen_ids:
            return None
        seen_ids.add(event_id)

        # Extract title and clean underscores
        title = data.get('title', '').strip()
        # Remove markdown-style underscores from title (_text_ -> text)
        title = re.sub(r'_([^_]+)_', r'\1', title)
        if not title or len(title) < 3:
            return None

        # Skip closure/closed events - these are not real events
        title_lower = title.lower()
        if any(skip in title_lower for skip in ['closed', 'closure', 'museum hours', 'holiday hours']):
            return None

        # Skip Harvard-only events
        text_lower = f"{title} {data.get('description', '')} {data.get('summary', '')}".lower()
        harvard_only_phrases = [
            'open to all harvard undergraduate and graduate students',
            'harvard students only',
            'student huid required',
            'harvard id required',
            'open to harvard students',
            'for harvard students',
        ]
        if any(phrase in text_lower for phrase in harvard_only_phrases):
            return None

        # Parse date and time
        date_str = data.get('date', '')
        start_time = data.get('start_time', '')

        if not date_str:
            return None

        try:
            # Parse the ISO date (UTC)
            start_datetime = date_parser.parse(date_str)

            # If we have a local start time, use it to adjust
            if start_time:
                # Parse time like "10:00 AM"
                time_match = re.match(r'(\d{1,2}):(\d{2})\s*(AM|PM)', start_time, re.IGNORECASE)
                if time_match:
                    hour = int(time_match.group(1))
                    minute = int(time_match.group(2))
                    am_pm = time_match.group(3).upper()

                    if am_pm == 'PM' and hour != 12:
                        hour += 12
                    elif am_pm == 'AM' and hour == 12:
                        hour = 0

                    start_datetime = start_datetime.replace(hour=hour, minute=minute, second=0, microsecond=0)
        except Exception as e:
            logger.warning(f"Failed to parse date '{date_str}': {e}")
            return None

        # Skip past events - handle timezone-aware datetimes
        now = datetime.now()
        # Remove timezone info for comparison if present
        if start_datetime.tzinfo is not None:
            start_datetime = start_datetime.replace(tzinfo=None)
        if start_datetime < now:
            return None

        # Get description - prefer HTML version, clean it up
        description = ""
        html_attrs = data.get('html_attributes', {})
        if html_attrs.get('description'):
            # Strip HTML tags but preserve some structure
            desc_html = html_attrs['description']
            # Remove script/style tags entirely
            desc_html = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', desc_html, flags=re.DOTALL | re.IGNORECASE)
            # Replace block elements with newlines
            desc_html = re.sub(r'</(p|div|br|li|h[1-6])>', '\n', desc_html, flags=re.IGNORECASE)
            # Remove remaining HTML tags
            desc_html = re.sub(r'<[^>]+>', '', desc_html)
            # Unescape HTML entities
            desc_html = html_module.unescape(desc_html)
            # Remove underscores used for emphasis (markdown-style _text_)
            desc_html = re.sub(r'_([^_]+)_', r'\1', desc_html)
            # Clean up whitespace
            description = self.clean_text(desc_html)
        elif data.get('description'):
            desc = data['description']
            desc = re.sub(r'_([^_]+)_', r'\1', desc)  # Remove underscores
            description = self.clean_text(desc)
        elif data.get('summary'):
            summary = re.sub(r'<[^>]+>', ' ', data['summary'])
            summary = re.sub(r'_([^_]+)_', r'\1', summary)  # Remove underscores
            description = self.clean_text(summary)

        # Remove boilerplate text that repeats in every event
        boilerplate_phrases = [
            "The Harvard Art Museums offer free admission every day, Tuesday through Sunday.",
            "Please see the museum visit page to learn about our general policies for visiting the museums.",
            "The Harvard Art Museums are committed to accessibility for all visitors.",
            "For anyone requiring accessibility accommodations for our programs, please contact us at am_register@harvard.edu at least 48 hours in advance.",
            "Please include the name and date of the program in the subject line of your email.",
            "The Harvard Art Museums welcome individuals with disabilities to participate in our programs and activities.",
            "Space is limited, and talks are available on a first-come, first-served basis; no registration is required.",
            "Space is limited, and tours are available on a first-come, first-served basis; no registration is required.",
        ]
        for phrase in boilerplate_phrases:
            description = description.replace(phrase, "")

        # Remove boilerplate using regex patterns for variations
        boilerplate_patterns = [
            r'Please check in with museum staff at the Visitor Services desk in the Calderwood Courtyard to request to join the [^.]+\.',
            r'If you would like to request accom\W*modations or have questions about the physical access provided, please contact Visitor Services[^.]*\.?',
            r'Related programming is supported by[^.]*\.',
            r'Modern and contemporary art programs at the Harvard Art Museums are made possible[^.]*\.',
            # Remove contact info fragments
            r'edu or \d{3}-\d{3}-\d{4} in advance of your visit\.?',
        ]
        for pattern in boilerplate_patterns:
            description = re.sub(pattern, '', description)

        # Aggressively remove sponsor/fund acknowledgments from the description
        # These often get truncated and leave fragments, so remove everything from the start of such phrases
        sponsor_cutoff_patterns = [
            r'Support for [A-Za-z\s,:\-]+ is provided by.*$',
            r'Support for this exhibition.*$',
            r'[A-Za-z\s:]+ is made possible through the generosity.*$',
            r'[A-Za-z\s:]+ is made possible in part by.*$',
            r'[A-Za-z\s:]+ is made possible by.*$',
            r'; the Alexander S\..*$',
            r', made possible by the Lunder Foundation.*$',
        ]
        for pattern in sponsor_cutoff_patterns:
            description = re.sub(pattern, '', description, flags=re.IGNORECASE)

        # Remove any remaining fragments starting with fund-related punctuation
        description = re.sub(r'^[,;\s]+', '', description)  # Leading punctuation
        description = re.sub(r'[,;\s]+$', '', description)  # Trailing punctuation

        # Clean up extra whitespace after removal
        description = re.sub(r'\s+', ' ', description).strip()

        if not description or len(description) < 20:
            description = f"{title} at Harvard Art Museums"

        # Get event URL
        event_url = data.get('event_link', '')
        if not event_url:
            slug = data.get('slug', '')
            if slug:
                event_url = f"https://harvardartmuseums.org/calendar/{slug}"
            else:
                event_url = self.source_url

        # Get image URL - prefer 'list' size
        image_url = None
        image_styles = data.get('image_styles', {})
        if image_styles:
            image_url = image_styles.get('list') or image_styles.get('hero') or image_styles.get('thumb')
        elif data.get('image'):
            img_data = data['image']
            if isinstance(img_data, dict):
                for size in ['list', 'hero', 'thumb', 'original']:
                    if size in img_data and 'url' in img_data[size]:
                        image_url = img_data[size]['url']
                        break

        # Get venue info
        address = data.get('address') or "32 Quincy Street"
        city = data.get('city') or "Cambridge"
        state = data.get('state') or "MA"

        # Get event type for categorization
        event_type = data.get('event_type', '')

        # Categorize event
        category = self.categorize_event(title, description, event_type)

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start_datetime,
            source_url=event_url,
            source_name=self.source_name,
            venue_name="Harvard Art Museums",
            street_address=address,
            city=city,
            state=state,
            zip_code="02138",
            category=category,
            image_url=image_url
        )

    def categorize_event(self, title: str, description: str, event_type: str) -> EventCategory:
        """Categorize event based on title, description, and type"""
        text = f"{title} {description} {event_type}".lower()

        # Check event type first
        if 'concert' in event_type.lower() or 'music' in event_type.lower():
            return EventCategory.MUSIC
        elif 'film' in event_type.lower():
            return EventCategory.ARTS_CULTURE

        # Check keywords
        if any(word in text for word in ['concert', 'music', 'performance', 'recital', 'jazz']):
            return EventCategory.MUSIC
        elif any(word in text for word in ['film', 'screening', 'movie', 'cinema']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['lecture', 'talk', 'discussion', 'panel', 'symposium']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['tour', 'gallery', 'exhibition', 'curator']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['workshop', 'class', 'studio', 'hands-on']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['family', 'children', 'kids', 'youth']):
            return EventCategory.COMMUNITY  # Family events categorized as community
        elif any(word in text for word in ['member', 'supporter', 'friends', 'fellows']):
            return EventCategory.COMMUNITY
        else:
            return EventCategory.ARTS_CULTURE  # Default for museum events
