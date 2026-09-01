"""The documentation must not be allowed to rot into a trap.

Every claim these tests check was, at some point, a sentence in a markdown file
that had quietly stopped being true: a `pytest tests/` command with no test
directory, an endpoint that returned 500, an event count off by a factor of four,
a monitoring list four scrapers behind reality.

Prose that nothing verifies decays. These are the checks that keep the
architecture and roadmap honest. See docs/ROADMAP.md item 8.
"""
import os
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def markdown_files():
    return [
        p for p in REPO.rglob("*.md")
        if not any(part in {".venv", ".git", ".pytest_cache", "node_modules"} for part in p.parts)
    ]


def test_all_markdown_links_resolve():
    """A doc set that cross-references itself is only useful if the links work."""
    broken = []
    for doc in markdown_files():
        for match in re.finditer(r"\[[^\]]+\]\(([^)#][^)]*?)(#[^)]*)?\)", doc.read_text()):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (doc.parent / target).resolve().exists():
                broken.append(f"{doc.relative_to(REPO)} -> {target}")
    assert not broken, "broken relative links:\n  " + "\n  ".join(broken)


def test_every_scraper_module_is_registered():
    """A scraper on disk that the registry does not list is dead code.

    Longfellow House was exactly this for months: 185 lines of working Playwright
    scraper that nothing ever called, producing zero events and raising no alarm.
    """
    from src.sources import SOURCES

    on_disk = {
        f[:-3] for f in os.listdir(REPO / "src" / "scrapers")
        if f.endswith(".py") and not f.startswith("__") and "base" not in f
    }
    registered = {s.module.rsplit(".", 1)[-1] for s in SOURCES if s.is_scraped}
    unregistered = on_disk - registered

    # google_sheets backs sync_user_events.py, not the scrape pipeline.
    assert unregistered <= {"google_sheets"}, (
        f"scraper modules that nothing runs: {sorted(unregistered)}. "
        "Add them to src/sources.py or delete them."
    )


def test_registry_names_match_what_scrapers_emit():
    """A Source.name that disagrees with its scraper's source_name silently
    unmonitors that source: events land under one name, monitoring watches another."""
    from src.sources import SOURCES

    mismatched = []
    for source in SOURCES:
        if not source.is_scraped:
            continue
        emitted = source.load().source_name
        if emitted != source.name:
            mismatched.append(f"{source.name!r} registered but scraper emits {emitted!r}")
    assert not mismatched, "\n  ".join(["registry/scraper name mismatch:"] + mismatched)


def test_monitoring_covers_every_registered_source():
    """ci_monitor must see everything the pipeline runs.

    This used to fail: REGISTERED_SOURCES was a second hand-kept copy of the
    source list and had drifted four scrapers behind scrape.py, leaving Harvard
    GSD, Museum of Science, Regent Theatre, and The Sinclair unmonitored. Both
    now derive from src/sources.py, so the drift is structurally impossible —
    this test is what keeps it that way.
    """
    from src.agents.ci_monitor import REGISTERED_SOURCES
    from src.sources import SOURCES

    unmonitored = {s.name for s in SOURCES} - set(REGISTERED_SOURCES)
    assert not unmonitored, f"sources with no freshness monitoring: {sorted(unmonitored)}"


@pytest.mark.parametrize("check", ["clock_stamped", "tz_aware", "timestamp_pileup"])
def test_stored_data_satisfies_documented_invariants(check):
    """The invariants in docs/ARCHITECTURE.md Layer 2, enforced against real data.

    These are absolute: they need no per-source baseline and hold for every event.
    """
    import json

    events = json.loads((REPO / "data" / "events.json").read_text())

    if check == "clock_stamped":
        # Real listings are on the minute. Sub-minute precision only comes from a
        # clock reading, which is what put 117 events on 2026-09-14.
        bad = [e for e in events if re.search(r"T\d{2}:\d{2}:(?!00)", e["start_datetime"])]
        assert not bad, f"{len(bad)} events carry a scrape timestamp, e.g. {bad[0]['start_datetime']}"

    elif check == "tz_aware":
        # Everything is naive Eastern; mixing raises TypeError on comparison.
        aware = re.compile(r"([+-]\d{2}:?\d{2}|Z)$")
        bad = [
            e[f] for e in events for f in ("start_datetime", "end_datetime")
            if isinstance(e.get(f), str) and aware.search(e[f])
        ]
        assert not bad, f"{len(bad)} tz-aware datetimes, e.g. {bad[0]}"

    elif check == "timestamp_pileup":
        # 8 is the highest legitimate per-source value observed; 117 was the bug.
        from collections import Counter
        worst = Counter((e["source_name"], e["start_datetime"]) for e in events).most_common(1)[0]
        (source, when), count = worst
        assert count <= 20, f"{count} events from {source} all start at {when} - date fallback?"
