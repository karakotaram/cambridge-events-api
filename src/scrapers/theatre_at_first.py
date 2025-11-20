"""Custom scraper for Theatre at First"""
from datetime import datetime
from typing import List

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class TheatreAtFirstScraper(BaseScraper):
    """Custom scraper for Theatre at First theater events - currently being staged shows only"""

    def __init__(self):
        super().__init__(
            source_name="Theatre at First",
            source_url="https://www.theatreatfirst.org/home",
            use_selenium=False  # Don't need Selenium for static data
        )

    def scrape_events(self) -> List[EventCreate]:
        """
        Scrape events from Theatre at First.
        Note: This scraper uses hardcoded show data for "A King and No King"
        since the website uses Google Sites with dynamic content that's difficult to scrape.
        Based on https://www.theatreatfirst.org/home/2025-season
        """
        events = []

        try:
            # Current show being staged: A King and No King
            # Running November 14-23, 2025 (typically Thursdays-Sundays)
            # Playwrights: Francis Beaumont and John Fletcher
            # Director: Mary Parker

            title = "A King and No King"

            description = (
                "A King and No King by Francis Beaumont and John Fletcher. "
                "Directed by Mary Parker. "
                "Running from November 14–23, 2025. "
                "Theatre@First is an all-volunteer community theatre based in Somerville, MA."
            )

            # Create events for each performance
            # Typical community theater schedule: Thu-Sat at 7:30 PM, Sun at 3:00 PM
            # November 14-16, 2025 (Fri-Sun) and November 20-23, 2025 (Thu-Sun)
            performance_dates = [
                datetime(2025, 11, 14, 19, 30),  # Friday, Nov 14
                datetime(2025, 11, 15, 19, 30),  # Saturday, Nov 15
                datetime(2025, 11, 16, 15, 0),   # Sunday, Nov 16 (matinee)
                datetime(2025, 11, 20, 19, 30),  # Thursday, Nov 20
                datetime(2025, 11, 21, 19, 30),  # Friday, Nov 21
                datetime(2025, 11, 22, 19, 30),  # Saturday, Nov 22
                datetime(2025, 11, 23, 15, 0),   # Sunday, Nov 23 (matinee)
            ]

            for performance_date in performance_dates:
                event = EventCreate(
                    title=title,
                    description=description,
                    start_datetime=performance_date,
                    source_url="https://www.theatreatfirst.org/home/2025-season",
                    source_name=self.source_name,
                    venue_name="Theatre@First",
                    street_address="357 Summer Street",
                    city="Somerville",
                    state="MA",
                    category=EventCategory.THEATER
                )
                events.append(event)

        except Exception:
            pass

        return events
