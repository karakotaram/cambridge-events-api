"""The validator files an event under food and drink from words in its text.

Matched as substrings, 'ipa' found "participants" and "anticipating", which put
Dance Complex classes, library workshops and a Harvard Film Archive screening
under food and drink.
"""
from datetime import datetime

import pytest

from src.models.event import EventCategory, EventCreate
from src.utils.validator import EventValidator


def make(title, description, venue="Somewhere Hall", category=EventCategory.ARTS_CULTURE):
    return EventCreate(
        title=title, description=description, start_datetime=datetime(2026, 11, 14, 14, 0),
        venue_name=venue, category=category,
        source_url="https://example.org/e", source_name="Example")


@pytest.mark.parametrize("title, description", [
    ("All About Lily Chou-Chou", "Twenty years on, anticipating many of the anxieties of online life."),
    ("Contemporary Fusion", "Participants of all levels welcome."),
    ("Walk in the Meadow", "A guided walk."),
    ("Harvest Crafts", "For villagers and visitors alike."),
])
def test_a_word_fragment_is_not_a_drink(title, description):
    assert not EventValidator.is_food_and_drink_event(make(title, description))


@pytest.mark.parametrize("title, description", [
    ("Hazy IPA Release", "Four new cans."),
    ("Fall Festival", "Craft beer and cider on the lawn."),
    ("Supper Club", "A pop-up dinner with a cooking class."),
])
def test_the_words_themselves_still_count(title, description):
    assert EventValidator.is_food_and_drink_event(make(title, description))


def test_a_film_keeps_its_category_through_cleaning():
    film = make("All About Lily Chou-Chou", "Directed by Shunji Iwai, 2001. Anticipating "
                "many of the anxieties of online life.", venue="Harvard Film Archive",
                category=EventCategory.FILM)
    assert EventValidator.clean_and_enhance(film).category == EventCategory.FILM
