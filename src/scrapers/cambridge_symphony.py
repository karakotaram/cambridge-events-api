"""Cambridge Symphony Orchestra — a Squarespace events collection.

The orchestra plays Kresge (Cambridge) and the Armory (Somerville), and also
Arlington, Watertown and Jordan Hall in Boston. It has no HOME; base_feed keeps
only the Cambridge and Somerville concerts.
"""
from src.models.event import EventCategory
from src.scrapers.base_squarespace import BaseSquarespaceScraper


class CambridgeSymphonyScraper(BaseSquarespaceScraper):
    HOME = None
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Cambridge Symphony Orchestra",
                         site="https://www.cambridgesymphony.org", collection="/concerts")
