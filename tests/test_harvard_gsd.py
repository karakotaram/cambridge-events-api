"""Harvard GSD — the school's WordPress events API.

Fixture: events-starts-2026-08-15.json.gz is `/wp-json/gsd/v1/events?per_page=100
&starts=2026-08-15`, captured 2026-10-06. Reaching back to August puts every
case in one payload: the two events the GSD asked us not to list, the named
_positions lecture that must stay, five alumni events in four cities, and a
two-day symposium.
"""
from __future__ import annotations

import copy
import json
from datetime import datetime

import pytest

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture


@pytest.fixture
def items():
    return json.loads(read_fixture("harvard_gsd", "events-starts-2026-08-15.json.gz"))


def _item(items, slug_prefix):
    return next(i for i in items if i["slug"].startswith(slug_prefix))


def test_gsd_drops_both_alumni_events_that_were_published(items, offline):
    """Production carried two off-site alumni events on 2026-10-05: a reception
    at a "Private Residence" in Miami, and a talk at "Surf Incubator, …,
    Seattle" — a city with no state, which the address check could not see."""
    events = BY_NAME["Harvard GSD"].load().parse_items(items)
    titles = " | ".join(e.title for e in events)

    assert "ULI Fall Meeting Miami" not in titles
    assert "Seattle" not in titles
    assert not [e for e in events if "Alumni" in e.title]


@pytest.mark.parametrize("slug,strip", [
    ("uli-fall-meeting-miami", "type"),               # still "Private Residence"
    ("changing-the-rules", "type"),                   # still stamped -07:00
    ("asla-los-angeles", "type"),                     # still ", CA, 90017"
    ("insider-tour-of-biidaasige-park", "type"),      # still "Toronto, Canada"
])
def test_gsd_off_site_signals_stand_alone(items, slug, strip, offline):
    """Each off-site event is caught by more than one signal, so a feed change
    to any single field does not put a Seattle event back on the calendar."""
    occ = copy.deepcopy(_item(items, slug)["occurrences"][0])
    occ[strip] = "Lecture"
    assert BY_NAME["Harvard GSD"].load().off_site_reason(occ)


def test_gsd_keeps_the_named_positions_lectures(items, offline):
    """The GSD asked us not to list `_Positions` (slug "_positions", Sep 10).
    The named lectures in that series — "_positions: Kengo Kuma" — are public
    and have their own slugs; they must not be caught by the exclusion."""
    events = BY_NAME["Harvard GSD"].load().parse_items(items)
    kuma = [e for e in events if "Kengo Kuma" in e.title]

    assert len(kuma) == 1 and kuma[0].title.startswith("_positions:")
    assert not [e for e in events if e.source_url.rstrip("/").endswith("/_positions")]
    assert not [e for e in events if "Comeback" in e.title]


def test_gsd_emits_every_day_of_a_multi_day_event(items, offline):
    """Only `occurrences[0]` was read, so the Nov 13 symposium day of
    "Infrastructure in a Time of Flux" was dropped."""
    events = BY_NAME["Harvard GSD"].load().parse_items(items)
    flux = sorted(e.start_datetime for e in events if "Methods, Conditions, and Situations" in e.title)

    assert flux == [datetime(2026, 11, 12, 18, 30), datetime(2026, 11, 13, 9, 0)]


def test_gsd_output_satisfies_invariants(items, monkeypatch, offline):
    """The whole payload through the scraper's own fetch seam."""
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: type(
        "_R", (), {"raise_for_status": lambda s: None, "json": lambda s: items})())
    events = BY_NAME["Harvard GSD"].load().scrape_events()

    assert len(events) == 17
    assert {e.source_name for e in events} == {"Harvard GSD"}
    assert all(e.start_datetime.tzinfo is None for e in events)
    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events]) if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)
