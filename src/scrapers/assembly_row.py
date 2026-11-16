"""Assembly Row, Somerville — a WordPress / The Events Calendar site.

Events are spread across the development's tenants (The Row Hotel, LEGO
Discovery Center, Baxter Park); the API names them. Unplaced events are the
district's own, at its management address.
"""
from src.models.event import EventCategory
from src.scrapers.base_feed import Place
from src.scrapers.base_tribe import BaseTribeScraper


class AssemblyRowScraper(BaseTribeScraper):
    HOME = Place("Assembly Row", "355 Artisan Way", "Somerville", "02145")
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Assembly Row", site="https://assemblyrow.com", page="/events/")
