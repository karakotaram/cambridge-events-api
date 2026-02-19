"""Event scoring service for ranking events by relevance and popularity"""
from math import log10
from datetime import datetime
from typing import Dict, Optional


# Source reputation weights (based on historical event quality)
SOURCE_WEIGHTS = {
    'Harvard Art Museums': 1.3,
    'The Sinclair': 1.2,
    'Brattle Theatre': 1.2,
    'Cambridge Public Library': 1.1,
    'Somerville Theatre': 1.2,
    'Club Passim': 1.2,
    'Harvard Film Archive': 1.15,
    'MIT Museum': 1.15,
    'Sanders Theatre': 1.2,
    'Museum of Science': 1.15,
    'Lizard Lounge': 1.1,
    'The Middle East': 1.1,
    'Sonia': 1.1,
    'The Rockwell': 1.1,
    'Arts at the Armory': 1.1,
    # All others default to 1.0
}

# Category popularity weights
CATEGORY_WEIGHTS = {
    'music': 1.2,
    'theater': 1.1,
    'arts and culture': 1.1,
    'food and drink': 1.0,
    'lectures': 0.9,
    'community': 0.9,
    'sports': 1.0,
    'other': 0.8,
}

# Interaction weights for scoring
INTERACTION_WEIGHTS = {
    'card_expand': 1,
    'click_external': 3,
    'calendar_add': 5,
}

# Position bias correction weights
# Interactions from lower positions (further down the list) indicate stronger
# genuine interest since the user scrolled past many other events.
POSITION_WEIGHTS = {
    (1, 5): 1.0,      # Top of list — expected clicks, no boost
    (6, 10): 1.5,     # Moderate scroll
    (11, 20): 2.0,    # Significant scroll
    (21, None): 3.0,  # Deep scroll — strong genuine interest signal
}


def calculate_content_score(
    source_name: Optional[str],
    category: Optional[str],
    cost: Optional[str]
) -> float:
    """
    Calculate content-based score from event metadata.

    This provides a baseline score for events with few/no interactions.

    Args:
        source_name: The event source (e.g., 'Harvard Art Museums')
        category: Event category (e.g., 'music', 'theater')
        cost: Cost string (e.g., 'Free', '$15')

    Returns:
        Score multiplier (typically 0.7 - 1.5)
    """
    score = 1.0

    # Apply source weight
    if source_name:
        score *= SOURCE_WEIGHTS.get(source_name, 1.0)

    # Apply category weight
    if category:
        # Handle both enum values and strings
        cat_str = category.lower() if isinstance(category, str) else category
        score *= CATEGORY_WEIGHTS.get(cat_str, 1.0)

    # Free events get slight boost
    if cost:
        cost_lower = cost.lower()
        if cost_lower in ('free', '$0', '0', 'free admission', '$free'):
            score *= 1.1

    return score


def calculate_interaction_score(interaction_counts: Dict[str, int]) -> float:
    """
    Calculate score based on user interactions.

    Uses weighted count with log normalization to prevent runaway scores.

    Args:
        interaction_counts: Dict mapping interaction_type to count
            e.g., {'card_expand': 10, 'click_external': 3, 'calendar_add': 1}

    Returns:
        Log-normalized interaction score (0.0+)
    """
    if not interaction_counts:
        return 0.0

    weighted_sum = sum(
        INTERACTION_WEIGHTS.get(interaction_type, 0) * count
        for interaction_type, count in interaction_counts.items()
    )

    # Log-normalize to prevent runaway scores
    # log10(1) = 0, log10(10) = 1, log10(100) = 2
    return log10(weighted_sum + 1)


def calculate_temporal_boost(start_datetime: datetime, now: Optional[datetime] = None) -> float:
    """
    Boost events approaching their start date.

    Events happening soon rank higher. Past events are heavily penalized.

    Args:
        start_datetime: Event start time
        now: Current time (for testing), defaults to utcnow

    Returns:
        Temporal boost multiplier (0.1 - 3.0)
    """
    if now is None:
        now = datetime.utcnow()

    # Handle timezone-aware datetimes
    if start_datetime.tzinfo is not None:
        start_naive = start_datetime.replace(tzinfo=None)
    else:
        start_naive = start_datetime

    if now.tzinfo is not None:
        now_naive = now.replace(tzinfo=None)
    else:
        now_naive = now

    hours_until = (start_naive - now_naive).total_seconds() / 3600

    # Past events heavily penalized
    if hours_until <= 0:
        return 0.1

    # Events more than 30 days out get no boost
    if hours_until > 24 * 30:
        return 1.0

    days_until = hours_until / 24

    # Strong bias toward events in the next 7 days:
    # today: 5.0x, 1 day: 3.5x, 3 days: 2.75x, 7 days: 2.4x
    # then taper: 14 days: 2.0x, 21 days: 1.5x, 30 days: 1.0x
    if days_until <= 7:
        boost = 2.0 + 3.0 / (days_until + 1)
    else:
        # Linear taper from ~2.4x at day 7 to 1.0x at day 30
        boost = max(1.0, 2.4 - 1.4 * (days_until - 7) / 23)

    return min(boost, 5.0)


def calculate_honeymoon_boost(created_at: datetime, now: Optional[datetime] = None) -> float:
    """
    Boost newly added events for initial visibility.

    New events get a 48-hour "honeymoon" period with elevated visibility
    to ensure they get a chance to accumulate interactions.

    Args:
        created_at: When the event was first scraped
        now: Current time (for testing), defaults to utcnow

    Returns:
        Honeymoon boost multiplier (1.0 - 1.3)
    """
    if now is None:
        now = datetime.utcnow()

    # Handle timezone-aware datetimes
    if created_at.tzinfo is not None:
        created_naive = created_at.replace(tzinfo=None)
    else:
        created_naive = created_at

    if now.tzinfo is not None:
        now_naive = now.replace(tzinfo=None)
    else:
        now_naive = now

    hours_since_created = (now_naive - created_naive).total_seconds() / 3600

    # No boost after 48 hours
    if hours_since_created > 48:
        return 1.0

    # Negative hours (future created_at) shouldn't happen, but handle gracefully
    if hours_since_created < 0:
        return 1.3

    # Linear decay from 1.3x to 1.0x over 48 hours
    return 1.3 - (0.3 * hours_since_created / 48)


def calculate_event_score(
    source_name: Optional[str],
    category: Optional[str],
    cost: Optional[str],
    start_datetime: datetime,
    scraped_at: datetime,
    interaction_counts: Dict[str, int],
    now: Optional[datetime] = None
) -> float:
    """
    Calculate final composite score for an event.

    Formula: (ContentScore + InteractionScore) x TemporalBoost x HoneymoonBoost

    Args:
        source_name: Event source name
        category: Event category
        cost: Cost string
        start_datetime: Event start time
        scraped_at: When event was first scraped (for honeymoon)
        interaction_counts: Dict of interaction type -> count
        now: Current time (for testing)

    Returns:
        Final composite score (higher = more relevant)
    """
    content = calculate_content_score(source_name, category, cost)
    interaction = calculate_interaction_score(interaction_counts)
    temporal = calculate_temporal_boost(start_datetime, now)
    honeymoon = calculate_honeymoon_boost(scraped_at, now)

    return (content + interaction) * temporal * honeymoon
