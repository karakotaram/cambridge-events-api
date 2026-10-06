"""Deduplication: one rule within a source, another across sources.

Every case here is a real pair from the 2026-10-05 audit. Within a source,
fuzzy matching had merged events a venue runs back to back under similar names;
across sources, run order alone decided which copy survived, so an aggregator
filed under the wrong kind took credit for venues' own listings.
"""
from datetime import datetime

from src.models.event import EventCreate
from src.utils.deduplicator import EventDeduplicator as D


def event(title, start, source, venue=None, url=None, description="An event description."):
    return EventCreate(title=title, description=description, start_datetime=start,
                       source_name=source, source_url=url or f"https://example.org/{abs(hash((title, start, source)))}",
                       venue_name=venue)


def at(hour, minute=0, day=7):
    return datetime(2026, 10, day, hour, minute)


# --------------------------------------------------------------------------- #
# Within one source: only the same occurrence listed twice is a duplicate
# --------------------------------------------------------------------------- #

def test_back_to_back_sessions_of_one_programme_are_kept():
    """Longfellow House runs "Hourly Tour" at 10:00 and 11:00. Fuzzy matching
    within an hour merged them: 105 tours became 60."""
    tours = [event("Hourly Tour", at(h), "Longfellow House", "Longfellow House") for h in range(10, 16)]
    assert len(D.deduplicate_events(tours)) == 6


def test_similarly_named_classes_are_different_events():
    """The Dance Complex's "Tap Level 1" at 6 PM swallowed "Tap Level 2" at 7 PM."""
    classes = [event("Tap Level 1", at(18), "The Dance Complex", "The Dance Complex"),
               event("Tap Level 2", at(19), "The Dance Complex", "The Dance Complex")]
    assert len(D.deduplicate_events(classes)) == 2


def test_same_programme_at_two_branches_is_two_events():
    """Library branches share a long venue prefix, which used to clear the
    venue-similarity bar."""
    same_time = [
        event("Evening Family Story Time", at(18), "City of Cambridge", "Collins Branch"),
        event("Evening Family Story Time", at(18), "City of Cambridge", "O'Connell Branch"),
    ]
    assert len(D.deduplicate_events(same_time)) == 2


def test_the_same_listing_twice_is_still_merged():
    """A curly-vs-straight apostrophe, or one copy missing its venue, is the
    same occurrence."""
    pair = [event("Writers' Den", at(18), "Somerville Public Library", "Central Library"),
            event("Writers’ Den", at(18), "Somerville Public Library", None)]
    assert len(D.deduplicate_events(pair)) == 1


# --------------------------------------------------------------------------- #
# Across sources: fuzzy, and the venue's own listing wins
# --------------------------------------------------------------------------- #

def test_the_venue_wins_over_an_aggregator_whatever_the_order():
    """BostonShows.org ran first and merge kept the first event, so Sanders
    Theatre's listings were published under BostonShows' name and link."""
    aggregator = event("Yo-Yo Ma", at(19, 30), "BostonShows.org", "Sanders Theatre",
                       url="https://bostonshows.org/yo-yo-ma", description="x" * 400)
    venue = event("Yo-Yo Ma", at(19, 30), "Sanders Theatre", "Sanders Theatre",
                  url="https://boxoffice.harvard.edu/yo-yo-ma")
    for events in ([aggregator, venue], [venue, aggregator]):
        (merged,) = D.deduplicate_events(events)
        assert merged.source_name == "Sanders Theatre"
        assert merged.source_url == "https://boxoffice.harvard.edu/yo-yo-ma"
        assert merged.description == "x" * 400, "the fuller description still fills in"


def test_a_presenter_prefix_does_not_hide_a_duplicate():
    a = event("Global Arts Live presents Brad Mehldau Trio", at(19, 30, day=13), "Sanders Theatre", "Sanders Theatre")
    b = event("Brad Mehldau Trio", at(19, 30, day=13), "BostonShows.org", "Sanders Theatre")
    assert D.are_duplicates(a, b)
    # Containment alone is weak evidence: it needs the venue to agree
    assert not D.are_duplicates(a, b.model_copy(update={"venue_name": None}))


def test_different_venues_are_never_the_same_show():
    a = event("Open Mic Night", at(20), "The Rockwell", "The Rockwell")
    b = event("Open Mic Night", at(20), "BostonShows.org", "The Burren")
    assert not D.are_duplicates(a, b)


def test_a_match_can_straddle_midnight():
    a = event("The Midnight Hour", at(23, 45), "The Mad Monkfish", "The Mad Monkfish")
    b = event("The Midnight Hour", datetime(2026, 10, 8, 0, 15), "BostonShows.org", "The Mad Monkfish")
    assert D.find_duplicates([a, b]) == [[0, 1]]


# --------------------------------------------------------------------------- #
# Preserved events are reconciled with this run's events
# --------------------------------------------------------------------------- #

def as_dict(e):
    return e.model_dump(mode="json")


def test_preserved_events_no_longer_bypass_deduplication():
    """User submissions and CI-skipped venues were appended after dedup, so
    "The Raven" was listed twice and Somerville Theatre shows appeared once
    from the venue and once from BostonShows."""
    raven_scraped = as_dict(event("The Raven", at(18), "Longfellow House", "Longfellow House"))
    raven_submitted = as_dict(event("The Raven", at(18), "User Submitted", "Longfellow House"))
    show_aggregator = as_dict(event("Andrea Gibson", at(18, 30, day=13), "BostonShows.org", "Somerville Theatre"))
    show_venue = as_dict(event("Andrea Gibson", at(18, 30, day=13), "Somerville Theatre", "Somerville Theatre"))
    unrelated = as_dict(event("Hourly Tour", at(10), "Longfellow House", "Longfellow House"))

    fresh, preserved = D.reconcile_preserved(
        [raven_scraped, show_aggregator, unrelated], [raven_submitted, show_venue])

    # A submission always survives: the sync would re-add it otherwise
    assert raven_submitted in preserved and raven_scraped not in fresh
    # The venue's own preserved listing beats a fresh aggregator copy
    assert show_venue in preserved and show_aggregator not in fresh
    assert unrelated in fresh


def test_a_fresh_listing_beats_a_stale_preserved_one_of_equal_rank():
    stale = as_dict(event("Swing Dance", at(20), "Boston Swing Central", "Boston Swing Central"))
    fresh_copy = as_dict(event("Swing Dance", at(20), "The Dance Complex", "Boston Swing Central"))
    fresh, preserved = D.reconcile_preserved([fresh_copy], [stale])
    assert fresh == [fresh_copy] and preserved == []


# --------------------------------------------------------------------------- #
# The enrichment pass's fuzzy dedup
# --------------------------------------------------------------------------- #

def test_enrichment_dedup_needs_the_same_time_and_venue():
    """It matched on title and calendar day alone, and kept the longer
    description regardless of source."""
    from src.agents.enrichment import EnrichmentAgent

    events = [
        as_dict(event("Jazz Brunch", at(11), "The Mad Monkfish", "The Mad Monkfish")),
        as_dict(event("Jazz Brunch", at(15), "BostonShows.org", "The Mad Monkfish")),      # 4 h later
        as_dict(event("Jazz Brunches", at(11), "BostonShows.org", "Lamplighter Brewing")),  # elsewhere
        as_dict(event("Yo-Yo Ma", at(19, 30), "BostonShows.org", "Sanders Theatre", description="x" * 400)),
        as_dict(event("Yo-Yo Ma", at(19, 30), "Sanders Theatre", "Sanders Theatre")),
    ]
    removed = EnrichmentAgent().fuzzy_cross_source_dedup(events)
    assert removed == 1
    assert [e["source_name"] for e in events if e["title"] == "Yo-Yo Ma"] == ["Sanders Theatre"]
