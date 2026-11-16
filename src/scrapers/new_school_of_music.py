"""New School of Music, Cambridge — a community music school's concerts and recitals.

A Wix site; its calendar page renders two Wix Events list widgets (past and
upcoming) whose data is read from the page itself. See base_wix_events_page.

Most events are in the school's Concert Room at 25 Lowell Street; a few are
elsewhere in Cambridge (the NSM Choirs at First Church in Cambridge), and
each event names its own place.
"""
from src.models.event import EventCategory
from src.scrapers.base_feed import Place
from src.scrapers.base_wix_events_page import WixEventsPageScraper


class NewSchoolOfMusicScraper(WixEventsPageScraper):
    HOME = Place("New School of Music", "25 Lowell Street", "Cambridge", "02138")
    HOME_ROOMS = ("Concert Room",)
    CATEGORY = EventCategory.MUSIC
    EVENT_PATH = "/events/"

    def __init__(self):
        super().__init__(source_name="New School of Music",
                         site="https://www.newschoolofmusic.org", page="/calendar")
