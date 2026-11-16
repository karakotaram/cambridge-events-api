"""Bow Market, Somerville — "Happenings at Bow", an eventscalendar.co embed.

The calendar lists what Bow Market's own shops and rooms host. Its location
text names a room or shop ("The Shared Palette @ Bow Market, #29", "Upstairs at
Bow", "Green Tiger & Co.", "Dearly Studio"), or nothing; all of those are the
market's address. A location naming a city is read as written — on 2026-11-16
one event was at "Union Square, Somerville MA".
"""
import re

from src.models.event import EventCategory
from src.scrapers.base_eventscalendar import BaseEventsCalendarScraper
from src.scrapers.base_feed import Place


class BowMarketScraper(BaseEventsCalendarScraper):
    HOME = Place("Bow Market", "1 Bow Market Way", "Somerville", "02143")
    # "Bow Market", "1 Bow Mkt Wy", "Upstairs at Bow", "Upstairs @ Bow" — not "23 Bow St".
    HOME_MARKER = re.compile(r"\bbow\s+(market|mkt)\b|(\bat|@)\s*bow\b", re.I)
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Bow Market", page_url="https://www.bowmarketsomerville.com/menu-calendar",
                         project="proj_oK00Zg3fdpCKj4SHJSYXq")
