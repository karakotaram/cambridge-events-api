"""MIT Literature (LIT@MIT) — readings, seminars and talks.

A WordPress / The Events Calendar site, read through the Tribe REST API.
The section posts its own events and partners' (the Global France Seminar,
the Ancient & Medieval Studies Colloquium, the MIT/French Library author
series). Each event names a Tribe venue; base_feed keeps the Cambridge ones
and drops the rest — the French Library in Boston, the Coolidge Corner in
Brookline. An event with no venue is skipped: the section uses many rooms.

**Not read: "Tea Time".** The Lit Tea and HumaniTea are the section's weekly
socials for its students and instructors, not public events. The "Lit Tea —
Fall 2026" series was also entered as a recurrence with no end: on
2026-11-16 the feed listed it every Monday through December 2027, winter
break and summer included.

**Online.** A venue named "Virtual" is listed as Online, as base_feed does
for a title that says so.
"""
import logging
from typing import Optional

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import ONLINE, Place
from src.scrapers.base_tribe import BaseTribeScraper

logger = logging.getLogger(__name__)

SOCIAL_CATEGORIES = {"tea-time"}


class MITLiteratureScraper(BaseTribeScraper):
    HOME = None
    CATEGORY = EventCategory.LECTURES

    def __init__(self):
        super().__init__(source_name="MIT Literature", site="https://lit.mit.edu", page="/events/")

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        slugs = {c.get("slug") for c in item.get("categories") or [] if isinstance(c, dict)}
        if slugs & SOCIAL_CATEGORIES:
            return None
        return super().parse_item(item)

    def place(self, title: str, venue: Optional[str], street: Optional[str],
              city: Optional[str], zip_code: Optional[str] = None) -> Optional[Place]:
        if venue and not city and ONLINE.search(self.text(venue)):
            return Place("Online", None, None, None)
        return super().place(title, venue, street, city, zip_code)
