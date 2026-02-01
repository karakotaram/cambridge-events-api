"""Event recommendation service based on user archetypes"""
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timedelta
import pytz

from src.models.event import Event, EASTERN_TZ
from src.models.user import ArchetypeEnum
from src.services.archetypes import ARCHETYPES, get_archetype
from src.services.popularity import calculate_popularity_score


def matches_timing(event: Event, timing_preferences: List[str]) -> bool:
    """Check if event matches timing preferences"""
    event_dt = event.start_datetime
    if event_dt.tzinfo is None:
        event_dt = EASTERN_TZ.localize(event_dt)

    hour = event_dt.hour
    weekday = event_dt.weekday()  # 0=Monday, 6=Sunday
    is_weekend = weekday >= 5

    for pref in timing_preferences:
        if pref == "flexible":
            return True
        elif pref == "evening" and hour >= 17:  # 5pm+
            return True
        elif pref == "weekend" and is_weekend:
            return True
        elif pref == "weekend_daytime" and is_weekend and 9 <= hour < 17:
            return True

    return False


def matches_category(event: Event, categories: List[str]) -> Tuple[bool, float]:
    """
    Check if event matches category preferences.

    Returns:
        Tuple of (matches, score_boost)
        - matches: True if event category is in preferred list
        - score_boost: Higher for primary categories, lower for secondary
    """
    if not event.category:
        return False, 0.0

    event_cat = event.category.value if hasattr(event.category, 'value') else str(event.category)

    if event_cat in categories:
        # Position in list determines boost (first = highest priority)
        position = categories.index(event_cat)
        boost = 1.0 - (position * 0.1)  # 1.0, 0.9, 0.8, 0.7...
        return True, max(boost, 0.5)

    return False, 0.0


def passes_special_rules(event: Event, rules: Dict) -> bool:
    """Check if event passes special filtering rules"""
    if rules.get("family_friendly_only") and not event.family_friendly:
        return False

    if rules.get("free_only"):
        if not event.cost:
            return False
        cost_lower = event.cost.lower()
        if not any(x in cost_lower for x in ["free", "$0", "no cost", "no charge"]):
            return False

    if rules.get("prefer_free"):
        # Don't filter, but will boost free events in scoring
        pass

    return True


def score_event_for_archetype(
    event: Event,
    archetype_id: ArchetypeEnum,
    secondary_archetype_id: Optional[ArchetypeEnum] = None,
    click_data: Optional[Dict[str, int]] = None
) -> float:
    """
    Score an event for a specific archetype.

    Returns:
        Float score >= 0 (higher is better match)
        Returns 0 if event doesn't pass required filters
    """
    archetype = get_archetype(archetype_id)
    if not archetype:
        return 0.0

    # Check required filters first
    if not passes_special_rules(event, archetype.special_rules):
        return 0.0

    # Start with base popularity score
    click_count = click_data.get(event.id, 0) if click_data else 0
    base_score = calculate_popularity_score(event, click_count)

    # Category matching (major boost)
    cat_matches, cat_boost = matches_category(event, archetype.categories)
    if not cat_matches:
        # Event is not in preferred categories, reduce score significantly
        base_score *= 0.3
    else:
        base_score *= (1.0 + cat_boost * 0.5)  # Up to 50% boost

    # Timing matching (moderate boost)
    if matches_timing(event, archetype.timing_preferences):
        base_score *= 1.2
    else:
        base_score *= 0.8

    # Secondary archetype consideration (smaller boost)
    if secondary_archetype_id:
        secondary = get_archetype(secondary_archetype_id)
        if secondary:
            sec_cat_matches, sec_cat_boost = matches_category(event, secondary.categories)
            if sec_cat_matches:
                base_score *= (1.0 + sec_cat_boost * 0.2)  # Up to 20% boost

    # Prefer_free boost for academic/curious
    if archetype.special_rules.get("prefer_free"):
        if event.cost and any(x in event.cost.lower() for x in ["free", "$0"]):
            base_score *= 1.3

    return round(base_score, 4)


def get_recommended_events(
    events: List[Event],
    primary_archetype: ArchetypeEnum,
    secondary_archetype: Optional[ArchetypeEnum] = None,
    limit: int = 10,
    exclude_event_ids: Optional[List[str]] = None,
    click_data: Optional[Dict[str, int]] = None
) -> List[Tuple[Event, float]]:
    """
    Get recommended events for a user based on their archetypes.

    Args:
        events: List of available events
        primary_archetype: User's primary archetype
        secondary_archetype: User's secondary archetype (optional)
        limit: Maximum number of events to return
        exclude_event_ids: Event IDs to exclude (e.g., already sent)
        click_data: Dict mapping event_id to click count

    Returns:
        List of (event, score) tuples, sorted by score descending
    """
    now = datetime.now(EASTERN_TZ)
    exclude_ids = set(exclude_event_ids or [])

    scored_events = []

    for event in events:
        # Skip excluded events
        if event.id in exclude_ids:
            continue

        # Skip past events
        event_dt = event.start_datetime
        if event_dt.tzinfo is None:
            event_dt = EASTERN_TZ.localize(event_dt)
        if event_dt < now:
            continue

        # Score the event
        score = score_event_for_archetype(
            event,
            primary_archetype,
            secondary_archetype,
            click_data
        )

        if score > 0:
            scored_events.append((event, score))

    # Sort by score descending
    scored_events.sort(key=lambda x: x[1], reverse=True)

    return scored_events[:limit]


def get_weekly_digest_events(
    events: List[Event],
    primary_archetype: ArchetypeEnum,
    secondary_archetype: Optional[ArchetypeEnum] = None,
    exclude_event_ids: Optional[List[str]] = None,
    click_data: Optional[Dict[str, int]] = None
) -> List[Tuple[Event, float]]:
    """
    Get events for weekly email digest.

    Selects ~7 events spread across the upcoming week,
    balancing variety with archetype matching.

    Returns:
        List of (event, score) tuples
    """
    now = datetime.now(EASTERN_TZ)
    week_end = now + timedelta(days=8)  # Slightly more than a week

    # Filter to events in the next week
    upcoming = []
    for event in events:
        event_dt = event.start_datetime
        if event_dt.tzinfo is None:
            event_dt = EASTERN_TZ.localize(event_dt)
        if now <= event_dt <= week_end:
            upcoming.append(event)

    # Get recommendations from upcoming events
    recommended = get_recommended_events(
        upcoming,
        primary_archetype,
        secondary_archetype,
        limit=20,  # Get more than needed for variety
        exclude_event_ids=exclude_event_ids,
        click_data=click_data
    )

    # Select ~7 events with day diversity
    selected = []
    days_covered = set()

    for event, score in recommended:
        event_day = event.start_datetime.date()

        # Prefer events on different days
        if event_day not in days_covered or len(selected) < 3:
            selected.append((event, score))
            days_covered.add(event_day)

        if len(selected) >= 7:
            break

    # If we don't have enough, add more regardless of day
    if len(selected) < 5:
        for event, score in recommended:
            if (event, score) not in selected:
                selected.append((event, score))
                if len(selected) >= 7:
                    break

    return selected
