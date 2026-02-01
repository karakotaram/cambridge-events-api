"""Onboarding service for archetype calculation and user creation"""
from typing import Tuple, Optional, Dict, List
from collections import defaultdict

from src.models.user import (
    ArchetypeEnum,
    QuestionnaireResponses,
    Question,
    QuestionOption,
)
from src.services.archetypes import (
    ARCHETYPES,
    LIFESTYLE_ARCHETYPE_POINTS,
    INTEREST_ARCHETYPE_POINTS,
    TIMING_ARCHETYPE_POINTS,
    BUDGET_ARCHETYPE_POINTS,
    get_archetype_description,
    get_archetype_name,
)


def get_questionnaire() -> List[Question]:
    """Return the 4 onboarding questions"""
    return [
        Question(
            id="lifestyle",
            question="Which best describes your current lifestyle?",
            type="single",
            options=[
                QuestionOption(
                    value="professional",
                    label="Working Professional",
                    description="You work during the day and look for evening/weekend activities"
                ),
                QuestionOption(
                    value="parent",
                    label="Parent with Young Kids",
                    description="You're looking for family-friendly activities"
                ),
                QuestionOption(
                    value="student",
                    label="Student or Recent Grad",
                    description="Flexible schedule, budget-conscious"
                ),
                QuestionOption(
                    value="retired",
                    label="Retired or Flexible Schedule",
                    description="You have daytime availability for events"
                ),
                QuestionOption(
                    value="active",
                    label="Active & Fitness-Focused",
                    description="You prioritize sports and outdoor activities"
                ),
            ]
        ),
        Question(
            id="interests",
            question="What interests you most? (Pick up to 2)",
            type="multi",
            max_selections=2,
            options=[
                QuestionOption(
                    value="live_music",
                    label="Live Music",
                    description="Concerts, bands, DJs"
                ),
                QuestionOption(
                    value="theater",
                    label="Theater & Performances",
                    description="Plays, musicals, dance"
                ),
                QuestionOption(
                    value="food_drink",
                    label="Food, Beer & Wine",
                    description="Tastings, food festivals, bar events"
                ),
                QuestionOption(
                    value="lectures",
                    label="Talks & Lectures",
                    description="Author talks, panels, discussions"
                ),
                QuestionOption(
                    value="community",
                    label="Community & Social",
                    description="Meetups, markets, festivals"
                ),
                QuestionOption(
                    value="sports",
                    label="Sports & Fitness",
                    description="Games, runs, outdoor activities"
                ),
                QuestionOption(
                    value="art_galleries",
                    label="Art & Galleries",
                    description="Museums, exhibitions, openings"
                ),
            ]
        ),
        Question(
            id="timing",
            question="When do you usually have time for events?",
            type="single",
            options=[
                QuestionOption(
                    value="weekday_evening",
                    label="Weekday Evenings",
                    description="After work, typically 5pm onwards"
                ),
                QuestionOption(
                    value="weekend_daytime",
                    label="Weekend Daytime",
                    description="Saturday/Sunday mornings and afternoons"
                ),
                QuestionOption(
                    value="weekend_evening",
                    label="Weekend Evenings",
                    description="Friday/Saturday nights out"
                ),
                QuestionOption(
                    value="flexible",
                    label="I'm Flexible",
                    description="Available various times"
                ),
            ]
        ),
        Question(
            id="budget",
            question="What's your typical event budget?",
            type="single",
            options=[
                QuestionOption(
                    value="free_only",
                    label="Free Events Only",
                    description="I stick to free community events"
                ),
                QuestionOption(
                    value="under_20",
                    label="Under $20",
                    description="Budget-friendly options"
                ),
                QuestionOption(
                    value="moderate",
                    label="$20-50",
                    description="Happy to pay for good experiences"
                ),
                QuestionOption(
                    value="any",
                    label="Price Isn't a Concern",
                    description="I'll pay for what I want"
                ),
            ]
        ),
    ]


def calculate_archetype(responses: QuestionnaireResponses) -> Tuple[ArchetypeEnum, Optional[ArchetypeEnum]]:
    """
    Calculate primary and secondary archetypes based on questionnaire responses.

    Returns:
        Tuple of (primary_archetype, secondary_archetype)
        secondary_archetype may be None if scores are too close or single dominant
    """
    scores: Dict[ArchetypeEnum, int] = defaultdict(int)

    # Add points from lifestyle
    lifestyle_points = LIFESTYLE_ARCHETYPE_POINTS.get(responses.lifestyle, {})
    for archetype, points in lifestyle_points.items():
        scores[archetype] += points

    # Add points from interests (can be multiple)
    for interest in responses.interests:
        interest_points = INTEREST_ARCHETYPE_POINTS.get(interest, {})
        for archetype, points in interest_points.items():
            scores[archetype] += points

    # Add points from timing
    timing_points = TIMING_ARCHETYPE_POINTS.get(responses.timing, {})
    for archetype, points in timing_points.items():
        scores[archetype] += points

    # Add points from budget
    budget_points = BUDGET_ARCHETYPE_POINTS.get(responses.budget, {})
    for archetype, points in budget_points.items():
        scores[archetype] += points

    # Sort by score descending
    sorted_archetypes = sorted(scores.items(), key=lambda x: x[1], reverse=True)

    if not sorted_archetypes:
        # Default if somehow no scores (shouldn't happen)
        return ArchetypeEnum.SOCIAL_CONNECTOR, None

    primary = sorted_archetypes[0][0]
    primary_score = sorted_archetypes[0][1]

    # Determine secondary archetype
    secondary = None
    if len(sorted_archetypes) > 1:
        secondary_candidate = sorted_archetypes[1][0]
        secondary_score = sorted_archetypes[1][1]

        # Only assign secondary if it has meaningful score (at least 40% of primary)
        if secondary_score >= primary_score * 0.4 and secondary_score >= 3:
            secondary = secondary_candidate

    return primary, secondary


def get_archetype_result(
    primary: ArchetypeEnum,
    secondary: Optional[ArchetypeEnum]
) -> Dict:
    """Get formatted archetype result for API response"""
    primary_name = get_archetype_name(primary)
    primary_desc = get_archetype_description(primary)

    if secondary:
        secondary_name = get_archetype_name(secondary)
        description = f"{primary_desc}\n\nYou also have traits of a {secondary_name}!"
    else:
        description = primary_desc

    return {
        "primary_archetype": primary.value,
        "primary_name": primary_name,
        "secondary_archetype": secondary.value if secondary else None,
        "secondary_name": get_archetype_name(secondary) if secondary else None,
        "description": description,
    }
