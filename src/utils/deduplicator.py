"""Duplicate event detection and merging

Two different questions live here, and they need different answers.

Within one source, a duplicate is the same occurrence listed twice: same start,
same title. Anything else is a different event. Fuzzy matching used to apply
here too, and it merged everything a venue runs back to back under similar
names - Longfellow House's 10:00 and 11:00 tours, the Dance Complex's "Tap
Level 1" at 6 and "Tap Level 2" at 7, story times at two library branches. On
2026-10-05 that silently removed 45 of 105 Longfellow tours and 33 Dance Complex
classes.

Across sources, the same show is described differently by each listing ("Global
Arts Live presents Brad Mehldau Trio" vs "Brad Mehldau Trio"), so matching has to
be fuzzy: similar titles, starts within an hour, compatible venues.

When duplicates merge, the venue's own source wins over an aggregator,
whatever order the events arrived in. That used to depend on run order alone,
so a misfiled aggregator took credit for other venues' listings.
"""
from typing import List, Optional, Set
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher

from src.models.event import EventCreate, _identity_title
from src.sources import BY_NAME, KIND_ORDER

# Starts further apart than this are never the same occurrence
WINDOW_SECONDS = 3600
TITLE_SIMILARITY = 0.85
VENUE_SIMILARITY = 0.7
# A title contained in another only counts if it says something on its own
MIN_CONTAINED_TITLE = 8


def source_rank(source_name: Optional[str]) -> int:
    """Lower wins a merge: a venue's own scraper before an aggregator.

    Unknown names (retired sources lingering in stored data) rank with the
    aggregators, so any live original source beats them.
    """
    source = BY_NAME.get(source_name or "")
    return KIND_ORDER[source.kind] if source else KIND_ORDER["aggregator"]


def _venue_key(venue: Optional[str]) -> str:
    return " ".join((venue or "").lower().split())


class EventDeduplicator:
    """Detects and merges duplicate events"""

    @staticmethod
    def normalize_datetime(dt: datetime) -> datetime:
        """Convert datetime to naive UTC for comparison"""
        if dt is None:
            return None
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    @staticmethod
    def find_duplicates(events: List[EventCreate]) -> List[List[int]]:
        """Groups of indices that describe the same occurrence.

        Only events starting within WINDOW_SECONDS of each other can match, so
        events are compared along a time-sorted sweep rather than all pairs.
        Each group is sorted by original index.
        """
        starts = [EventDeduplicator.normalize_datetime(e.start_datetime) for e in events]
        order = sorted(range(len(events)), key=lambda i: (starts[i], i))

        duplicate_groups = []
        processed: Set[int] = set()
        for pos, i in enumerate(order):
            if i in processed:
                continue
            group = [i]
            for j in order[pos + 1:]:
                if (starts[j] - starts[i]).total_seconds() > WINDOW_SECONDS:
                    break
                if j not in processed and EventDeduplicator.are_duplicates(events[i], events[j]):
                    group.append(j)
                    processed.add(j)
            if len(group) > 1:
                processed.add(i)
                duplicate_groups.append(sorted(group))
        return duplicate_groups

    @staticmethod
    def are_duplicates(event1: EventCreate, event2: EventCreate) -> bool:
        """Whether two events describe the same occurrence. See the module docstring."""
        dt1 = EventDeduplicator.normalize_datetime(event1.start_datetime)
        dt2 = EventDeduplicator.normalize_datetime(event2.start_datetime)
        title1, title2 = _identity_title(event1.title), _identity_title(event2.title)
        venue1, venue2 = _venue_key(event1.venue_name), _venue_key(event2.venue_name)

        if event1.source_name == event2.source_name:
            return (dt1 == dt2 and title1 == title2
                    and (not venue1 or not venue2 or venue1 == venue2))

        if abs((dt1 - dt2).total_seconds()) > WINDOW_SECONDS:
            return False

        venues_known = bool(venue1 and venue2)
        if venues_known and EventDeduplicator.text_similarity(venue1, venue2) < VENUE_SIMILARITY:
            return False

        if EventDeduplicator.text_similarity(title1, title2) >= TITLE_SIMILARITY:
            return True

        # "Global Arts Live presents Brad Mehldau Trio" vs "Brad Mehldau Trio".
        # Containment alone is weak evidence, so it needs the venue to agree.
        shorter, longer = sorted((title1, title2), key=len)
        return venues_known and len(shorter) >= MIN_CONTAINED_TITLE and shorter in longer

    @staticmethod
    def text_similarity(text1: str, text2: str) -> float:
        """Calculate similarity between two text strings (0-1)"""
        return SequenceMatcher(None, text1, text2).ratio()

    @staticmethod
    def merge_duplicates(events: List[EventCreate]) -> EventCreate:
        """Merge duplicates into one, based on the highest-priority source.

        The base keeps its title, time, URL, and source name, so the event is
        credited to the venue's own listing rather than an aggregator's copy.
        Gaps in the base are filled from the others.
        """
        if not events:
            raise ValueError("Cannot merge empty list")

        if len(events) == 1:
            return events[0]

        # min() keeps the first of equal rank, so arrival order breaks ties
        base = min(events, key=lambda e: source_rank(e.source_name))
        merged = base.model_copy()

        for event in events:
            if event is base:
                continue

            # Use longer description
            if len(event.description) > len(merged.description):
                merged.description = event.description

            # Use more complete location data
            if event.venue_name and not merged.venue_name:
                merged.venue_name = event.venue_name

            if event.street_address and not merged.street_address:
                merged.street_address = event.street_address

            if event.latitude and not merged.latitude:
                merged.latitude = event.latitude
                merged.longitude = event.longitude

            # Use more specific category
            if event.category and not merged.category:
                merged.category = event.category

            # Merge tags
            merged.tags = list(set(merged.tags + event.tags))

            # Use populated contact info
            if event.contact_email and not merged.contact_email:
                merged.contact_email = event.contact_email

            if event.contact_phone and not merged.contact_phone:
                merged.contact_phone = event.contact_phone

            # Prefer event-specific URLs over general source URLs
            if event.website_url and not merged.website_url:
                merged.website_url = event.website_url

            if event.image_url and not merged.image_url:
                merged.image_url = event.image_url

        return merged

    @staticmethod
    def deduplicate_events(events: List[EventCreate]) -> List[EventCreate]:
        """
        Remove duplicates from event list
        Returns deduplicated list of events
        """
        if not events:
            return []

        duplicate_groups = EventDeduplicator.find_duplicates(events)

        # Merge duplicate groups
        merged_events = []
        processed_indices = set()

        # Add merged events
        for group in duplicate_groups:
            group_events = [events[i] for i in group]
            merged = EventDeduplicator.merge_duplicates(group_events)
            merged_events.append(merged)
            processed_indices.update(group)

        # Add non-duplicate events
        for i, event in enumerate(events):
            if i not in processed_indices:
                merged_events.append(event)

        return merged_events

    @staticmethod
    def reconcile_preserved(fresh: List[dict], preserved: List[dict]) -> tuple:
        """Drop duplicates between this run's events and preserved ones.

        Preserved events (user submissions, CI-skipped sources, sources that
        failed this run) used to be appended after deduplication, so a show
        both preserved and freshly scraped by another source was listed twice.

        Which copy survives:
          - a user submission always does. sync_user_events.py re-adds any
            submission missing from the stored user events, so dropping one
            would make it flap on every run.
          - a venue's own preserved listing beats a fresh aggregator copy.
          - otherwise the fresh copy wins; it was just read from the source.

        Returns (fresh, preserved) with the losers removed.
        """
        def as_create(d: dict) -> Optional[EventCreate]:
            try:
                return EventCreate(**{k: v for k, v in d.items() if k in EventCreate.model_fields})
            except Exception:
                return None

        fresh_by_day = {}
        fresh_models = [as_create(d) for d in fresh]
        for idx, model in enumerate(fresh_models):
            if model is not None:
                day = EventDeduplicator.normalize_datetime(model.start_datetime).date()
                fresh_by_day.setdefault(day, []).append(idx)

        drop_fresh, drop_preserved = set(), set()
        for p_idx, p_dict in enumerate(preserved):
            p = as_create(p_dict)
            if p is None:
                continue
            day = EventDeduplicator.normalize_datetime(p.start_datetime).date()
            # A one-hour window can cross midnight
            candidates = [i for d in (day - timedelta(days=1), day, day + timedelta(days=1))
                          for i in fresh_by_day.get(d, ())]
            for f_idx in candidates:
                if f_idx in drop_fresh:
                    continue
                f = fresh_models[f_idx]
                if not EventDeduplicator.are_duplicates(p, f):
                    continue
                p_source = BY_NAME.get(p.source_name)
                if (p_source is not None and p_source.kind == "manual") or \
                        source_rank(p.source_name) < source_rank(f.source_name):
                    drop_fresh.add(f_idx)
                else:
                    drop_preserved.add(p_idx)
                    break

        return ([d for i, d in enumerate(fresh) if i not in drop_fresh],
                [d for i, d in enumerate(preserved) if i not in drop_preserved])
