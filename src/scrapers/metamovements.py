"""MetaMovements — a Latin and Afro-Cuban dance collective.

A Wix site whose homepage carries a Wix Events month calendar; its data is
read from the page itself. See base_wix_events_page.

The collective teaches and performs across Massachusetts (Cambridge Community
Center, JAE Dance Center in Roxbury, BCYF Blackstone in the South End,
Leominster). Each event names its own address, and only the Cambridge and
Somerville ones are kept; there is no single home venue.
"""
from src.models.event import EventCategory
from src.scrapers.base_wix_events_page import WixEventsPageScraper


class MetamovementsScraper(WixEventsPageScraper):
    HOME = None
    CATEGORY = EventCategory.ARTS_CULTURE
    EVENT_PATH = "/event-details/"

    def __init__(self):
        super().__init__(source_name="MetaMovements", site="https://www.metamovements.com", page="/")
