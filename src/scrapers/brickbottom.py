"""Brickbottom Gallery, Somerville — a Squarespace events collection.

The collection gives no location; every event is at the gallery (address from
the site's contact page).
"""
from src.models.event import EventCategory
from src.scrapers.base_feed import Place
from src.scrapers.base_squarespace import BaseSquarespaceScraper


class BrickbottomScraper(BaseSquarespaceScraper):
    HOME = Place("Brickbottom Gallery", "1 Fitchburg Street", "Somerville", "02143")
    CATEGORY = EventCategory.ARTS_CULTURE

    def __init__(self):
        super().__init__(source_name="Brickbottom Gallery", site="https://brickbottom.org", collection="/events")
