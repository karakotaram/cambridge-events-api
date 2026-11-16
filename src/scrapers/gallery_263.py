"""Gallery 263, Cambridge — a WordPress / The Events Calendar site.

Its own venue record spells the city "Cambride"; base_feed uses HOME for any
event at the gallery under its own name. Workshops titled "VIRTUAL ..." are
listed as online.
"""
from src.models.event import EventCategory
from src.scrapers.base_feed import Place
from src.scrapers.base_tribe import BaseTribeScraper


class Gallery263Scraper(BaseTribeScraper):
    HOME = Place("Gallery 263", "263 Pearl Street", "Cambridge", "02139")
    CATEGORY = EventCategory.ARTS_CULTURE

    def __init__(self):
        super().__init__(source_name="Gallery 263", site="https://gallery263.org", page="/events/")
