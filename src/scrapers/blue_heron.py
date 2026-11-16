"""Blue Heron, the early-music choir — a WordPress / The Events Calendar site.

Most concerts are at First Church in Cambridge, but the choir tours (New York,
Northampton, Newton), so it has no HOME: base_feed keeps the Cambridge and
Somerville dates and skips the rest, and skips any date with no venue.
"""
from src.models.event import EventCategory
from src.scrapers.base_tribe import BaseTribeScraper


class BlueHeronScraper(BaseTribeScraper):
    HOME = None
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Blue Heron", site="https://www.blueheron.org", page="/events/")
