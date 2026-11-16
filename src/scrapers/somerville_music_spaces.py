"""Somerville Music Spaces — a Squarespace events collection."""
from src.models.event import EventCategory
from src.scrapers.base_feed import Place
from src.scrapers.base_squarespace import BaseSquarespaceScraper


class SomervilleMusicSpacesScraper(BaseSquarespaceScraper):
    HOME = Place("Somerville Music Spaces", "1060 Broadway C101B", "Somerville", "02144")
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Somerville Music Spaces",
                         site="https://www.somervillemusicspaces.com", collection="/events")
