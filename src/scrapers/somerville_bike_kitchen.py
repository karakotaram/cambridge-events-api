"""Somerville Bike Kitchen — its public Google Calendar.

The site's "Subscribe to our Calendar" link is a public Google Calendar
(admin@somervillebikekitchen.org), read as iCalendar. The site also runs The
Events Calendar, but its Tribe API had nothing after August 2026 while the
Google Calendar carried the winter workshops; the calendar is what the co-op
maintains.

The calendar mixes public events with the co-op's own business. What is kept:
one-off events and renamed instances ("SBK Friendsgiving" in place of a
Thursday open shop). What is not:
  - the weekly Open Shop and monthly staff meeting series (RRULE; base_ical
    explains why series are not expanded);
  - closures and cancellations, which Google writes as renamed instances
    ("SBK CLOSED", "CANCELLED - No Open Shop - Happy Thanksgiving!");
  - internal business ("Sbk lease call", "SBK 2026 Retreat").

Events without a location are at the shop.

`?futureevents=true` makes Google drop past events server-side (the plain
feed carries everything since 2023), the way base_tribe asks for
start_date=now. Series still active are included and skipped as above.
"""
import re

from src.models.event import EventCategory
from src.scrapers.base_feed import Place
from src.scrapers.base_ical import BaseICalScraper

FEED = "https://calendar.google.com/calendar/ical/admin%40somervillebikekitchen.org/public/basic.ics?futureevents=true"

INTERNAL = re.compile(
    r"\b(closed|cancell?ed|no open shop|staff meeting|monthly meeting|lease call|retreat)\b", re.I)


class SomervilleBikeKitchenScraper(BaseICalScraper):
    HOME = Place("Somerville Bike Kitchen", "15 Properzi Way", "Somerville", "02143")
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Somerville Bike Kitchen",
                         source_url="https://somervillebikekitchen.org/",
                         feed_url=FEED)

    def keep(self, title, vevent) -> bool:
        return not INTERNAL.search(title)
