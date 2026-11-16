"""SomArt Space — the Somerville Arts Council's rehearsal and performance rooms.

Classes at SomArt @ the Hive (561 Windsor Pl) and shows at the Diamond Factory
(260 Elm St), both Somerville, from the same WordPress Events Manager install
as the Arts Council, read through /events.ics. The site caps the feed at its
next 50 events (about eight weeks on 2026-11-16) and ignores limit/page
parameters; a daily scrape keeps the window rolling.

Two rooms, so no HOME. "Walkthrough" entries are tours for artists booking the
space, not public events, and are dropped by category.
"""
from src.models.event import EventCategory
from src.scrapers.base_ical import BaseICalScraper


class SomArtScraper(BaseICalScraper):
    HOME = None
    CATEGORY = EventCategory.ARTS_CULTURE
    SKIP_CATEGORIES = frozenset({"walkthrough"})
    CATEGORY_MAP = {
        "music": EventCategory.MUSIC,
        "performance": EventCategory.THEATER,
        "dance": EventCategory.THEATER,
        "experimental": EventCategory.THEATER,
        "gallery exhibit": EventCategory.ARTS_CULTURE,
        "class": EventCategory.ARTS_CULTURE,
    }

    def __init__(self):
        super().__init__(source_name="SomArt Space",
                         source_url="https://somartspace.org/classes-the-hive/",
                         feed_url="https://somartspace.org/events.ics")
