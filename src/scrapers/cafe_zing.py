"""Café Zing, inside Porter Square Books — a Squarespace events collection.

The collection gives no location; the address is the one the site prints.
"""
from src.models.event import EventCategory
from src.scrapers.base_feed import Place
from src.scrapers.base_squarespace import BaseSquarespaceScraper


class CafeZingScraper(BaseSquarespaceScraper):
    HOME = Place("Café Zing", "25 White Street", "Cambridge", "02140")
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Café Zing", site="https://www.cafezingcambridge.com", collection="/events")
