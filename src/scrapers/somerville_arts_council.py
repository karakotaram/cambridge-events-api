"""Somerville Arts Council — WordPress Events Manager, read as iCalendar.

/events/ is an Events Manager listing; /events.ics serves the same events with
TZID=America/New_York times and a LOCATION line. The council's events happen
all over the city (Lantern Parade on the Community Path, PorchFest, ArtBeat in
Davis Square), so there is no HOME and an event without a location is skipped.

On 2026-11-16 the feed also carried twelve "Phone Art Project" entries — art
installations in old phone boxes, dated 2015 to 2030 with no location. They are
standing listings, not events: MAX_DAYS and the missing location each drop them.
"""
from src.models.event import EventCategory
from src.scrapers.base_ical import BaseICalScraper


class SomervilleArtsCouncilScraper(BaseICalScraper):
    HOME = None
    CATEGORY = EventCategory.ARTS_CULTURE
    MAX_DAYS = 92

    def __init__(self):
        super().__init__(source_name="Somerville Arts Council",
                         source_url="https://somervilleartscouncil.org/events/",
                         feed_url="https://somervilleartscouncil.org/events.ics")
