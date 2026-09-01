"""Reading and writing data/events.json.

One place decides the on-disk order, because that order is load-bearing. Combined
with the content-derived ids from `event_identity()`, a deterministic order turns
the nightly `git diff data/events.json` into a readable changelog: events that
were added, events that were removed, fields that changed, and nothing else.

Before both of those existed, every scrape rewrote all ~92,000 lines with fresh
UUIDs, which is why `.git` reached 136 MB against an 18 MB working tree and why
nobody could answer "what did last night's scrape actually change?"
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

EVENTS_PATH = Path(__file__).resolve().parents[2] / "data" / "events.json"


def _sort_key(event: dict) -> tuple:
    """Chronological, then by id so equal timestamps keep a fixed order."""
    return (str(event.get("start_datetime") or ""), str(event.get("id") or ""))


def sort_events(events: Iterable[dict]) -> list[dict]:
    """Canonical on-disk order for events.json."""
    return sorted(events, key=_sort_key)


def load_events(path: Path | str = EVENTS_PATH) -> list[dict]:
    """Read events.json as plain dicts. Missing file reads as empty."""
    path = Path(path)
    if not path.exists():
        return []
    with open(path) as f:
        data = json.load(f)
    # Historically the file has been both a bare list and {"events": [...]}
    return data["events"] if isinstance(data, dict) else data


def write_events(events: Iterable[dict], path: Path | str = EVENTS_PATH) -> int:
    """Write events.json in canonical order. Returns the count written."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = sort_events(events)
    with open(path, "w") as f:
        json.dump(ordered, f, indent=2, default=str)
    return len(ordered)


def diff_events(before: Iterable[dict], after: Iterable[dict]) -> dict[str, Any]:
    """What changed between two snapshots, keyed by stable event id.

    Only meaningful because ids survive a scrape. With the old UUIDs every event
    would show up as both removed and added.
    """
    a = {e["id"]: e for e in before if e.get("id")}
    b = {e["id"]: e for e in after if e.get("id")}

    tracked = ("title", "start_datetime", "end_datetime", "venue_name",
               "category", "cost", "image_url", "description")
    changed = []
    for event_id in a.keys() & b.keys():
        fields = [f for f in tracked if a[event_id].get(f) != b[event_id].get(f)]
        if fields:
            changed.append({"id": event_id, "title": b[event_id].get("title"),
                            "source": b[event_id].get("source_name"), "fields": fields})

    return {
        "added": [b[i] for i in b.keys() - a.keys()],
        "removed": [a[i] for i in a.keys() - b.keys()],
        "changed": changed,
        "unchanged": len(a.keys() & b.keys()) - len(changed),
    }
