"""Main scraping orchestrator"""
import json
import sys
import logging
import os
from datetime import datetime
from typing import List

from src.sources import SOURCES, in_run_order, skipped_in_ci, preserved_always
from src.models.event import EventCreate, Event
from src.utils.validator import EventValidator
from src.utils.deduplicator import EventDeduplicator
from src.utils.storage import sort_events, load_events as load_stored_events, diff_events
from src.quality import gate as gate_module
from src.quality.fingerprint import record as record_fingerprints
from src.quality.run_record import RunRecord, ScraperResult

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

    def run_all(self, skipped_sources: List[str] = None, *,
                run: "RunRecord" = None, force: bool = False) -> List[Event]:
        """Run all registered scrapers, then decide whether the result may ship."""
        import gc
        import time

        run = run or RunRecord.start()
        self.run = run
        all_events = []

        logger.info(f"Starting scrape of {len(self.scrapers)} sources")
        if skipped_sources:
            logger.info(f"Skipped sources (will preserve existing events): {skipped_sources}")

        for scraper in self.scrapers:
            started = time.monotonic()
            try:
                events = scraper.run()
                logger.info(f"Scraped {len(events)} events from {scraper.source_name}")
                all_events.extend(events)
                run.add_scraper(ScraperResult(
                    source=scraper.source_name, status="ok", returned=len(events),
                    duration_s=round(time.monotonic() - started, 1)))
            except Exception as e:
                logger.error(f"Scraper {scraper.source_name} failed: {str(e)}")
                run.add_scraper(ScraperResult(
                    source=scraper.source_name, status="failed", returned=0,
                    duration_s=round(time.monotonic() - started, 1), error=str(e)[:300]))
            finally:
                # Force garbage collection between scrapers to free memory
                # This is especially important for Selenium scrapers in CI
                gc.collect()

        logger.info(f"Total events scraped: {len(all_events)}")
        run.counts["scraped"] = len(all_events)

        # Validate and clean events
        validated_events = self.validate_events(all_events)
        logger.info(f"Events after validation: {len(validated_events)}")
        run.counts["validated"] = len(validated_events)

        # Deduplicate events
        deduplicated_events = self.deduplicator.deduplicate_events(validated_events)
        logger.info(f"Events after deduplication: {len(deduplicated_events)}")
        run.counts["deduplicated"] = len(deduplicated_events)

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

        # Convert to full Event objects with stable IDs
        final_events = self.finalize_events(deduplicated_events)

        # The publish set is what the gate judges and what would land on disk:
        # this run's events plus the ones we always preserve.
        publish_set = self.build_publish_set(final_events, skipped_sources)
        run.counts["final"] = len(publish_set)

        before = load_stored_events()
        run.diff = {k: (len(v) if isinstance(v, list) else v)
                    for k, v in diff_events(before, publish_set).items()}

        decision = gate_module.evaluate(publish_set, previous=before, force=force)
        run.gate = decision.to_dict()
        run.fingerprints = {k: v.to_dict() for k, v in decision.fingerprints.items()}
        logger.info(decision.report())

        if decision.blocking:
            path = gate_module.quarantine(publish_set, decision, run.run_id)
            logger.error(f"GATE BLOCKED this run - data/events.json left unchanged. "
                         f"Quarantined at {path}")
            run.finish()
            run.save()
            self.decision = decision
            return final_events

        self.write_publish_set(publish_set)
        # Only a run that shipped may define "normal". Recording a bad run is
        # exactly how a slow degradation becomes the baseline.
        record_fingerprints(decision.fingerprints, run_id=run.run_id)
        run.finish()
        run.save()
        self.decision = decision

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
                if getattr(self, "run", None) is not None:
                    self.run.rejected[error] = self.run.rejected.get(error, 0) + 1

        return validated

    def finalize_events(self, events: List[EventCreate]) -> List[Event]:
        """Convert EventCreate to Event with stable, content-derived IDs."""
        return [Event.from_create(e) for e in events]

    def build_publish_set(self, events: List[Event],
                          skipped_sources: List[str] = None) -> List[dict]:
        """This run's events plus the ones that must survive it.

        Built before writing anything, so the gate judges exactly what would
        land on disk rather than a subset of it.
        """
        # Preserve CI-blocked sources plus anything the registry says the
        # scrape pipeline does not produce (e.g. user submissions).
        sources_to_preserve = set(skipped_sources or []) | set(preserved_always())

        preserved_events = [
            e for e in load_stored_events()
            if e.get('source_name') in sources_to_preserve
        ]
        if preserved_events:
            user_submitted = len([e for e in preserved_events
                                  if e.get('source_name') == 'User Submitted'])
            logger.info(f"Preserving {len(preserved_events)} events "
                        f"({user_submitted} user-submitted, "
                        f"{len(preserved_events) - user_submitted} from skipped sources)")

        events_dict = [event.model_dump(mode='json') for event in events]
        # Deterministic order: stable ids plus a stable order make the daily git
        # diff a readable changelog instead of a full-file rewrite.
        return sort_events(events_dict + preserved_events)

    def write_publish_set(self, publish_set: List[dict]):
        """Write the approved set to data/events.json."""
        os.makedirs("data", exist_ok=True)
        with open("data/events.json", 'w') as f:
            json.dump(publish_set, f, indent=2, default=str)
        logger.info(f"Saved {len(publish_set)} events to data/events.json")


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
    skipped_sources = skipped_in_ci() if is_ci else []

    if is_ci:
        logger.info(f"Running in CI - will skip and preserve events from: {skipped_sources}")

    force = "--force" in sys.argv
    run = RunRecord.start(is_ci=is_ci)
    logger.info(f"Run {run.run_id} (gate mode: {gate_module.resolve_mode(force=force)})")

    orchestrator = ScraperOrchestrator()

    # Register everything the registry lists, cheapest transport first.
    # src/sources.py is the only place a source is described.
    for source in in_run_order(is_ci=is_ci):
        try:
            orchestrator.register_scraper(source.load())
        except Exception as e:
            logger.error(f"Could not load scraper for {source.name}: {e}")

    # Run all scrapers
    events = orchestrator.run_all(skipped_sources=skipped_sources, run=run, force=force)

    logger.info("=" * 80)
    logger.info(f"SCRAPING COMPLETE - {len(events)} events collected")
    logger.info("=" * 80)

    decision = getattr(orchestrator, "decision", None)
    if decision is not None and decision.blocking:
        # data/events.json was left untouched; readers keep yesterday's data,
        # which is a far smaller harm than shipping something wrong.
        print("\n" + decision.report())
        print(f"\n✗ Gate BLOCKED run {run.run_id} - data/events.json unchanged")
        print(f"  Quarantined:  data/quarantine/{run.run_id}/")
        print(f"  Run record:   data/runs/{run.run_id}.json")
        print("  Override with: python scrape.py --force")
        _open_gate_issue(run, decision)
        return 1

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
        logger.info("Generated HTML view at data/events.html")
    except Exception as e:
        logger.error(f"Failed to generate HTML: {str(e)}")

    # Print summary
    print(f"\n✓ Successfully scraped {len(events)} events")
    print(f"✓ Data saved to data/events.json")
    print(f"✓ HTML view generated at data/events.html")
    print(f"✓ Logs saved to logs/scraper.log")
    print(f"✓ Run record at data/runs/{run.run_id}.json")
    return 0


def _open_gate_issue(run, decision):
    """Tell someone. A gate that blocks silently is just an outage."""
    try:
        from src.agents.base_agent import BaseAgent

        class _Notifier(BaseAgent):
            def execute(self):
                return {"status": "ok"}

        body = "\n".join([
            "The nightly scrape was blocked before publishing. "
            "`data/events.json` is unchanged, so the site is serving the last good data.",
            "",
            "```",
            decision.report(),
            "```",
            "",
            f"- Run record: `data/runs/{run.run_id}.json`",
            f"- Quarantined output: `data/quarantine/{run.run_id}/`",
            "- Diagnose: `python -m src.cli doctor` and `python -m src.cli run " + run.run_id + "`",
            "- Override once the data is confirmed good: re-run with `--force`",
        ])
        _Notifier("gate").create_github_issue(
            f"Gate blocked scrape {run.run_id}", body)
    except Exception as e:
        logger.warning(f"Could not open gate issue (non-fatal): {e}")


if __name__ == "__main__":
    sys.exit(main() or 0)
