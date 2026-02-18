"""Preference computation, diverse event selection, and engagement updates"""
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict

from src.models.event import Event, EASTERN_TZ


# --- Timing slot helpers ---

TIMING_SLOTS = [
    "weekday_morning",
    "weekday_afternoon",
    "weekday_evening",
    "weekend_morning",
    "weekend_afternoon",
    "weekend_evening",
]


def classify_timing_slot(dt: datetime) -> str:
    """Classify a datetime into one of 6 timing slots."""
    if dt.tzinfo is None:
        dt = EASTERN_TZ.localize(dt)
    hour = dt.hour
    is_weekend = dt.weekday() >= 5

    if hour < 12:
        period = "morning"
    elif hour < 17:
        period = "afternoon"
    else:
        period = "evening"

    day_type = "weekend" if is_weekend else "weekday"
    return f"{day_type}_{period}"


# --- Preference computation from onboarding likes ---

def compute_preferences_from_likes(liked_events: List[Event]) -> dict:
    """
    Compute initial user preferences from onboarding liked events.

    Returns dict with keys matching UserPreferences columns:
        category_weights, timing_weights, venue_weights,
        price_sensitivity, prefers_family_friendly
    """
    if not liked_events:
        return {
            "category_weights": {},
            "timing_weights": {},
            "venue_weights": {},
            "price_sensitivity": 0.5,
            "prefers_family_friendly": False,
        }

    # Category weights
    cat_counts = defaultdict(int)
    for ev in liked_events:
        cat = ev.category.value if hasattr(ev.category, "value") else str(ev.category) if ev.category else "other"
        cat_counts[cat] += 1
    max_cat = max(cat_counts.values()) if cat_counts else 1
    category_weights = {cat: round(count / max_cat, 2) for cat, count in cat_counts.items()}

    # Timing weights
    timing_counts = defaultdict(int)
    for ev in liked_events:
        slot = classify_timing_slot(ev.start_datetime)
        timing_counts[slot] += 1
    max_timing = max(timing_counts.values()) if timing_counts else 1
    timing_weights = {slot: round(count / max_timing, 2) for slot, count in timing_counts.items()}

    # Venue weights
    venue_counts = defaultdict(int)
    for ev in liked_events:
        if ev.source_name:
            venue_counts[ev.source_name] += 1
    max_venue = max(venue_counts.values()) if venue_counts else 1
    venue_weights = {v: round(c / max_venue, 2) for v, c in venue_counts.items()}

    # Price sensitivity: 1.0 - (free_count / total)
    free_count = 0
    for ev in liked_events:
        if ev.cost and any(x in ev.cost.lower() for x in ["free", "$0", "no cost", "no charge"]):
            free_count += 1
    price_sensitivity = round(1.0 - (free_count / len(liked_events)), 2)

    # Family friendly: majority of likes
    ff_count = sum(1 for ev in liked_events if ev.family_friendly)
    prefers_family_friendly = ff_count > len(liked_events) / 2

    return {
        "category_weights": category_weights,
        "timing_weights": timing_weights,
        "venue_weights": venue_weights,
        "price_sensitivity": price_sensitivity,
        "prefers_family_friendly": prefers_family_friendly,
    }


# --- Engagement-based preference updates ---

def update_preferences_from_engagement(
    current_prefs: dict,
    clicked_events: List[Event],
    decay: float = 0.8,
) -> dict:
    """
    Update preference weights using exponential moving average
    from recently clicked events.

    Formula: new_weight = old_weight * decay + signal_weight * (1 - decay)
    """
    if not clicked_events:
        return current_prefs

    signal = compute_preferences_from_likes(clicked_events)

    updated = {}
    for key in ["category_weights", "timing_weights", "venue_weights"]:
        old = current_prefs.get(key, {})
        new_signal = signal.get(key, {})
        all_keys = set(list(old.keys()) + list(new_signal.keys()))
        merged = {}
        for k in all_keys:
            old_val = old.get(k, 0.0)
            new_val = new_signal.get(k, 0.0)
            merged[k] = round(old_val * decay + new_val * (1 - decay), 3)
        updated[key] = merged

    # Price sensitivity EMA
    old_price = current_prefs.get("price_sensitivity", 0.5)
    new_price = signal.get("price_sensitivity", 0.5)
    updated["price_sensitivity"] = round(old_price * decay + new_price * (1 - decay), 3)

    # Family friendly: keep current unless signal is strong
    updated["prefers_family_friendly"] = current_prefs.get("prefers_family_friendly", False)
    if len(clicked_events) >= 3:
        ff_ratio = sum(1 for ev in clicked_events if ev.family_friendly) / len(clicked_events)
        if ff_ratio > 0.6:
            updated["prefers_family_friendly"] = True
        elif ff_ratio < 0.2:
            updated["prefers_family_friendly"] = False

    return updated


# --- Diverse event selection for onboarding ---

def select_diverse_events(events: List[Event], count: int = 10) -> List[Event]:
    """
    Select diverse events for the onboarding thumbs-up screen.

    Filters to future events in next 14 days, sorts by popularity,
    then greedily selects ensuring diversity:
    - Max 2 per category
    - Max 1 per venue (normalized name)
    - Max 3 per timing slot
    - At least 3 family-friendly events
    """
    now = datetime.now(EASTERN_TZ)
    cutoff = now + timedelta(days=14)

    # Filter to upcoming events in next 14 days
    upcoming = []
    for ev in events:
        ev_dt = ev.start_datetime
        if ev_dt.tzinfo is None:
            ev_dt = EASTERN_TZ.localize(ev_dt)
        if now <= ev_dt <= cutoff:
            upcoming.append(ev)

    if not upcoming:
        return []

    # Sort by popularity score (reuse existing scoring)
    from src.services.popularity import calculate_popularity_score
    scored = [(ev, calculate_popularity_score(ev)) for ev in upcoming]
    scored.sort(key=lambda x: x[1], reverse=True)

    def _normalize_venue(name: str) -> str:
        """Normalize venue name for dedup (strip suffixes like '- Studio 2')."""
        import re
        name = name.strip().lower()
        # Remove trailing " - Studio X", " - Room X", etc.
        name = re.sub(r'\s*[-–]\s*(studio|room|hall|theater)\s*\S*$', '', name)
        # Remove trailing punctuation differences like "Co." vs ""
        name = re.sub(r'\bco\.?$', '', name).strip()
        return name

    def _pick_events(candidates, target, cat_limit=2, ff_only=False):
        """Greedy picker with venue/category/timing diversity."""
        picked = []
        cat_counts = defaultdict(int)
        venue_counts = defaultdict(int)
        timing_counts = defaultdict(int)

        for ev, score in candidates:
            if ff_only and not ev.family_friendly:
                continue
            cat = ev.category.value if hasattr(ev.category, "value") else str(ev.category) if ev.category else "other"
            venue_key = _normalize_venue(ev.venue_name or ev.source_name or "unknown")
            slot = classify_timing_slot(ev.start_datetime)

            if cat_counts[cat] >= cat_limit:
                continue
            if venue_counts[venue_key] >= 1:
                continue
            if timing_counts[slot] >= 3:
                continue

            picked.append(ev)
            cat_counts[cat] += 1
            venue_counts[venue_key] += 1
            timing_counts[slot] += 1

            if len(picked) >= target:
                break
        return picked

    # First, pick at least 3 family-friendly events
    min_ff = 3
    ff_picks = _pick_events(scored, min_ff, cat_limit=2, ff_only=True)

    # Then fill the rest from all events, excluding already-picked IDs
    picked_ids = {ev.id for ev in ff_picks}
    remaining = [(ev, s) for ev, s in scored if ev.id not in picked_ids]
    remaining_needed = count - len(ff_picks)

    # Rebuild venue/cat/timing counts from ff_picks so the second pass respects them
    general_picks = []
    cat_counts = defaultdict(int)
    venue_counts = defaultdict(int)
    timing_counts = defaultdict(int)
    for ev in ff_picks:
        cat = ev.category.value if hasattr(ev.category, "value") else str(ev.category) if ev.category else "other"
        cat_counts[cat] += 1
        venue_counts[_normalize_venue(ev.venue_name or ev.source_name or "unknown")] += 1
        timing_counts[classify_timing_slot(ev.start_datetime)] += 1

    for ev, score in remaining:
        cat = ev.category.value if hasattr(ev.category, "value") else str(ev.category) if ev.category else "other"
        venue_key = _normalize_venue(ev.venue_name or ev.source_name or "unknown")
        slot = classify_timing_slot(ev.start_datetime)

        if cat_counts[cat] >= 2:
            continue
        if venue_counts[venue_key] >= 1:
            continue
        if timing_counts[slot] >= 3:
            continue

        general_picks.append(ev)
        cat_counts[cat] += 1
        venue_counts[venue_key] += 1
        timing_counts[slot] += 1

        if len(general_picks) >= remaining_needed:
            break

    return ff_picks + general_picks


# --- Archetype-to-preference migration mapper ---

ARCHETYPE_PREFERENCE_MAP = {
    "culture_professional": {
        "category_weights": {"arts and culture": 1.0, "theater": 0.9, "lectures": 0.8, "music": 0.7},
        "timing_weights": {"weekday_evening": 1.0, "weekend_evening": 0.8, "weekend_afternoon": 0.5},
        "venue_weights": {},
        "price_sensitivity": 0.7,
        "prefers_family_friendly": False,
    },
    "family_explorer": {
        "category_weights": {"community": 1.0, "arts and culture": 0.8, "music": 0.7, "sports": 0.6},
        "timing_weights": {"weekend_morning": 1.0, "weekend_afternoon": 0.9, "weekday_morning": 0.5},
        "venue_weights": {},
        "price_sensitivity": 0.4,
        "prefers_family_friendly": True,
    },
    "nightlife_enthusiast": {
        "category_weights": {"music": 1.0, "food and drink": 0.9, "theater": 0.7},
        "timing_weights": {"weekday_evening": 1.0, "weekend_evening": 1.0},
        "venue_weights": {},
        "price_sensitivity": 0.8,
        "prefers_family_friendly": False,
    },
    "academic_curious": {
        "category_weights": {"lectures": 1.0, "arts and culture": 0.8, "community": 0.6},
        "timing_weights": {"weekday_evening": 0.8, "weekday_afternoon": 0.7, "weekend_afternoon": 0.6},
        "venue_weights": {},
        "price_sensitivity": 0.2,
        "prefers_family_friendly": False,
    },
    "social_connector": {
        "category_weights": {"community": 1.0, "food and drink": 0.8, "sports": 0.7, "music": 0.6},
        "timing_weights": {"weekend_afternoon": 0.8, "weekday_evening": 0.8, "weekend_evening": 0.7},
        "venue_weights": {},
        "price_sensitivity": 0.5,
        "prefers_family_friendly": False,
    },
    "arts_aficionado": {
        "category_weights": {"arts and culture": 1.0, "theater": 0.9, "music": 0.8},
        "timing_weights": {"weekday_evening": 1.0, "weekend_evening": 0.9, "weekend_afternoon": 0.6},
        "venue_weights": {},
        "price_sensitivity": 0.8,
        "prefers_family_friendly": False,
    },
    "active_adventurer": {
        "category_weights": {"sports": 1.0, "community": 0.7, "other": 0.5},
        "timing_weights": {"weekend_morning": 1.0, "weekend_afternoon": 0.8, "weekday_evening": 0.5},
        "venue_weights": {},
        "price_sensitivity": 0.5,
        "prefers_family_friendly": False,
    },
    "budget_explorer": {
        "category_weights": {"community": 1.0, "arts and culture": 0.8, "lectures": 0.7, "music": 0.6},
        "timing_weights": {"weekend_afternoon": 0.8, "weekday_evening": 0.7, "weekend_morning": 0.6},
        "venue_weights": {},
        "price_sensitivity": 0.0,
        "prefers_family_friendly": False,
    },
}


def get_default_preferences_for_archetype(archetype_value: str) -> dict:
    """Get default preference weights for a given archetype value (for migration)."""
    return ARCHETYPE_PREFERENCE_MAP.get(archetype_value, {
        "category_weights": {},
        "timing_weights": {},
        "venue_weights": {},
        "price_sensitivity": 0.5,
        "prefers_family_friendly": False,
    })
