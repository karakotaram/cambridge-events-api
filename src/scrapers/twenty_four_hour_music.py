"""24 Hour Music — the ShoWare box office for a handful of local rooms.

tickets.24hourmusic.com sells for The Burren, Arts at the Armory, McCarthy's
(Porter Square, Cambridge) and Boston Harbor Distillery. Only McCarthy's has no
source of its own here, so that is what this contributes:

  - The Burren and Arts at the Armory publish their own calendars, which are
    registered sources, and the box office titles and times the same shows
    differently ("Honky Tonk Holiday with the Talking Hearts at Arts at the
    Armory" at 7:30 here, "24 Hour Concerts Presents: Honky Tonk Holiday …" at
    7:00 on the Armory's calendar), so deduplication would not pair them;
  - Boston Harbor Distillery is in Boston, outside the calendar's area.
"""
from src.models.event import EventCategory
from src.scrapers.base_showare import BaseShoWareScraper


class TwentyFourHourMusicScraper(BaseShoWareScraper):
    CATEGORY = EventCategory.MUSIC
    COVERED_ELSEWHERE = frozenset({"The Burren", "Arts At The Armory"})

    def __init__(self):
        super().__init__(source_name="24 Hour Music", site="https://tickets.24hourmusic.com")
