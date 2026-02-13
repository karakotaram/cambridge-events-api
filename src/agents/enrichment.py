"""Agent 3: Enrichment - Improve event data quality (categories, dedup, family-friendly)"""
import json
import logging
import os
from collections import defaultdict
from datetime import datetime
from difflib import SequenceMatcher
from typing import List

from src.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

# Known family-friendly venues
FAMILY_VENUES = {
    "Cambridge Public Library", "Harvard Art Museums", "Mount Auburn Cemetery",
    "First Parish Cambridge", "Multicultural Arts Center",
}

VALID_CATEGORIES = {
    "music", "arts and culture", "food and drink", "theater",
    "lectures", "sports", "community", "other",
}


class EnrichmentAgent(BaseAgent):
    """Improve event data quality: categories, fuzzy dedup, family-friendly tagging"""

    def __init__(self):
        super().__init__("enrichment")

    def execute(self) -> dict:
        return self.enrich_from_disk()

    def enrich_from_disk(self) -> dict:
        """Load events from disk, enrich, save back"""
        events_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data", "events.json"
        )
        try:
            with open(events_path, "r") as f:
                events = json.load(f)
        except FileNotFoundError:
            return {"status": "error", "error": "No events file found"}

        result = self.enrich_events(events)

        # Save enriched events back
        with open(events_path, "w") as f:
            json.dump(result["events"], f, indent=2, default=str)

        del result["events"]  # Don't include full event list in report
        self.save_report(result, "enrichment_report.json")
        return result

    def enrich_events(self, events: list) -> dict:
        """Run all enrichment steps on an event list. Returns dict with enriched events."""
        categories_improved = self.improve_categories(events)
        family_improved = self.improve_family_friendly(events)
        dedup_removed = self.fuzzy_cross_source_dedup(events)

        return {
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "total_events": len(events),
            "categories_improved": categories_improved,
            "family_friendly_improved": family_improved,
            "fuzzy_dedup_removed": dedup_removed,
            "events": events,
        }

    def improve_categories(self, events: list) -> int:
        """Batch events with no/other category to Groq for classification"""
        uncategorized = [
            (i, e) for i, e in enumerate(events)
            if not e.get("category") or e.get("category") == "other"
        ]

        if not uncategorized:
            return 0

        if not self.groq_client:
            self.logger.info("Groq not available, skipping category improvement")
            return 0

        improved = 0
        # Process in batches of 10
        for batch_start in range(0, len(uncategorized), 10):
            batch = uncategorized[batch_start:batch_start + 10]
            event_lines = []
            for idx, (_, event) in enumerate(batch):
                title = event.get("title", "")
                venue = event.get("venue_name", "")
                desc = (event.get("description", "") or "")[:100]
                event_lines.append(f"{idx}. {title} | {venue} | {desc}")

            prompt = (
                "Categorize each event into exactly one category. "
                f"Valid categories: {', '.join(sorted(VALID_CATEGORIES))}\n\n"
                "Events:\n" + "\n".join(event_lines) + "\n\n"
                "Reply with ONLY lines like: 0: music\n1: theater\nNo other text."
            )

            response = self.llm_complete(prompt, system="You categorize events concisely.")
            if not response:
                continue

            for line in response.strip().split("\n"):
                line = line.strip()
                if ":" not in line:
                    continue
                try:
                    idx_str, category = line.split(":", 1)
                    idx = int(idx_str.strip())
                    category = category.strip().lower()
                    if category in VALID_CATEGORIES and 0 <= idx < len(batch):
                        original_idx = batch[idx][0]
                        events[original_idx]["category"] = category
                        improved += 1
                except (ValueError, IndexError):
                    continue

        self.logger.info(f"Improved categories for {improved}/{len(uncategorized)} events")
        return improved

    def improve_family_friendly(self, events: list) -> int:
        """Flag events at family venues that aren't yet marked family-friendly"""
        candidates = [
            (i, e) for i, e in enumerate(events)
            if not e.get("family_friendly")
            and e.get("venue_name") in FAMILY_VENUES
        ]

        if not candidates:
            return 0

        if not self.groq_client:
            self.logger.info("Groq not available, skipping family-friendly improvement")
            return 0

        improved = 0
        for batch_start in range(0, len(candidates), 10):
            batch = candidates[batch_start:batch_start + 10]
            event_lines = []
            for idx, (_, event) in enumerate(batch):
                title = event.get("title", "")
                desc = (event.get("description", "") or "")[:100]
                event_lines.append(f"{idx}. {title} | {desc}")

            prompt = (
                "For each event, reply YES if it's suitable for families with young children, NO otherwise.\n\n"
                "Events:\n" + "\n".join(event_lines) + "\n\n"
                "Reply with ONLY lines like: 0: YES\n1: NO\nNo other text."
            )

            response = self.llm_complete(prompt, system="You assess family-friendliness concisely.")
            if not response:
                continue

            for line in response.strip().split("\n"):
                line = line.strip()
                if ":" not in line:
                    continue
                try:
                    idx_str, answer = line.split(":", 1)
                    idx = int(idx_str.strip())
                    answer = answer.strip().upper()
                    if answer == "YES" and 0 <= idx < len(batch):
                        original_idx = batch[idx][0]
                        events[original_idx]["family_friendly"] = True
                        improved += 1
                except (ValueError, IndexError):
                    continue

        self.logger.info(f"Improved family-friendly for {improved}/{len(candidates)} events")
        return improved

    def fuzzy_cross_source_dedup(self, events: list) -> int:
        """Cross-source fuzzy dedup: 70% title similarity + same-day match"""
        if len(events) < 2:
            return 0

        # Group events by date for efficiency
        by_date = defaultdict(list)
        for i, event in enumerate(events):
            dt_str = event.get("start_datetime", "")
            if dt_str:
                date_key = str(dt_str)[:10]  # YYYY-MM-DD
                by_date[date_key].append(i)

        indices_to_remove = set()

        for date_key, indices in by_date.items():
            if len(indices) < 2:
                continue

            for a_pos in range(len(indices)):
                i = indices[a_pos]
                if i in indices_to_remove:
                    continue

                for b_pos in range(a_pos + 1, len(indices)):
                    j = indices[b_pos]
                    if j in indices_to_remove:
                        continue

                    e1, e2 = events[i], events[j]
                    # Skip same-source (already deduped by primary deduplicator)
                    if e1.get("source_name") == e2.get("source_name"):
                        continue

                    title_sim = SequenceMatcher(
                        None,
                        (e1.get("title") or "").lower(),
                        (e2.get("title") or "").lower()
                    ).ratio()

                    if title_sim >= 0.70:
                        # Keep the one with more data (longer description)
                        desc1 = len(e1.get("description") or "")
                        desc2 = len(e2.get("description") or "")
                        remove_idx = j if desc1 >= desc2 else i
                        indices_to_remove.add(remove_idx)

        if indices_to_remove:
            # Remove in reverse order to preserve indices
            for idx in sorted(indices_to_remove, reverse=True):
                events.pop(idx)
            self.logger.info(f"Fuzzy cross-source dedup removed {len(indices_to_remove)} events")

        return len(indices_to_remove)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    agent = EnrichmentAgent()
    result = agent.run()
    print(f"\nEnrichment results:")
    print(f"  Categories improved: {result.get('categories_improved', 0)}")
    print(f"  Family-friendly improved: {result.get('family_friendly_improved', 0)}")
    print(f"  Fuzzy dedup removed: {result.get('fuzzy_dedup_removed', 0)}")
