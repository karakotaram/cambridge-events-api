"""Somerville Museum — a Squarespace events collection.

Walks and talks also happen around the city (Prospect Hill, Milk Row
Cemetery); the collection names those places.
"""
from src.models.event import EventCategory
from src.scrapers.base_feed import Place
from src.scrapers.base_squarespace import BaseSquarespaceScraper


class SomervilleMuseumScraper(BaseSquarespaceScraper):
    HOME = Place("Somerville Museum", "One Westwood Road", "Somerville", "02143")
    CATEGORY = EventCategory.ARTS_CULTURE

    def __init__(self):
        super().__init__(source_name="Somerville Museum",
                         site="https://www.somervillemuseum.org", collection="/calendar-events")
