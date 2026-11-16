"""Remnant Brewing — a Squarespace events collection covering two rooms.

Most events are at its Satellite bar (877 Cambridge St, Cambridge), some at the
taproom (Bow Market, Somerville). Each event names its room. With two rooms
there is no single HOME, so an event naming neither is skipped — on
2026-11-16 those were the "Closed for Thanksgiving" notices, which base_feed
drops by title anyway.
"""
from src.models.event import EventCategory
from src.scrapers.base_squarespace import BaseSquarespaceScraper


class RemnantScraper(BaseSquarespaceScraper):
    HOME = None
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Remnant Brewing", site="https://www.remnantsomerville.com", collection="/live")
