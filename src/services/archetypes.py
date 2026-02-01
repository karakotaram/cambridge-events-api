"""Archetype definitions and characteristics for event recommendations"""
from typing import List, Dict, Set, Optional
from dataclasses import dataclass
from src.models.user import ArchetypeEnum


@dataclass
class ArchetypeDefinition:
    """Definition of a user archetype with its characteristics"""
    id: ArchetypeEnum
    name: str
    description: str
    categories: List[str]  # Preferred event categories
    timing_preferences: List[str]  # Preferred time slots
    special_rules: Dict[str, any]  # Special filtering rules


# Archetype definitions matching the plan
ARCHETYPES: Dict[ArchetypeEnum, ArchetypeDefinition] = {
    ArchetypeEnum.CULTURE_PROFESSIONAL: ArchetypeDefinition(
        id=ArchetypeEnum.CULTURE_PROFESSIONAL,
        name="Culture-Loving Professional",
        description="You appreciate the finer things in life - art exhibitions, theater performances, thought-provoking lectures, and live music. Your evenings and weekends are opportunities for cultural enrichment.",
        categories=["arts and culture", "theater", "lectures", "music"],
        timing_preferences=["evening", "weekend"],  # 5pm+, weekends
        special_rules={}
    ),
    ArchetypeEnum.FAMILY_EXPLORER: ArchetypeDefinition(
        id=ArchetypeEnum.FAMILY_EXPLORER,
        name="Family Explorer",
        description="You're always on the lookout for fun, enriching activities the whole family can enjoy together. From community events to kid-friendly performances, you prioritize experiences everyone can share.",
        categories=["community", "arts and culture", "music", "sports"],
        timing_preferences=["weekend_daytime"],  # 9am-5pm on weekends
        special_rules={"family_friendly_only": True}
    ),
    ArchetypeEnum.NIGHTLIFE_ENTHUSIAST: ArchetypeDefinition(
        id=ArchetypeEnum.NIGHTLIFE_ENTHUSIAST,
        name="Nightlife Enthusiast",
        description="Your evenings come alive with live music, great food, craft drinks, and entertainment. You know all the best spots and love discovering new venues.",
        categories=["music", "food and drink", "theater"],
        timing_preferences=["evening"],  # 6pm+
        special_rules={}
    ),
    ArchetypeEnum.ACADEMIC_CURIOUS: ArchetypeDefinition(
        id=ArchetypeEnum.ACADEMIC_CURIOUS,
        name="Academic & Curious",
        description="You're intellectually curious and love learning. Lectures, talks, art discussions, and community conversations are your jam. Bonus points if it's free!",
        categories=["lectures", "arts and culture", "community"],
        timing_preferences=["flexible"],
        special_rules={"prefer_free": True}
    ),
    ArchetypeEnum.SOCIAL_CONNECTOR: ArchetypeDefinition(
        id=ArchetypeEnum.SOCIAL_CONNECTOR,
        name="Social Connector",
        description="You love bringing people together and discovering community happenings. From networking events to food festivals to sports, if it's social, you're there.",
        categories=["community", "food and drink", "sports", "music"],
        timing_preferences=["flexible"],
        special_rules={}
    ),
    ArchetypeEnum.ARTS_AFICIONADO: ArchetypeDefinition(
        id=ArchetypeEnum.ARTS_AFICIONADO,
        name="Arts Aficionado",
        description="Art in all its forms speaks to your soul. Whether it's a gallery opening, a theater premiere, or an indie concert, you appreciate creative expression.",
        categories=["arts and culture", "theater", "music"],
        timing_preferences=["evening"],
        special_rules={}
    ),
    ArchetypeEnum.ACTIVE_ADVENTURER: ArchetypeDefinition(
        id=ArchetypeEnum.ACTIVE_ADVENTURER,
        name="Active Adventurer",
        description="You're always up for something active and outdoorsy. Sports events, fitness activities, and community adventures get you energized.",
        categories=["sports", "community", "other"],
        timing_preferences=["weekend_daytime"],
        special_rules={}
    ),
    ArchetypeEnum.BUDGET_EXPLORER: ArchetypeDefinition(
        id=ArchetypeEnum.BUDGET_EXPLORER,
        name="Budget Explorer",
        description="You know that the best things in life are free (or nearly free). You're a pro at finding amazing community events, free concerts, and art experiences without breaking the bank.",
        categories=["community", "arts and culture", "lectures", "music"],
        timing_preferences=["flexible"],
        special_rules={"free_only": True}
    ),
}


def get_archetype(archetype_id: ArchetypeEnum) -> ArchetypeDefinition:
    """Get archetype definition by ID"""
    return ARCHETYPES.get(archetype_id)


def get_archetype_description(archetype_id: ArchetypeEnum) -> str:
    """Get archetype description by ID"""
    archetype = ARCHETYPES.get(archetype_id)
    return archetype.description if archetype else ""


def get_archetype_name(archetype_id: ArchetypeEnum) -> str:
    """Get archetype display name by ID"""
    archetype = ARCHETYPES.get(archetype_id)
    return archetype.name if archetype else ""


# Mapping from questionnaire answers to archetype points
LIFESTYLE_ARCHETYPE_POINTS: Dict[str, Dict[ArchetypeEnum, int]] = {
    "professional": {
        ArchetypeEnum.CULTURE_PROFESSIONAL: 3,
        ArchetypeEnum.NIGHTLIFE_ENTHUSIAST: 2,
    },
    "parent": {
        ArchetypeEnum.FAMILY_EXPLORER: 5,  # Strong indicator
    },
    "student": {
        ArchetypeEnum.ACADEMIC_CURIOUS: 3,
        ArchetypeEnum.BUDGET_EXPLORER: 2,
    },
    "retired": {
        ArchetypeEnum.ARTS_AFICIONADO: 3,
        ArchetypeEnum.SOCIAL_CONNECTOR: 2,
    },
    "active": {
        ArchetypeEnum.ACTIVE_ADVENTURER: 4,
    },
}

INTEREST_ARCHETYPE_POINTS: Dict[str, Dict[ArchetypeEnum, int]] = {
    "live_music": {
        ArchetypeEnum.NIGHTLIFE_ENTHUSIAST: 3,
        ArchetypeEnum.ARTS_AFICIONADO: 2,
    },
    "theater": {
        ArchetypeEnum.CULTURE_PROFESSIONAL: 3,
        ArchetypeEnum.ARTS_AFICIONADO: 2,
    },
    "food_drink": {
        ArchetypeEnum.NIGHTLIFE_ENTHUSIAST: 3,
        ArchetypeEnum.SOCIAL_CONNECTOR: 2,
    },
    "lectures": {
        ArchetypeEnum.ACADEMIC_CURIOUS: 3,
        ArchetypeEnum.CULTURE_PROFESSIONAL: 2,
    },
    "community": {
        ArchetypeEnum.SOCIAL_CONNECTOR: 3,
        ArchetypeEnum.BUDGET_EXPLORER: 2,
    },
    "sports": {
        ArchetypeEnum.ACTIVE_ADVENTURER: 4,
    },
    "art_galleries": {
        ArchetypeEnum.ARTS_AFICIONADO: 3,
        ArchetypeEnum.CULTURE_PROFESSIONAL: 2,
    },
}

TIMING_ARCHETYPE_POINTS: Dict[str, Dict[ArchetypeEnum, int]] = {
    "weekday_evening": {
        ArchetypeEnum.CULTURE_PROFESSIONAL: 2,
        ArchetypeEnum.NIGHTLIFE_ENTHUSIAST: 2,
    },
    "weekend_daytime": {
        ArchetypeEnum.FAMILY_EXPLORER: 3,
        ArchetypeEnum.ACTIVE_ADVENTURER: 2,
    },
    "weekend_evening": {
        ArchetypeEnum.NIGHTLIFE_ENTHUSIAST: 3,
        ArchetypeEnum.ARTS_AFICIONADO: 2,
    },
    "flexible": {
        ArchetypeEnum.ACADEMIC_CURIOUS: 2,
        ArchetypeEnum.BUDGET_EXPLORER: 2,
        ArchetypeEnum.SOCIAL_CONNECTOR: 1,
    },
}

BUDGET_ARCHETYPE_POINTS: Dict[str, Dict[ArchetypeEnum, int]] = {
    "free_only": {
        ArchetypeEnum.BUDGET_EXPLORER: 4,
        ArchetypeEnum.ACADEMIC_CURIOUS: 2,
    },
    "under_20": {
        ArchetypeEnum.BUDGET_EXPLORER: 2,
        ArchetypeEnum.SOCIAL_CONNECTOR: 1,
    },
    "moderate": {
        ArchetypeEnum.CULTURE_PROFESSIONAL: 2,
        ArchetypeEnum.NIGHTLIFE_ENTHUSIAST: 1,
    },
    "any": {
        ArchetypeEnum.ARTS_AFICIONADO: 2,
        ArchetypeEnum.NIGHTLIFE_ENTHUSIAST: 2,
    },
}
