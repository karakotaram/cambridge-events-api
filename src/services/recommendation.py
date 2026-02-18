"""Event recommendation service based on user preferences"""
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timedelta

from src.models.event import Event, EASTERN_TZ
from src.services.popularity import calculate_popularity_score
from src.services.preferences import classify_timing_slot


def score_event_for_user(
    event: Event,
    prefs: dict,
    click_data: Optional[Dict[str, int]] = None,
) -> float:
    """
    Score an event for a specific user based on their preferences.

    prefs should contain: category_weights, timing_weights, venue_weights,
    price_sensitivity, prefers_family_friendly

    Score formula:
        base_popularity
        x category_multiplier   (0.5 to 1.5)
        x timing_multiplier     (0.7 to 1.3)
        x venue_multiplier      (1.0 to 1.3)
        x price_multiplier      (0.8 to 1.3)
        x family_multiplier     (0.7 to 1.2)
    """
    # Base popularity
    click_count = click_data.get(event.id, 0) if click_data else 0
    base_score = calculate_popularity_score(event, click_count)

    # Category multiplier (0.3 to 2.0)
    # Strong signal: preferred categories get 2x, unknown categories get 0.5x,
    # explicitly unpreferred (present in weights at 0) get 0.3x
    cat_weights = prefs.get("category_weights", {})
    event_cat = event.category.value if hasattr(event.category, "value") else str(event.category) if event.category else "other"
    if cat_weights:
        cat_weight = cat_weights.get(event_cat, None)
        if cat_weight is None:
            # Category not in user's preferences at all — mild penalty
            category_multiplier = 0.5
        else:
            # 0.3 (weight=0, explicitly low) to 2.0 (weight=1.0, top preference)
            category_multiplier = 0.3 + cat_weight * 1.7
    else:
        category_multiplier = 1.0  # No preferences yet, neutral

    # Timing multiplier (0.6 to 1.5)
    timing_weights = prefs.get("timing_weights", {})
    event_slot = classify_timing_slot(event.start_datetime)
    timing_weight = timing_weights.get(event_slot, 0.0)
    timing_multiplier = 0.6 + timing_weight * 0.9  # 0.6 to 1.5

    # Venue multiplier (1.0 to 1.3)
    venue_weights = prefs.get("venue_weights", {})
    venue_weight = venue_weights.get(event.source_name, 0.0) if event.source_name else 0.0
    venue_multiplier = 1.0 + venue_weight * 0.3  # 1.0 to 1.3

    # Price multiplier (0.8 to 1.3)
    price_sensitivity = prefs.get("price_sensitivity", 0.5)
    is_free = False
    if event.cost:
        is_free = any(x in event.cost.lower() for x in ["free", "$0", "no cost", "no charge"])
    if is_free:
        # Free event: boost more for users who prefer free (low price_sensitivity)
        price_multiplier = 1.3 - price_sensitivity * 0.3  # 1.0 to 1.3
    else:
        # Paid event: slight penalty for users who prefer free
        price_multiplier = 0.8 + price_sensitivity * 0.4  # 0.8 to 1.2

    # Family friendly multiplier (0.7 to 1.2)
    prefers_ff = prefs.get("prefers_family_friendly", False)
    if prefers_ff:
        family_multiplier = 1.2 if event.family_friendly else 0.7
    else:
        family_multiplier = 1.0  # No penalty either way

    score = (
        base_score
        * category_multiplier
        * timing_multiplier
        * venue_multiplier
        * price_multiplier
        * family_multiplier
    )

    return round(score, 4)


def get_recommended_events(
    events: List[Event],
    prefs: dict,
    limit: int = 10,
    exclude_event_ids: Optional[List[str]] = None,
    click_data: Optional[Dict[str, int]] = None,
) -> List[Tuple[Event, float]]:
    """
    Get recommended events for a user based on their preferences.

    Returns:
        List of (event, score) tuples, sorted by score descending
    """
    now = datetime.now(EASTERN_TZ)
    exclude_ids = set(exclude_event_ids or [])

    scored_events = []

    for event in events:
        if event.id in exclude_ids:
            continue

        # Skip past events
        event_dt = event.start_datetime
        if event_dt.tzinfo is None:
            event_dt = EASTERN_TZ.localize(event_dt)
        if event_dt < now:
            continue

        score = score_event_for_user(event, prefs, click_data)

        if score > 0:
            scored_events.append((event, score))

    scored_events.sort(key=lambda x: x[1], reverse=True)

    return scored_events[:limit]


def get_weekly_digest_events(
    events: List[Event],
    prefs: dict,
    exclude_event_ids: Optional[List[str]] = None,
    click_data: Optional[Dict[str, int]] = None,
) -> List[Tuple[Event, float]]:
    """
    Get events for weekly email digest.

    Selects ~7 events spread across the upcoming week,
    balancing variety with preference matching.

    Returns:
        List of (event, score) tuples
    """
    now = datetime.now(EASTERN_TZ)
    week_end = now + timedelta(days=8)

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
        prefs,
        limit=20,
        exclude_event_ids=exclude_event_ids,
        click_data=click_data,
    )

    # Select ~7 events with day diversity and title dedup
    # (same event with multiple showtimes should only appear once)
    selected = []
    days_covered = set()
    titles_seen = set()

    for event, score in recommended:
        # Skip duplicate titles (e.g. same film at different showtimes)
        title_key = event.title.strip().lower()
        if title_key in titles_seen:
            continue

        event_day = event.start_datetime.date()

        # Prefer events on different days
        if event_day not in days_covered or len(selected) < 3:
            selected.append((event, score))
            days_covered.add(event_day)
            titles_seen.add(title_key)

        if len(selected) >= 7:
            break

    # If we don't have enough, add more regardless of day
    if len(selected) < 5:
        for event, score in recommended:
            title_key = event.title.strip().lower()
            if title_key in titles_seen:
                continue
            selected.append((event, score))
            titles_seen.add(title_key)
            if len(selected) >= 7:
                break

    return selected
