"""Kendall Center, Cambridge — public programs, mostly the summer roof garden.

A WordPress / The Events Calendar site whose timezone is set to UTC+0. Checked
2026-11-16 against the 2025 season: Community Yoga 18:00, Sofar Sounds 19:30,
Farmer Visits 10:15, Beer Garden 16:30 — local times, with `utc_start_date`
identical. Read as UTC they would be 2 PM yoga and a 3:30 PM Sofar show. The
wall clock is the venue's Eastern time, so WALL_CLOCK_IS_EASTERN is set; see
base_tribe.

Seasonal: the feed is empty outside the summer programme.
"""
from src.models.event import EventCategory
from src.scrapers.base_feed import Place
from src.scrapers.base_tribe import BaseTribeScraper


class KendallCenterScraper(BaseTribeScraper):
    HOME = Place("Kendall Center", None, "Cambridge", "02142")
    CATEGORY = EventCategory.COMMUNITY
    WALL_CLOCK_IS_EASTERN = True

    def __init__(self):
        super().__init__(source_name="Kendall Center", site="https://www.kendallcenter.com",
                         page="/events-public-programs/roof-garden/")
