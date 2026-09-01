"""The gate: what may and may not be published.

Written after a rehearsal of the real pipeline caught the gate letting through a
run that would have replaced 2,974 events with 232. Every scraper had failed,
only the always-preserved user submissions survived, and the fifteen "source
disappeared" findings were drift — which is in report mode by default. Losing
almost the whole calendar cannot depend on a tunable, so `check_collapse` is
absolute and these tests pin it.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.quality import gate
from src.quality.fingerprint import fingerprint_all, record
from src.utils.storage import load_events

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture
def published():
    return load_events()


@pytest.fixture
def baseline(tmp_path, published):
    """Four healthy runs of City of Cambridge, so drift has something to say."""
    path = tmp_path / "fingerprints.json"
    city = [e for e in published if e["source_name"] == "City of Cambridge"]
    for i in range(4):
        record(fingerprint_all(city), path=path, run_id=f"healthy-{i}")
    return path


def test_healthy_run_passes(published, baseline):
    d = gate.evaluate(published, previous=published, baseline_path=baseline)
    assert d.passed and not d.blocking, d.report()


def test_fabricated_dates_are_blocked_on_invariants_alone(baseline):
    """The 2026-08-31 failure, replayed from the commit that shipped it.

    Blocked with drift in report mode, which is the point: invariants do not
    need a baseline and do not need tuning.
    """
    import subprocess

    raw = subprocess.run(["git", "show", "94f2b07:data/events.json"],
                         cwd=REPO, capture_output=True, text=True).stdout
    if not raw.strip():
        pytest.skip("commit 94f2b07 not available")
    shipped = json.loads(raw)

    d = gate.evaluate(shipped, previous=shipped, baseline_path=baseline,
                      drift_mode="report")
    assert d.blocking
    rules = {v.rule for v in d.violations if v.severity == "error"}
    assert "clock_stamped" in rules and "timestamp_pileup" in rules


def test_mass_collapse_is_blocked_regardless_of_drift_mode(published):
    """The hole the rehearsal found. 232 events replacing 2,974 must never ship."""
    survivors = [e for e in published if e["source_name"] == "User Submitted"]
    for drift_mode in ("report", "enforce"):
        d = gate.evaluate(survivors, previous=published, drift_mode=drift_mode)
        assert d.blocking, f"drift_mode={drift_mode}: {d.report()}"
        assert any("collapsed" in r for r in d.reasons)
        assert any("vanished" in r for r in d.reasons)


def test_collapse_check_is_silent_without_a_previous_state():
    """A first run has nothing to compare against and must not block on that."""
    events = [{"source_name": "X", "title": "t", "description": "d" * 20,
               "start_datetime": (datetime.now() + timedelta(days=3)
                                  ).replace(second=0, microsecond=0).isoformat(),
               "source_url": "http://x", "id": "a" * 32}]
    assert not gate.check_collapse(events, None)
    assert not gate.check_collapse(events, [])


def test_a_normal_daily_change_is_not_mistaken_for_collapse(published):
    """Sources routinely gain and lose a few events; that is not a collapse."""
    trimmed = published[: int(len(published) * 0.8)]
    assert not gate.check_collapse(trimmed, published)


def test_drift_blocks_only_when_enforced(published, baseline):
    """A drift-only problem reports by default and blocks when asked.

    Constructed to trip drift *without* tripping collapse or any invariant:
    same event count, same sources, but the calendar squeezed from 63 days onto
    three. That is the shape of a truncated run, and only drift sees it.
    """
    import copy

    city = [e for e in published if e["source_name"] == "City of Cambridge"]
    squeezed = copy.deepcopy(city)
    for i, event in enumerate(squeezed):
        day = 14 + (i % 3)
        # Spread across enough distinct times that no single timestamp exceeds
        # the pileup invariant — the point is to isolate drift, not to trip
        # everything at once.
        slot = i // 3
        hour, minute = 9 + slot % 12, (slot // 12 % 4) * 15
        event["start_datetime"] = f"2026-09-{day:02d}T{hour:02d}:{minute:02d}:00"
        event["id"] = f"{i:032x}"          # keep ids unique after retiming

    assert len(squeezed) == len(city)      # no collapse
    assert not gate.evaluate(squeezed, previous=city, drift_mode="report",
                             baseline_path=baseline).blocking
    blocked = gate.evaluate(squeezed, previous=city, drift_mode="enforce",
                            baseline_path=baseline)
    assert blocked.blocking
    assert {d.metric for d in blocked.drifts} & {"date_span_days", "max_events_one_day"}


def test_force_overrides_everything(published, baseline):
    survivors = [e for e in published if e["source_name"] == "User Submitted"]
    d = gate.evaluate(survivors, previous=published, baseline_path=baseline, force=True)
    assert d.passed and not d.blocking
    assert "overridden by --force" in d.reasons


def test_report_mode_never_blocks(published, baseline):
    survivors = [e for e in published if e["source_name"] == "User Submitted"]
    d = gate.evaluate(survivors, previous=published, baseline_path=baseline, mode="report")
    assert not d.passed and not d.blocking


def test_quarantine_preserves_the_evidence(tmp_path, published, baseline):
    survivors = [e for e in published if e["source_name"] == "User Submitted"]
    d = gate.evaluate(survivors, previous=published, baseline_path=baseline)
    target = gate.quarantine(survivors, d, "test-run", root=tmp_path)
    assert json.loads((target / "events.json").read_text())
    assert json.loads((target / "gate.json").read_text())["decision"] == "BLOCKED"
    assert "collapsed" in (target / "report.txt").read_text()


# --------------------------------------------------------------------------- #
# Preservation: a failed scrape must not delete data
# --------------------------------------------------------------------------- #

def test_a_failed_source_keeps_its_upcoming_events(tmp_path, monkeypatch):
    """Harvard Book Store started 403-ing from every IP. Because it only had
    "preserve" status inside CI, a local run deleted all 21 of its events, and
    Somerville Theatre's with them. A scrape that failed says nothing about
    whether a venue still has a programme."""
    import json as _json
    from datetime import datetime, timedelta

    import scrape
    from src.models.event import Event, EventCreate

    future = (datetime.now() + timedelta(days=10)).replace(second=0, microsecond=0)
    past = (datetime.now() - timedelta(days=10)).replace(second=0, microsecond=0)

    def stored(source, when, n=1):
        return [{
            "id": f"{source}-{when.date()}-{i}", "title": f"{source} show {i}",
            "description": "d" * 25, "start_datetime": when.isoformat(),
            "source_url": f"http://x/{source}/{i}", "source_name": source,
        } for i in range(n)]

    on_disk = (stored("Harvard Book Store", future, 3)
               + stored("Harvard Book Store", past, 2)
               + stored("Brattle Theatre", future, 4))
    monkeypatch.setattr(scrape, "load_stored_events", lambda *a, **k: on_disk)

    orchestrator = scrape.ScraperOrchestrator()
    fresh = [Event.from_create(EventCreate(
        title="Brattle screening", description="d" * 25, start_datetime=future,
        source_url="http://b/1", source_name="Brattle Theatre"))]

    published = orchestrator.build_publish_set(
        fresh, skipped_sources=[], barren_sources=["Harvard Book Store"])

    by_source = {}
    for e in published:
        by_source[e["source_name"]] = by_source.get(e["source_name"], 0) + 1

    assert by_source.get("Harvard Book Store") == 3, (
        "the 3 upcoming events of the failed source must survive; "
        "its 2 past ones must not")
    assert by_source.get("Brattle Theatre") == 1, "the working source is replaced, not merged"


def test_a_source_that_succeeded_is_replaced_not_merged():
    """Preservation applies only to sources that produced nothing. A source that
    scraped successfully is authoritative for its own events."""
    import scrape
    from datetime import datetime, timedelta
    from src.models.event import Event, EventCreate

    future = (datetime.now() + timedelta(days=5)).replace(second=0, microsecond=0)
    fresh = [Event.from_create(EventCreate(
        title="Only one now", description="d" * 25, start_datetime=future,
        source_url="http://b/new", source_name="Brattle Theatre"))]

    published = scrape.ScraperOrchestrator().build_publish_set(
        fresh, skipped_sources=[], barren_sources=[])
    brattle = [e for e in published if e["source_name"] == "Brattle Theatre"]
    assert len(brattle) == 1 and brattle[0]["title"] == "Only one now"
