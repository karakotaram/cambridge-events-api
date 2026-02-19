"""Event recommendation service based on user preferences"""
from typing import List, Optional, Dict, Tuple
from datetime import datetime, timedelta

from src.models.event import Event, EASTERN_TZ
from src.services.popularity import calculate_popularity_score
from src.services.preferences import classify_timing_slot

# TYPE_CHECKING import to avoid circular / heavyweight imports at runtime
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from src.services.lightfm_recommender import LightFMRecommender


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


def get_lightfm_recommended_events(
    events: List[Event],
    user_uuid: str,
    recommender: "LightFMRecommender",
    prefs: dict,
    limit: int = 20,
    exclude_event_ids: Optional[List[str]] = None,
    click_data: Optional[Dict[str, int]] = None,
) -> List[Tuple[Event, float]]:
    """
    Get recommended events using LightFM hybrid collaborative filtering.

    Falls back to get_recommended_events() if LightFM returns empty scores.

    Returns:
        List of (event, blended_score) tuples, sorted by score descending
    """
    now = datetime.now(EASTERN_TZ)
    exclude_ids = set(exclude_event_ids or [])

    # Filter to upcoming events
    upcoming = []
    for event in events:
        if event.id in exclude_ids:
            continue
        event_dt = event.start_datetime
        if event_dt.tzinfo is None:
            event_dt = EASTERN_TZ.localize(event_dt)
        if event_dt >= now:
            upcoming.append(event)

    if not upcoming:
        return []

    # Get LightFM scores
    candidate_ids = [e.id for e in upcoming]
    lfm_scores = recommender.predict_scores(user_uuid, candidate_ids)

    if not lfm_scores:
        # Fallback to multiplier-based scoring
        return get_recommended_events(
            events, prefs, limit=limit,
            exclude_event_ids=list(exclude_ids),
            click_data=click_data,
        )

    # Normalize LightFM scores to [0, 1]
    score_vals = list(lfm_scores.values())
    min_s, max_s = min(score_vals), max(score_vals)
    score_range = max_s - min_s if max_s > min_s else 1.0
    norm_scores = {eid: (s - min_s) / score_range for eid, s in lfm_scores.items()}

    # Blend with popularity: 0.7 LightFM + 0.3 popularity
    scored = []
    for event in upcoming:
        lfm_norm = norm_scores.get(event.id, 0.0)
        click_count = click_data.get(event.id, 0) if click_data else 0
        pop = calculate_popularity_score(event, click_count)
        blended = 0.7 * lfm_norm + 0.3 * pop
        scored.append((event, round(blended, 4)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:limit]


def get_weekly_digest_events(
    events: List[Event],
    prefs: dict,
    exclude_event_ids: Optional[List[str]] = None,
    click_data: Optional[Dict[str, int]] = None,
    liked_event_ids: Optional[List[str]] = None,
    user_uuid: Optional[str] = None,
    recommender: Optional["LightFMRecommender"] = None,
    use_groq_reranking: bool = True,
) -> List[Tuple[Event, float]]:
    """
    Get events for weekly email digest.

    Liked events are included first (if still upcoming), then remaining
    slots are filled with preference-scored events for variety.

    Selects ~7 events spread across the upcoming week,
    balancing variety with preference matching.

    Returns:
        List of (event, score) tuples
    """
    now = datetime.now(EASTERN_TZ)
    week_end = now + timedelta(days=8)

    # Filter to events in the next week
    upcoming = []
    upcoming_map = {}
    for event in events:
        event_dt = event.start_datetime
        if event_dt.tzinfo is None:
            event_dt = EASTERN_TZ.localize(event_dt)
        if now <= event_dt <= week_end:
            upcoming.append(event)
            upcoming_map[event.id] = event

    # Step 1: Include liked events that are still upcoming (top priority)
    liked_ids = set(liked_event_ids or [])
    selected = []
    titles_seen = set()
    days_covered = set()
    included_ids = set()

    for eid in (liked_event_ids or []):
        ev = upcoming_map.get(eid)
        if not ev:
            continue
        title_key = ev.title.strip().lower()
        if title_key in titles_seen:
            continue
        score = score_event_for_user(ev, prefs, click_data)
        selected.append((ev, score))
        titles_seen.add(title_key)
        days_covered.add(ev.start_datetime.date())
        included_ids.add(ev.id)
        if len(selected) >= 7:
            break

    # Step 2: Fill remaining slots with scored events
    if len(selected) < 7:
        all_exclude = set(exclude_event_ids or []) | included_ids
        slots_needed = 7 - len(selected)

        # Try LightFM if recommender and user_uuid are provided
        if recommender is not None and user_uuid is not None:
            recommended = get_lightfm_recommended_events(
                upcoming,
                user_uuid,
                recommender,
                prefs,
                limit=20,
                exclude_event_ids=list(all_exclude),
                click_data=click_data,
            )
        else:
            recommended = get_recommended_events(
                upcoming,
                prefs,
                limit=20,
                exclude_event_ids=list(all_exclude),
                click_data=click_data,
            )

        # Step 3: Optionally re-rank with Groq LLM for diversity
        groq_reranked = None
        if use_groq_reranking and recommended and len(recommended) >= slots_needed:
            try:
                from src.services.groq_reranker import rerank_events_with_groq
                groq_reranked = rerank_events_with_groq(
                    recommended, prefs, count=slots_needed
                )
            except Exception as e:
                print(f"[Groq Reranker] Failed, using ML order: {e}")

        if groq_reranked:
            # Use Groq's ordering
            rec_map = {ev.id: (ev, score) for ev, score in recommended}
            for item in groq_reranked:
                eid = item["event_id"]
                if eid not in rec_map:
                    continue
                ev, score = rec_map[eid]
                title_key = ev.title.strip().lower()
                if title_key in titles_seen:
                    continue
                selected.append((ev, score))
                titles_seen.add(title_key)
                days_covered.add(ev.start_datetime.date())
                if len(selected) >= 7:
                    break
        else:
            # Fallback: use ML/multiplier order with day diversity
            for event, score in recommended:
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

        # If still not enough, add more regardless of day
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
