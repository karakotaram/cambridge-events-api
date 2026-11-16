"""Lizard Lounge, Cambridge — a WordPress / The Events Calendar site.

The API pads empty nights with all-day placeholders titled "No Event
Tonight-Check Our Calendar For Upcoming Events"; base_feed drops them by title.
"""
from src.models.event import EventCategory
from src.scrapers.base_feed import Place
from src.scrapers.base_tribe import BaseTribeScraper


class LizardLoungeScraper(BaseTribeScraper):
    HOME = Place("Lizard Lounge", "1667 Massachusetts Avenue", "Cambridge", "02138")
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Lizard Lounge", site="https://lizardloungeclub.com", page="/calendar/")
