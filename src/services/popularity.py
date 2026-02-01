"""Event popularity scoring service"""
from typing import Dict, Optional
from datetime import datetime, timedelta
import pytz

from src.models.event import Event, EASTERN_TZ


# Venue reputation scores (0.0 - 1.0)
VENUE_SCORES: Dict[str, float] = {
    # Top tier venues
    "Harvard Art Museums": 0.95,
    "Sanders Theatre": 0.95,
    "MIT Museum": 0.90,
    "Museum of Science": 0.90,
    "Brattle Theatre": 0.90,
    "American Repertory Theater": 0.90,
    "Harvard Memorial Church": 0.88,
    "Mahindra Humanities Center": 0.88,

    # High quality venues
    "Club Passim": 0.85,
    "The Sinclair": 0.85,
    "Somerville Theatre": 0.85,
    "Central Square Theater": 0.85,
    "Cambridge Public Library": 0.85,
    "The Middle East": 0.83,
    "Longy School of Music": 0.83,

    # Good venues
    "The Dance Complex": 0.80,
    "The Armory": 0.80,
    "The Rockwell": 0.80,
    "Aeronaut Brewing": 0.80,
    "Lamplighter Brewing": 0.78,
    "The Lilypad": 0.78,
    "Grolier Poetry Book Shop": 0.78,

    # Solid venues
    "Harvard Book Store": 0.75,
    "First Parish Cambridge": 0.75,
    "Theatre at First": 0.75,
    "Mad Monkfish": 0.73,
    "The Portico": 0.73,
    "Comedy Studio": 0.73,
}

# Source reliability scores (0.0 - 1.0)
SOURCE_SCORES: Dict[str, float] = {
    "Harvard Art Museums": 0.95,
    "MIT Calendar": 0.93,
    "Cambridge Public Library": 0.92,
    "City of Cambridge": 0.90,
    "Museum of Science": 0.90,
    "Somerville Theatre": 0.88,
    "Club Passim": 0.88,
    "Brattle Theatre": 0.88,
    "The Sinclair": 0.85,
    "The Middle East": 0.85,
    "Aeronaut Brewing": 0.83,
    "The Dance Complex": 0.80,
    "BostonShows.org": 0.70,  # Aggregator, less reliable
}

# Category base scores
CATEGORY_SCORES: Dict[str, float] = {
    "music": 0.90,
    "arts and culture": 0.85,
    "theater": 0.83,
    "lectures": 0.80,
    "community": 0.78,
    "food and drink": 0.75,
    "sports": 0.73,
    "other": 0.70,
}


def calculate_venue_score(event: Event) -> float:
    """Calculate score based on venue reputation"""
    if not event.venue_name:
        return 0.5  # Default for unknown venue

    # Check exact match first
    if event.venue_name in VENUE_SCORES:
        return VENUE_SCORES[event.venue_name]

    # Check partial matches
    venue_lower = event.venue_name.lower()
    for venue, score in VENUE_SCORES.items():
        if venue.lower() in venue_lower or venue_lower in venue.lower():
            return score

    return 0.5  # Default for unlisted venue


def calculate_source_score(event: Event) -> float:
    """Calculate score based on source reliability"""
    if not event.source_name:
        return 0.5

    if event.source_name in SOURCE_SCORES:
        return SOURCE_SCORES[event.source_name]

    # Check partial matches
    source_lower = event.source_name.lower()
    for source, score in SOURCE_SCORES.items():
        if source.lower() in source_lower or source_lower in source.lower():
            return score

    return 0.6  # Slightly above default for any listed source


def calculate_cost_score(event: Event) -> float:
    """Calculate score based on cost (free events score higher)"""
    if not event.cost:
        return 0.5  # Unknown cost

    cost_lower = event.cost.lower()

    if any(x in cost_lower for x in ["free", "$0", "no cost", "no charge"]):
        return 1.0

    # Try to extract price
    import re
    price_match = re.search(r'\$(\d+)', event.cost)
    if price_match:
        price = int(price_match.group(1))
        if price == 0:
            return 1.0
        elif price <= 10:
            return 0.85
        elif price <= 20:
            return 0.70
        elif price <= 50:
            return 0.55
        else:
            return 0.40

    return 0.5


def calculate_recurrence_score(event: Event) -> float:
    """Calculate score boost for recurring events (generally more established)"""
    if event.recurring_pattern:
        return 0.3  # Boost for recurring events
    return 0.0


def calculate_freshness_score(event: Event) -> float:
    """Calculate score based on how soon the event is"""
    now = datetime.now(EASTERN_TZ)
    event_dt = event.start_datetime

    # Ensure timezone awareness
    if event_dt.tzinfo is None:
        event_dt = EASTERN_TZ.localize(event_dt)

    days_until = (event_dt - now).days

    if days_until < 0:
        return 0.0  # Past event
    elif days_until <= 3:
        return 1.0  # This week gets highest score
    elif days_until <= 7:
        return 0.9
    elif days_until <= 14:
        return 0.75
    elif days_until <= 30:
        return 0.6
    else:
        return 0.4  # Far future events score lower


def calculate_category_score(event: Event) -> float:
    """Calculate score based on category popularity"""
    if not event.category:
        return 0.5

    category_value = event.category.value if hasattr(event.category, 'value') else str(event.category)
    return CATEGORY_SCORES.get(category_value, 0.5)


def calculate_popularity_score(
    event: Event,
    click_count: int = 0,
    weights: Optional[Dict[str, float]] = None
) -> float:
    """
    Calculate composite popularity score for an event.

    Args:
        event: The event to score
        click_count: Number of clicks from email recommendations
        weights: Optional custom weights for each factor

    Returns:
        Float score between 0.0 and 1.0
    """
    default_weights = {
        "venue": 0.20,
        "source": 0.15,
        "cost": 0.15,
        "recurrence": 0.05,
        "freshness": 0.25,
        "category": 0.10,
        "clicks": 0.10,
    }

    w = weights or default_weights

    # Calculate individual scores
    venue_score = calculate_venue_score(event)
    source_score = calculate_source_score(event)
    cost_score = calculate_cost_score(event)
    recurrence_score = calculate_recurrence_score(event)
    freshness_score = calculate_freshness_score(event)
    category_score = calculate_category_score(event)

    # Click score (normalized, caps at 10 clicks for max score)
    click_score = min(click_count / 10.0, 1.0) if click_count > 0 else 0.0

    # Weighted composite
    composite = (
        venue_score * w["venue"] +
        source_score * w["source"] +
        cost_score * w["cost"] +
        recurrence_score * w["recurrence"] +
        freshness_score * w["freshness"] +
        category_score * w["category"] +
        click_score * w["clicks"]
    )

    return round(composite, 4)


def get_event_scores(event: Event, click_count: int = 0) -> Dict[str, float]:
    """Get all individual scores for an event (for debugging/admin)"""
    return {
        "venue_score": calculate_venue_score(event),
        "source_score": calculate_source_score(event),
        "cost_score": calculate_cost_score(event),
        "recurrence_score": calculate_recurrence_score(event),
        "freshness_score": calculate_freshness_score(event),
        "category_score": calculate_category_score(event),
        "popularity_score": calculate_popularity_score(event, click_count),
    }
