"""Union Square Main Streets, Somerville — a community calendar (eventscalendar.co).

Anyone can submit an event, so this is a listings site, not a venue: it carries
shows from Aeronaut, Arts at the Armory, Bow Market and others that have their
own sources. It is registered as an aggregator so those win deduplication.

There is no home venue. An event whose location names no city ("Somerville
Community Growing Center", "116 Beacon Street") is skipped — Beacon Street
runs through Somerville and Boston alike.
"""
from src.models.event import EventCategory
from src.scrapers.base_eventscalendar import BaseEventsCalendarScraper


class UnionSquareMainStreetsScraper(BaseEventsCalendarScraper):
    HOME = None
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Union Square Main Streets", page_url="https://www.unionsquaremain.org/events",
                         project="proj_gqKILQVjQUfVT6KWtO4kw")
