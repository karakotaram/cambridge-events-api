"""Farrington Nature Linc — a Lincoln nature centre, kept only for its local events.

Almost everything is at its own site in Lincoln, MA, which base_feed drops as
outside the calendar's area. What survives is the occasional event in town:
"Nature Trivia Night at Aeronaut" (Aeronaut Brewing, Somerville) in March 2025
and March 2026. HOME is None because the home venue is out of area — an
unplaced event is skipped, not assumed to be in Lincoln or here.

The collection's default view is a month calendar, so `?format=json` alone
returns a month's `items` and no `upcoming`. `view=list` returns the usual
`upcoming`/`past` payload base_squarespace reads.
"""
import requests

from src.models.event import EventCategory
from src.scrapers.base_squarespace import BaseSquarespaceScraper


class FarringtonScraper(BaseSquarespaceScraper):
    HOME = None
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Farrington Nature Linc", site="https://www.farringtonnaturelinc.org",
                         collection="/community-events")

    def fetch_collection(self) -> dict:
        response = requests.get(self.source_url, params={"format": "json", "view": "list"},
                                headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.json()
