"""Event data validation and quality checking"""
import re
from datetime import datetime, timedelta
from typing import Optional, Tuple
import pytz

from src.models.event import EventCreate, EASTERN_TZ


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

        # Check date is not too far in past (using Eastern Time since all events are in Cambridge/Somerville)
        now_eastern = datetime.now(EASTERN_TZ).replace(tzinfo=None)  # Compare as naive datetimes
        if event.start_datetime < now_eastern - timedelta(days=30):
            return False, "Event date is too far in the past"

        # Check date is not too far in future (likely data error)
        if event.start_datetime > now_eastern + timedelta(days=730):  # 2 years
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

        # Auto-detect family-friendly events
        event.family_friendly = EventValidator.is_family_friendly(event)

        # Auto-categorize as food and drink if no category or if venue is food/drink related
        if EventValidator.is_food_and_drink_event(event):
            from src.models.event import EventCategory
            event.category = EventCategory.FOOD_DRINK

        return event

    @staticmethod
    def is_food_and_drink_event(event: EventCreate) -> bool:
        """Detect if an event should be categorized as food and drink"""
        venue_lower = (event.venue_name or "").lower()
        source_lower = (event.source_name or "").lower()

        # Exclude libraries and other non-food venues
        excluded_venues = ['library', 'branch', 'museum', 'theater', 'theatre', 'school', 'church']
        for excluded in excluded_venues:
            if excluded in venue_lower:
                return False

        # Venues that are inherently food and drink (specific names)
        food_drink_venue_names = [
            'lamplighter', 'portico brewing', 'aeronaut', 'night shift', 'idle hands',
            'lamplighter cx', 'lamplighter brewing',
        ]

        for venue_name in food_drink_venue_names:
            if venue_name in venue_lower or venue_name in source_lower:
                return True

        # Venue type keywords (must be word boundaries to avoid "Central Square Branch" matching "bar")
        import re
        food_drink_venue_types = [
            r'\bbrewing\b', r'\bbrewery\b', r'\bbrewpub\b',
            r'\btaproom\b', r'\btap room\b', r'\bbeer garden\b', r'\bbeer hall\b',
            r'\bwinery\b', r'\bwine bar\b', r'\bdistillery\b',
            r'\brestaurant\b', r'\bcafe\b', r'\bcafé\b', r'\bbistro\b', r'\bdiner\b',
            r'\bbar\b', r'\bpub\b', r'\btavern\b', r'\blounge\b',
            r'\bgrill\b', r'\beatery\b',
            r'\bcoffeehouse\b', r'\bcoffee house\b',
            r'\bbakery\b', r'\bpatisserie\b',
        ]

        for pattern in food_drink_venue_types:
            if re.search(pattern, venue_lower) or re.search(pattern, source_lower):
                return True

        # Check title and description for food/drink keywords
        text = f"{event.title} {event.description}".lower()

        food_drink_keywords = [
            # Drinks
            'beer', 'wine tasting', 'cocktail', 'cocktails', 'spirits', 'whiskey', 'bourbon',
            'happy hour', 'beer tasting',
            'craft beer', 'ipa', 'lager', 'stout',
            'cider', 'mead',
            # Food events
            'food truck', 'foodtruck', 'pop-up dinner', 'popup dinner',
            'cooking class', 'culinary',
            'farmers market', "farmer's market", 'food festival',
        ]

        for keyword in food_drink_keywords:
            if keyword in text:
                return True

        return False

    @staticmethod
    def is_family_friendly(event: EventCreate) -> bool:
        """Detect if an event is family-friendly based on keywords and time"""
        import re

        # Late night events (8pm or later) are unlikely to be family-friendly
        # Unless they explicitly say "all ages" or "family"
        if event.start_datetime and event.start_datetime.hour >= 20:
            text = f"{event.title} {event.description}".lower()
            # Only tag as family-friendly if explicitly mentioned
            explicit_family = ['all ages', 'all-ages', 'family friendly', 'family-friendly',
                              'family event', 'family program', 'family fun', 'family day']
            if not any(phrase in text for phrase in explicit_family):
                return False

        # Combine title and description for searching
        text = f"{event.title} {event.description}".lower()

        # Keywords that require word boundary matching (to avoid false positives)
        # e.g., 'teen' should not match 'nineteenth'
        boundary_keywords = [
            r'\bkids?\b',  # kid, kids
            r'\bchildren\b', r'\bchild\b',
            r'\bbab(y|ies)\b',  # baby, babies
            r'\btoddlers?\b',  # toddler, toddlers
            r'\binfants?\b',  # infant, infants
            r'\byouth\b',
            r'\bteens?\b', r'\bteenager\b',  # teen, teens, teenager (not nineteenth)
            r'\bpuppets?\b',  # puppet, puppets (not muppet)
            r'\bpajamas?\b',  # pajama, pajamas
            r'\bcaregiver\b',
            # Note: 'craft/crafts' removed - too generic (matches adult artisan markets)
        ]

        for pattern in boundary_keywords:
            if re.search(pattern, text):
                return True

        # Multi-word phrases and specific terms (less likely to have false positives)
        phrase_keywords = [
            # Program types
            'story time', 'storytime', 'story hour', 'lapsit', 'lap sit',
            'family program', 'family event', 'family fun', 'family day',
            'puppet show',
            # Age indicators
            'all ages', 'all-ages', 'family friendly', 'family-friendly',
            'ages 0', 'ages 1', 'ages 2', 'ages 3', 'ages 4', 'ages 5',
            'ages 6', 'ages 7', 'ages 8', 'ages 9', 'ages 10',
            'ages 0-', 'ages 1-', 'ages 2-', 'ages 3-', 'ages 4-', 'ages 5-',
            # Activities
            'arts and crafts',
            'sing along', 'sing-along', 'singalong',
            'read aloud', 'read-aloud',
            'playgroup', 'play group', 'playdate', 'play date',
            # Specific programs
            'pj storytime',
            'preschool', 'pre-school',
            'kindergarten',
            'young readers', 'young reader',
            'parent and child', 'parent & child',
        ]

        for keyword in phrase_keywords:
            if keyword in text:
                return True

        return False

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
