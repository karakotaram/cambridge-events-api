"""
Local scraper for sources that don't work in CI.

Runs whichever sources `src/sources.py` marks `runs_in_ci=False` — venues that
block GitHub's IP ranges. The daily CI workflow preserves their events rather
than re-collecting them, so this script is how they get refreshed.

The source list is derived, not repeated. It used to be a third hand-maintained
copy alongside scrape.py and ci_monitor.py, which is how four scrapers ended up
running daily with no monitoring at all.
"""
import json
import logging
import uuid
from datetime import datetime

from src.sources import SOURCES
from src.utils.validator import EventValidator
from src.utils.deduplicator import EventDeduplicator
from src.utils.storage import sort_events
from src.models.event import Event

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Derived from the one registry — see src/sources.py
LOCAL_ONLY_SOURCES = [s.name for s in SOURCES if s.is_scraped and not s.runs_in_ci]


def main():
    """Run local-only scrapers and update events database"""
    logger.info("=" * 60)
    logger.info("Local Scraper for CI-blocked sources")
    logger.info("=" * 60)

    validator = EventValidator()
    deduplicator = EventDeduplicator()

    scrapers = [s.load() for s in SOURCES if s.is_scraped and not s.runs_in_ci]
    logger.info(f"Local-only sources: {', '.join(LOCAL_ONLY_SOURCES)}")

    all_events = []
    for scraper in scrapers:
        try:
            events = scraper.run()
            logger.info(f"Scraped {len(events)} events from {scraper.source_name}")
            all_events.extend(events)
        except Exception as e:
            logger.error(f"Scraper {scraper.source_name} failed: {e}")

    if not all_events:
        logger.warning("No events scraped from local sources")
        return

    # Validate events
    validated_events = []
    for event in all_events:
        event = validator.clean_and_enhance(event)
        is_valid, error = validator.validate_event(event)
        if is_valid:
            validated_events.append(event)
        else:
            logger.warning(f"Rejected event '{event.title}': {error}")

    logger.info(f"Events after validation: {len(validated_events)}")

    # Deduplicate
    deduplicated_events = deduplicator.deduplicate_events(validated_events)
    logger.info(f"Events after deduplication: {len(deduplicated_events)}")

    # Convert to Event objects with IDs
    new_events = []
    for event_create in deduplicated_events:
        new_events.append(Event.from_create(event_create).model_dump(mode='json'))

    # Load existing events
    try:
        with open('data/events.json', 'r') as f:
            existing_events = json.load(f)
    except FileNotFoundError:
        existing_events = []

    # Remove old events from local sources
    filtered_events = [
        e for e in existing_events
        if e.get('source_name') not in LOCAL_ONLY_SOURCES
    ]
    logger.info(f"Kept {len(filtered_events)} events from other sources")

    # Combine
    final_events = filtered_events + new_events

    # Save in deterministic order so the diff stays readable
    with open('data/events.json', 'w') as f:
        json.dump(sort_events(final_events), f, indent=2, default=str)

    logger.info("=" * 60)
    logger.info(f"LOCAL SCRAPE COMPLETE")
    logger.info(f"  New events from local sources: {len(new_events)}")
    logger.info(f"  Total events in database: {len(final_events)}")
    logger.info("=" * 60)

    # Print summary
    print(f"\n✓ Scraped {len(new_events)} events from local-only sources:")
    for source in LOCAL_ONLY_SOURCES:
        count = len([e for e in new_events if e.get('source_name') == source])
        print(f"    - {source}: {count} events")
    print(f"✓ Total events in database: {len(final_events)}")
    print(f"✓ Data saved to data/events.json")


if __name__ == "__main__":
    main()
