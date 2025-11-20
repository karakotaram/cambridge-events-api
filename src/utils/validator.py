"""Event data validation and quality checking"""
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple
from src.models.event import EventCreate


class EventValidator:
    """Validates and quality-checks event data"""

    @staticmethod
    def validate_event(event: EventCreate) -> Tuple[bool, Optional[str]]:
        """
        Validate event data
        Returns (is_valid, error_message)
        """

        # Check required fields
        if not event.title or len(event.title.strip()) < 3:
            return False, "Title is too short"

        if not event.description or len(event.description.strip()) < 10:
            return False, "Description is too short"

        if not event.start_datetime:
            return False, "Missing start datetime"

        # Check date is not too far in past
        if event.start_datetime < datetime.now() - timedelta(days=30):
            return False, "Event date is too far in the past"

        # Check date is not too far in future (likely data error)
        if event.start_datetime > datetime.now() + timedelta(days=730):  # 2 years
            return False, "Event date is too far in the future"

        # Check for garbage data in title
        if EventValidator.is_low_quality_title(event.title):
            return False, "Title appears to be low quality"

        return True, None

    @staticmethod
    def is_low_quality_title(title: str) -> bool:
        """Check if title is likely garbage data"""
        title_lower = title.lower().strip()

        # Too short
        if len(title_lower) < 3:
            return True

        # Just numbers/dates
        if re.match(r'^[\d/\-\s:]+$', title_lower):
            return True

        # Date abbreviations like "Nov12", "Dec25"
        if re.match(r'^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\d{1,2}$', title_lower):
            return True

        # Generic single words
        generic_words = ['event', 'show', 'performance', 'image', 'home', 'calendar']
        if title_lower in generic_words:
            return True

        # Contains UI/navigation text
        ui_text = ['jump to', 'click here', 'more info', 'iframe', 'please update']
        if any(text in title_lower for text in ui_text):
            return True

        return False

    @staticmethod
    def clean_and_enhance(event: EventCreate) -> EventCreate:
        """Clean and enhance event data"""

        # Clean title
        event.title = EventValidator.clean_text(event.title)

        # Clean description
        event.description = EventValidator.clean_text(event.description)

        # Clean location fields
        if event.venue_name:
            event.venue_name = EventValidator.clean_text(event.venue_name)

        if event.street_address:
            event.street_address = EventValidator.clean_text(event.street_address)

        # Ensure city/state defaults
        if not event.city:
            event.city = "Cambridge"

        if not event.state:
            event.state = "MA"

        return event

    @staticmethod
    def clean_text(text: str) -> str:
        """Clean and normalize text"""
        if not text:
            return ""

        # Remove extra whitespace
        text = ' '.join(text.split())

        # Remove HTML entities
        text = re.sub(r'&[a-z]+;', ' ', text)

        # Remove multiple punctuation
        text = re.sub(r'([!?.]){2,}', r'\1', text)

        return text.strip()
