"""Canal District Kendall, Cambridge — a Squarespace events collection.

The site's timezone is Canada/Eastern, which is Eastern time.
"""
from src.models.event import EventCategory
from src.scrapers.base_feed import Place
from src.scrapers.base_squarespace import BaseSquarespaceScraper


class CanalDistrictScraper(BaseSquarespaceScraper):
    HOME = Place("Canal District Kendall", "300 Athenaeum Street", "Cambridge", "02142")
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Canal District Kendall",
                         site="https://www.canaldistrictkendall.com", collection="/canal-events")
