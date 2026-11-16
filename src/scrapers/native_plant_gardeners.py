"""Native Plant Community Gardeners, Cambridge — a Squarespace events collection.

Seasonal: plantings, invasives pulls and Biodiversity Day, spring to autumn.

**Times.** The site's timezone is set to America/Los_Angeles, but its editors
type Eastern times, and the event pages display them as typed: on 2026-11-16
"BioDay at Danehy Park — Saturday, May 3, 3–5pm" showed 3:00 PM–5:00 PM, and
the Moth Ball ("Wednesday, July 24th from 7 to 9:30 pm") 7:00 PM–9:30 PM. Read
in Los Angeles time the epochs give exactly those, so WALL_CLOCK_IS_EASTERN.

**Where.** Most events carry no location block. The group works in two parks,
and an event body that names exactly one of them ("Enter Danehy Park through
the Sherman Street entrance") is placed there. An event that names neither,
or both, is skipped.
"""
import re
from typing import Optional

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import Place
from src.scrapers.base_squarespace import BaseSquarespaceScraper

PARKS = (
    (re.compile(r"\bDanehy Park\b", re.I), Place("Danehy Park", "99 Sherman Street", "Cambridge", "02138")),
    (re.compile(r"\bFresh Pond Reservation\b", re.I),
     Place("Fresh Pond Reservation", "250 Fresh Pond Parkway", "Cambridge", "02138")),
)


class NativePlantGardenersScraper(BaseSquarespaceScraper):
    HOME = None
    CATEGORY = EventCategory.COMMUNITY
    WALL_CLOCK_IS_EASTERN = True

    def __init__(self):
        super().__init__(source_name="Native Plant Community Gardeners",
                         site="https://www.nativeplantcommunitygardeners.org", collection="/events")

    def named_park(self, item: dict) -> Optional[Place]:
        text = " ".join((self.text(item.get("title")), self.body_text(item.get("body"))))
        found = [place for pattern, place in PARKS if pattern.search(text)]
        return found[0] if len(found) == 1 else None

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        loc = item.get("location") or {}
        if not any((loc.get("addressTitle"), loc.get("addressLine1"), loc.get("addressLine2"))):
            park = self.named_park(item)
            if park is not None:
                item = {**item, "location": {"addressTitle": park.venue, "addressLine1": park.street,
                                             "addressLine2": f"{park.city}, MA, {park.zip_code}"}}
        return super().parse_item(item)
