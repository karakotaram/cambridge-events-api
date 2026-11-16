"""First Street Market, Cambridge — a Squarespace events collection.

The collection gives no location; the address is the one the site prints. It
had no upcoming events on 2026-11-16 (the last was Oct 30); the parser is
tested against its recent past events.
"""
from src.models.event import EventCategory
from src.scrapers.base_feed import Place
from src.scrapers.base_squarespace import BaseSquarespaceScraper


class FirstStreetMarketScraper(BaseSquarespaceScraper):
    HOME = Place("First Street Market", "59 First Street", "Cambridge", "02141")
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="First Street Market", site="https://firststreetmkt.com", collection="/events")
