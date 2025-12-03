"""Main scraping orchestrator"""
import json
import logging
import os
import uuid
from datetime import datetime
from typing import List

# Sources that don't work in CI (blocked by cloud IP detection)
# These should be run locally and their events will be preserved in CI runs
CI_SKIP_SOURCES = [
    "Harvard Book Store",
    "Boston Swing Central",
    "Aeronaut Brewing",
]

from src.scrapers.cambridge_gov import CambridgeGovScraper
from src.scrapers.lilypad import LilyPadScraper
from src.scrapers.mideast import MideastClubScraper
from src.scrapers.lamplighter import LamplighterScraper
from src.scrapers.portico import PorticoScraper
from src.scrapers.harvard import HarvardBookStoreScraper
from src.scrapers.porter import PorterSquareBooksScraper
from src.scrapers.armory import ArtsAtTheArmoryScraper
from src.scrapers.hrdc import HRDCScraper
from src.scrapers.boston_swing import BostonSwingCentralScraper
from src.scrapers.comedy_studio import ComedyStudioScraper
from src.scrapers.dance_complex import DanceComplexScraper
from src.scrapers.bostonshows import BostonShowsScraper
from src.scrapers.central_square import CentralSquareTheaterScraper
from src.scrapers.theatre_at_first import TheatreAtFirstScraper
from src.scrapers.aeronaut import AeronautScraper
from src.scrapers.first_parish import FirstParishScraper
from src.scrapers.harvard_art_museums import HarvardArtMuseumsScraper
from src.scrapers.brattle import BrattleTheaterScraper
from src.models.event import EventCreate, Event
from src.utils.validator import EventValidator
from src.utils.deduplicator import EventDeduplicator

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('logs/scraper.log'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)


class ScraperOrchestrator:
    """Orchestrates multiple scrapers and processes results"""

    def __init__(self):
        self.scrapers = []
        self.validator = EventValidator()
        self.deduplicator = EventDeduplicator()

    def register_scraper(self, scraper):
        """Register a scraper to be executed"""
        self.scrapers.append(scraper)
        logger.info(f"Registered scraper: {scraper.source_name}")

    def run_all(self, skipped_sources: List[str] = None) -> List[Event]:
        """Run all registered scrapers and process results"""
        import gc

        all_events = []

        logger.info(f"Starting scrape of {len(self.scrapers)} sources")
        if skipped_sources:
            logger.info(f"Skipped sources (will preserve existing events): {skipped_sources}")

        for scraper in self.scrapers:
            try:
                events = scraper.run()
                logger.info(f"Scraped {len(events)} events from {scraper.source_name}")
                all_events.extend(events)
            except Exception as e:
                logger.error(f"Scraper {scraper.source_name} failed: {str(e)}")
            finally:
                # Force garbage collection between scrapers to free memory
                # This is especially important for Selenium scrapers in CI
                gc.collect()

        logger.info(f"Total events scraped: {len(all_events)}")

        # Validate and clean events
        validated_events = self.validate_events(all_events)
        logger.info(f"Events after validation: {len(validated_events)}")

        # Deduplicate events
        deduplicated_events = self.deduplicator.deduplicate_events(validated_events)
        logger.info(f"Events after deduplication: {len(deduplicated_events)}")

        # Convert to full Event objects with IDs
        final_events = self.finalize_events(deduplicated_events)

        # Save to file (preserving events from skipped sources)
        self.save_events(final_events, skipped_sources)

        return final_events

    def validate_events(self, events: List[EventCreate]) -> List[EventCreate]:
        """Validate and clean events"""
        validated = []

        for event in events:
            # Clean and enhance
            event = self.validator.clean_and_enhance(event)

            # Validate
            is_valid, error = self.validator.validate_event(event)

            if is_valid:
                validated.append(event)
            else:
                logger.warning(f"Rejected event '{event.title}': {error}")

        return validated

    def finalize_events(self, events: List[EventCreate]) -> List[Event]:
        """Convert EventCreate to Event with IDs"""
        final_events = []

        for event_create in events:
            # Generate unique ID
            event_id = str(uuid.uuid4())

            # Convert to Event model
            event = Event(
                id=event_id,
                **event_create.model_dump()
            )
            final_events.append(event)

        return final_events

    def save_events(self, events: List[Event], skipped_sources: List[str] = None):
        """Save events to JSON file, preserving events from skipped sources"""
        output_file = "data/events.json"

        # Create data directory if it doesn't exist
        os.makedirs("data", exist_ok=True)

        # If we skipped some sources, preserve their events from existing file
        preserved_events = []
        if skipped_sources and os.path.exists(output_file):
            try:
                with open(output_file, 'r') as f:
                    existing_events = json.load(f)
                # Keep events from skipped sources
                preserved_events = [
                    e for e in existing_events
                    if e.get('source_name') in skipped_sources
                ]
                logger.info(f"Preserved {len(preserved_events)} events from skipped sources: {skipped_sources}")
            except Exception as e:
                logger.warning(f"Could not load existing events: {e}")

        # Convert new events to dict for JSON serialization
        events_dict = [event.model_dump(mode='json') for event in events]

        # Combine new events with preserved events
        all_events = events_dict + preserved_events

        with open(output_file, 'w') as f:
            json.dump(all_events, f, indent=2, default=str)

        logger.info(f"Saved {len(all_events)} events to {output_file} ({len(events)} new, {len(preserved_events)} preserved)")


def main():
    """Main execution function"""
    logger.info("=" * 80)
    logger.info("Cambridge-Somerville Event Scraper")
    logger.info("=" * 80)

    # Check if running in CI environment
    is_ci = os.environ.get('CI', '').lower() == 'true' or os.environ.get('GITHUB_ACTIONS', '').lower() == 'true'
    skipped_sources = CI_SKIP_SOURCES if is_ci else []

    if is_ci:
        logger.info(f"Running in CI - will skip and preserve events from: {skipped_sources}")

    orchestrator = ScraperOrchestrator()

    # Register scrapers - non-Selenium scrapers first to reduce memory pressure
    # Non-Selenium scrapers (use requests)
    orchestrator.register_scraper(LamplighterScraper())
    if not is_ci:
        orchestrator.register_scraper(HarvardBookStoreScraper())
        orchestrator.register_scraper(BostonSwingCentralScraper())
    orchestrator.register_scraper(ComedyStudioScraper())
    orchestrator.register_scraper(DanceComplexScraper())
    orchestrator.register_scraper(BostonShowsScraper())
    orchestrator.register_scraper(TheatreAtFirstScraper())
    orchestrator.register_scraper(FirstParishScraper())
    orchestrator.register_scraper(HarvardArtMuseumsScraper())
    orchestrator.register_scraper(BrattleTheaterScraper())

    # Selenium scrapers (run after non-Selenium to reduce Chrome restarts)
    orchestrator.register_scraper(CambridgeGovScraper())
    orchestrator.register_scraper(LilyPadScraper())
    orchestrator.register_scraper(MideastClubScraper())
    orchestrator.register_scraper(PorticoScraper())
    orchestrator.register_scraper(PorterSquareBooksScraper())
    orchestrator.register_scraper(ArtsAtTheArmoryScraper())
    orchestrator.register_scraper(HRDCScraper())
    orchestrator.register_scraper(CentralSquareTheaterScraper())
    if not is_ci:
        orchestrator.register_scraper(AeronautScraper())

    # Run all scrapers
    events = orchestrator.run_all(skipped_sources=skipped_sources)

    logger.info("=" * 80)
    logger.info(f"SCRAPING COMPLETE - {len(events)} events collected")
    logger.info("=" * 80)

    # Generate HTML view
    try:
        from generate_html import generate_events_html
        generate_events_html()
        logger.info("Generated HTML view at events.html")
    except Exception as e:
        logger.error(f"Failed to generate HTML: {str(e)}")

    # Print summary
    print(f"\n✓ Successfully scraped {len(events)} events")
    print(f"✓ Data saved to data/events.json")
    print(f"✓ HTML view generated at events.html")
    print(f"✓ Logs saved to logs/scraper.log")


if __name__ == "__main__":
    main()
