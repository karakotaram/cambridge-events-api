"""Earthwise Aware (EwA) — its "Urban Nature Participatory Science" Eventbrite collection.

EwA runs nature-monitoring walks in several towns. On 2026-11-16 the
collection held monthly "Introduction to Nature Monitoring in the City"
sessions at the Somerville Community Growing Center (22 Vinal Ave), plus
walks at Bellevue Pond in Medford and on Cape Cod, which base_feed drops as
outside the area. See base_eventbrite for how upcoming events are told from
past ones and how each is placed.
"""
from src.models.event import EventCategory
from src.scrapers.base_eventbrite import BaseEventbriteScraper


class EarthwiseAwareScraper(BaseEventbriteScraper):
    HOME = None
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Earthwise Aware",
                         source_url="https://www.eventbrite.com/cc/ewa-urban-nature-participatory-science-1116609/")
