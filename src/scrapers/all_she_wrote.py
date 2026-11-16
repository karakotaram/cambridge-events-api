"""All She Wrote Books, Somerville — a Wix Events page.

The shop also runs book clubs at bars in Boston and talks at the Museum of
Science (1 Science Park, which is a Boston address); those carry Boston
addresses and are dropped by base_feed's area rule.
"""
from src.models.event import EventCategory
from src.scrapers.base_feed import Place
from src.scrapers.base_wix import BaseWixEventsScraper


class AllSheWroteBooksScraper(BaseWixEventsScraper):
    HOME = Place("All She Wrote Books", "75 Washington Street", "Somerville", "02143")
    CATEGORY = EventCategory.LECTURES

    def __init__(self):
        super().__init__(source_name="All She Wrote Books", site="https://www.allshewrotebooks.com", page="/events")
