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
    "Somerville Theatre",  # SSL handshake failure in CI
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
from src.scrapers.sanders_theatre import SandersTheatreScraper
from src.scrapers.art import AmericanRepertoryTheaterScraper
from src.scrapers.somerville_theatre import SomervilleTheatreScraper
from src.scrapers.grolier import GrolierPoetryBookshopScraper
from src.scrapers.multicultural_arts import MulticulturalArtsCenterScraper
from src.scrapers.harvard_square import HarvardSquareScraper
from src.scrapers.rockwell import RockwellScraper
from src.scrapers.mad_monkfish import MadMonkfishScraper
from src.scrapers.mount_auburn import MountAuburnScraper
from src.scrapers.longy import LongyScraper
from src.scrapers.mit_calendar import MITCalendarScraper
from src.scrapers.mit_music_theater import MITMusicTheaterScraper
from src.scrapers.memorial_church import MemorialChurchScraper
from src.scrapers.cambridge_library import CambridgeLibraryScraper
from src.scrapers.openspace_mit import OpenSpaceMITScraper
from src.scrapers.skip_small_talk import SkipSmallTalkScraper
from src.scrapers.harvard_athletics import HarvardAthleticsScraper
from src.scrapers.museum_of_science import MuseumOfScienceScraper
from src.scrapers.the_sinclair import TheSinclairScraper
from src.scrapers.regent_theatre import RegentTheatreScraper
from src.scrapers.harvard_gsd import HarvardGSDScraper
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

        # Run enrichment agent (non-fatal)
        try:
            from src.agents.enrichment import EnrichmentAgent
            enrichment = EnrichmentAgent()
            enrich_result = enrichment.enrich_events(
                [e.model_dump(mode='json') for e in deduplicated_events]
            )
            # Rebuild EventCreate objects from enriched dicts
            enriched_dicts = enrich_result.get("events", [])
            if enriched_dicts:
                deduplicated_events = [EventCreate(**d) for d in enriched_dicts]
                logger.info(
                    f"Enrichment: {enrich_result.get('categories_improved', 0)} categories, "
                    f"{enrich_result.get('fuzzy_dedup_removed', 0)} dedup removed"
                )
        except Exception as e:
            logger.warning(f"Enrichment agent failed (non-fatal): {e}")

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
        """Save events to JSON file, preserving events from skipped sources and user-submitted events"""
        output_file = "data/events.json"

        # Create data directory if it doesn't exist
        os.makedirs("data", exist_ok=True)

        # Always preserve user-submitted events and events from skipped sources
        preserved_events = []
        user_submitted_events = []
        sources_to_preserve = set(skipped_sources or [])
        sources_to_preserve.add("User Submitted")  # Always preserve user-submitted events

        if os.path.exists(output_file):
            try:
                with open(output_file, 'r') as f:
                    existing_events = json.load(f)
                # Keep events from sources we want to preserve
                preserved_events = [
                    e for e in existing_events
                    if e.get('source_name') in sources_to_preserve
                ]
                user_submitted_count = len([e for e in preserved_events if e.get('source_name') == 'User Submitted'])
                other_preserved_count = len(preserved_events) - user_submitted_count
                logger.info(f"Preserved {len(preserved_events)} events ({user_submitted_count} user-submitted, {other_preserved_count} from skipped sources)")
            except Exception as e:
                logger.warning(f"Could not load existing events: {e}")

        # Convert new events to dict for JSON serialization
        events_dict = [event.model_dump(mode='json') for event in events]

        # Combine new events with preserved events
        all_events = events_dict + preserved_events

        with open(output_file, 'w') as f:
            json.dump(all_events, f, indent=2, default=str)

        logger.info(f"Saved {len(all_events)} events to {output_file} ({len(events)} new, {len(preserved_events)} preserved)")


def prune_orphaned_featured(featured_file: str = "data/featured.json",
                            events_file: str = "data/events.json"):
    """Remove Editor's Picks entries that no longer point at a live upcoming event.

    An entry is kept only if some event in events.json matches its title +
    source_name AND has a start_datetime on or after today (Eastern). This keeps
    featured.json from accumulating orphaned/past entries as events roll off.

    Safe by design: if events.json is missing/empty/unreadable it is a no-op, so a
    failed scrape can never wipe the featured list.
    """
    if not os.path.exists(featured_file) or not os.path.exists(events_file):
        return
    try:
        with open(featured_file) as f:
            featured = json.load(f)
        with open(events_file) as f:
            events = json.load(f)
    except Exception as e:
        logger.warning(f"prune_orphaned_featured: could not load files ({e}); skipping")
        return
    if not featured or not events:
        return

    from src.models.event import EASTERN_TZ
    today = datetime.now(EASTERN_TZ).strftime("%Y-%m-%d")

    def has_upcoming_match(entry: dict) -> bool:
        title = entry.get("title")
        source = entry.get("source_name")
        for e in events:
            if (e.get("title") == title and e.get("source_name") == source
                    and str(e.get("start_datetime", ""))[:10] >= today):
                return True
        return False

    kept = [e for e in featured if has_upcoming_match(e)]
    removed = len(featured) - len(kept)
    if removed:
        with open(featured_file, "w") as f:
            json.dump(kept, f, indent=2)
        logger.info(f"Pruned {removed} orphaned/past featured entries ({len(kept)} remain)")
    else:
        logger.info(f"Featured entries all valid ({len(kept)} upcoming)")


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
    orchestrator.register_scraper(GrolierPoetryBookshopScraper())
    orchestrator.register_scraper(MulticulturalArtsCenterScraper())
    orchestrator.register_scraper(RockwellScraper())
    orchestrator.register_scraper(MadMonkfishScraper())
    orchestrator.register_scraper(MountAuburnScraper())
    orchestrator.register_scraper(HarvardAthleticsScraper())
    orchestrator.register_scraper(HarvardGSDScraper())
    orchestrator.register_scraper(TheSinclairScraper())

    # Selenium/Playwright scrapers (run after non-Selenium to reduce browser restarts)
    orchestrator.register_scraper(CambridgeGovScraper())
    orchestrator.register_scraper(LilyPadScraper())
    orchestrator.register_scraper(MideastClubScraper())
    orchestrator.register_scraper(PorticoScraper())
    orchestrator.register_scraper(PorterSquareBooksScraper())
    orchestrator.register_scraper(ArtsAtTheArmoryScraper())
    orchestrator.register_scraper(HRDCScraper())
    orchestrator.register_scraper(CentralSquareTheaterScraper())
    orchestrator.register_scraper(SandersTheatreScraper())
    orchestrator.register_scraper(AmericanRepertoryTheaterScraper())
    if not is_ci:
        orchestrator.register_scraper(AeronautScraper())
        orchestrator.register_scraper(SomervilleTheatreScraper())

    # Playwright scrapers
    orchestrator.register_scraper(MuseumOfScienceScraper())
    orchestrator.register_scraper(RegentTheatreScraper())
    orchestrator.register_scraper(LongyScraper())
    orchestrator.register_scraper(MITCalendarScraper())
    orchestrator.register_scraper(MITMusicTheaterScraper())
    orchestrator.register_scraper(MemorialChurchScraper())
    orchestrator.register_scraper(CambridgeLibraryScraper())
    orchestrator.register_scraper(OpenSpaceMITScraper())
    orchestrator.register_scraper(SkipSmallTalkScraper())

    # Aggregator scrapers (run last so original sources take priority in deduplication)
    orchestrator.register_scraper(HarvardSquareScraper())

    # Run all scrapers
    events = orchestrator.run_all(skipped_sources=skipped_sources)

    logger.info("=" * 80)
    logger.info(f"SCRAPING COMPLETE - {len(events)} events collected")
    logger.info("=" * 80)

    # Keep Editor's Picks free of orphaned/past entries (non-fatal)
    try:
        prune_orphaned_featured()
    except Exception as e:
        logger.warning(f"Featured prune failed (non-fatal): {e}")

    # Run monitoring agents (non-fatal)
    try:
        from src.agents.health_monitor import HealthMonitorAgent
        health = HealthMonitorAgent()
        health_result = health.run()
        logger.info(f"Health monitor: {health_result.get('summary', 'done')}")
    except Exception as e:
        logger.warning(f"Health monitor agent failed (non-fatal): {e}")

    try:
        from src.agents.ci_monitor import CIMonitorAgent
        ci = CIMonitorAgent()
        ci_result = ci.run()
        logger.info(f"CI monitor: {ci_result.get('summary', 'done')}")
    except Exception as e:
        logger.warning(f"CI monitor agent failed (non-fatal): {e}")

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
