"""The documentation must not be allowed to rot into a trap.

Every claim these tests check was, at some point, a sentence in a markdown file
that had quietly stopped being true: a `pytest tests/` command with no test
directory, an endpoint that returned 500, an event count off by a factor of four,
a monitoring list four scrapers behind reality.

Prose that nothing verifies decays. These are the checks that keep the
architecture and roadmap honest. See docs/ROADMAP.md item 8.
"""
import importlib
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


def registered_source_names():
    """Instantiate everything scrape.py registers and collect its source_name."""
    scrape_src = (REPO / "scrape.py").read_text()
    names = {}
    for cls_name in re.findall(r"orchestrator\.register_scraper\((\w+)\(\)\)", scrape_src):
        module_match = re.search(rf"from (\S+) import .*\b{cls_name}\b", scrape_src)
        assert module_match, f"{cls_name} is registered but never imported"
        cls = getattr(importlib.import_module(module_match.group(1)), cls_name)
        names[cls().source_name] = cls_name
    return names


def test_every_scraper_module_is_registered():
    """A scraper on disk that nothing runs is dead code or a forgotten wiring step."""
    on_disk = {
        f[:-3] for f in os.listdir(REPO / "src" / "scrapers")
        if f.endswith(".py") and not f.startswith("__") and "base" not in f
    }
    imported = set(re.findall(r"from src\.scrapers\.(\w+) import", (REPO / "scrape.py").read_text()))
    unregistered = on_disk - imported

    # google_sheets is driven by sync_user_events.py, not the scrape pipeline.
    assert unregistered <= {"google_sheets", "longfellow_house"}, (
        f"scraper modules that nothing runs: {sorted(unregistered)}. "
        "Register them in scrape.py or delete them."
    )


def test_monitoring_covers_every_registered_source():
    """ci_monitor.REGISTERED_SOURCES and scrape.py are two hand-kept copies of one
    fact. They have drifted before. This test pins the drift so that fixing it, or
    making it worse, forces the documentation to be updated in the same commit.

    docs/ROADMAP.md item 1 deletes one of the two lists; when that lands, this
    assertion becomes `assert not unmonitored` and the doc references go away.
    """
    from src.agents.ci_monitor import REGISTERED_SOURCES

    registered = set(registered_source_names())
    monitored = set(REGISTERED_SOURCES)
    unmonitored = registered - monitored

    documented = {"Harvard GSD", "Museum of Science", "Regent Theatre", "The Sinclair"}
    assert unmonitored == documented, (
        f"monitoring drift changed.\n"
        f"  unmonitored now: {sorted(unmonitored)}\n"
        f"  documented:      {sorted(documented)}\n"
        "If you fixed this, update CLAUDE.md, docs/ARCHITECTURE.md (Layer 0 and the "
        "deviation table), docs/ROADMAP.md item 1, docs/OPERATIONS.md, and "
        "src/agents/README.md, then relax this assertion."
    )


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
