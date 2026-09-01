"""Per-source shape, and drift against a source's own history — the second tier
of layer 2.

The health monitor watched one number, event count, and on 2026-08-31 it scored
a badly broken Cambridge.gov run as healthy: 359 events against a 250.6 average,
143% of normal. The count went *up* while 117 events piled onto a single
fabricated timestamp. Volume is the signal that moves least during a real
failure.

A fingerprint is the shape of a source's output: how far ahead it reaches, how
concentrated its dates are, how varied its titles and venues and images are. The
same run that looked healthy by count looked obviously wrong by shape.

**Thresholds are source-relative, and that is the whole design.** Run naive
global rules over today's healthy data and they flag nine of twenty-four sources
— Brattle Theatre for having one venue (it is one cinema), American Repertory
Theater for 11 distinct titles across 156 events (11 productions, many
performances each), City of Cambridge for repeating titles (weekly story times).
All correct, all healthy. A monitor that cries wolf is worse than no monitor,
because its reader learns to skip it. So every check here compares a source to
its own baseline and alerts on *change*, not on absolute value.

The baseline is also what makes the system accretive: the longer a source runs,
the better this knows what normal looks like for it.
"""
from __future__ import annotations

import json
import statistics
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from dateutil import parser as date_parser

BASELINE_PATH = Path(__file__).resolve().parents[2] / "data" / "fingerprints.json"

# 30, not the 5 the old health monitor kept. Cambridge.gov degraded across
# several days and its broken state became the baseline before anything noticed.
MAX_HISTORY = 30

# Below this, a source's shape metrics are too noisy to say anything about.
MIN_EVENTS_FOR_DRIFT = 10

# Below this many past runs, there is no baseline worth comparing against.
MIN_RUNS_FOR_DRIFT = 3


@dataclass
class Fingerprint:
    """The shape of one source's output in one run."""

    source: str
    events: int = 0
    date_span_days: int = 0
    max_events_one_day: int = 0
    max_events_one_timestamp: int = 0
    distinct_titles_ratio: float = 0.0
    distinct_venues: int = 0
    distinct_images_ratio: float = 0.0
    median_description_len: int = 0
    null_venue_rate: float = 0.0
    latest_start: Optional[str] = None
    earliest_start: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


def _parse(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return date_parser.parse(str(value))
    except (ValueError, OverflowError, TypeError):
        return None


def _ratio(numerator: int, denominator: int) -> float:
    return round(numerator / denominator, 4) if denominator else 0.0


def fingerprint_source(source: str, events: list[dict]) -> Fingerprint:
    """Measure one source's shape. Events must all be from that source."""
    fp = Fingerprint(source=source, events=len(events))
    if not events:
        return fp

    starts = [_parse(e.get("start_datetime")) for e in events]
    starts = [s.replace(tzinfo=None) if s and s.tzinfo else s for s in starts]
    starts = [s for s in starts if s]

    if starts:
        fp.earliest_start = min(starts).date().isoformat()
        fp.latest_start = max(starts).date().isoformat()
        fp.date_span_days = (max(starts).date() - min(starts).date()).days + 1
        fp.max_events_one_day = Counter(s.date() for s in starts).most_common(1)[0][1]
        fp.max_events_one_timestamp = Counter(starts).most_common(1)[0][1]

    fp.distinct_titles_ratio = _ratio(len({e.get("title") for e in events}), len(events))

    venues = [e.get("venue_name") for e in events]
    fp.distinct_venues = len({v for v in venues if v})
    fp.null_venue_rate = _ratio(sum(1 for v in venues if not v), len(events))

    images = [e.get("image_url") for e in events if e.get("image_url")]
    fp.distinct_images_ratio = _ratio(len(set(images)), len(images))

    lengths = sorted(len(e.get("description") or "") for e in events)
    fp.median_description_len = lengths[len(lengths) // 2]

    return fp


def fingerprint_all(events: Iterable[dict]) -> dict[str, Fingerprint]:
    """Fingerprint every source present in a set of events."""
    by_source: dict[str, list[dict]] = {}
    for e in events:
        by_source.setdefault(e.get("source_name") or "Unknown", []).append(e)
    return {name: fingerprint_source(name, evs) for name, evs in sorted(by_source.items())}


# --------------------------------------------------------------------------- #
# Drift
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class Drift:
    source: str
    metric: str
    current: float
    baseline: float
    detail: str
    severity: str = "warning"   # "error" blocks a run, "warning" reports

    def __str__(self) -> str:
        return (f"[{self.severity.upper()}] {self.source}: {self.metric} "
                f"{self.current:g} vs baseline {self.baseline:g} — {self.detail}")


# metric, direction, ratio at which it warns, ratio at which it errors, phrasing.
# "down" means a fall below baseline is suspicious; "up" means a rise is.
_RULES: tuple[tuple[str, str, float, float, str], ...] = (
    ("events",                "down", 0.70, 0.50, "source is returning much less than usual"),
    ("events",                "up",   2.00, 3.00, "source suddenly returning far more — duplicate explosion?"),
    ("date_span_days",        "down", 0.70, 0.50, "reaching less far ahead — pagination or a crash mid-run"),
    ("max_events_one_day",    "up",   2.00, 2.50, "dates collapsing onto one day"),
    ("distinct_titles_ratio", "down", 0.70, 0.50, "titles repeating — selector may have drifted"),
    ("distinct_images_ratio", "down", 0.60, 0.40, "images collapsing to a placeholder"),
    ("median_description_len", "down", 0.50, 0.30, "descriptions thinning out"),
)


def _baseline_of(history: list[dict], metric: str) -> Optional[float]:
    values = [run[metric] for run in history if run.get(metric) is not None]
    return statistics.median(values) if values else None


def compare(current: Fingerprint, history: list[dict]) -> list[Drift]:
    """Compare one source's fingerprint to its own recent history.

    Silent until there is enough history and enough events to say anything —
    a monitor that fires on thin data is a monitor nobody reads.
    """
    drifts: list[Drift] = []
    if len(history) < MIN_RUNS_FOR_DRIFT:
        return drifts

    baseline_events = _baseline_of(history, "events") or 0
    if current.events < MIN_EVENTS_FOR_DRIFT and baseline_events < MIN_EVENTS_FOR_DRIFT:
        return drifts

    # A source that vanished entirely is unambiguous; report it and stop, since
    # every ratio below would also fire and bury the actual finding.
    if current.events == 0 and baseline_events >= MIN_EVENTS_FOR_DRIFT:
        return [Drift(current.source, "events", 0, baseline_events,
                      "returned nothing — scraper is broken", "error")]

    for metric, direction, warn_at, error_at, detail in _RULES:
        base = _baseline_of(history, metric)
        value = getattr(current, metric, None)
        if base is None or value is None or base == 0:
            continue
        ratio = value / base
        if direction == "down" and ratio < warn_at:
            severity = "error" if ratio < error_at else "warning"
        elif direction == "up" and ratio > warn_at:
            severity = "error" if ratio > error_at else "warning"
        else:
            continue
        drifts.append(Drift(current.source, metric, round(value, 4), round(base, 4),
                            f"{detail} ({ratio:.0%} of normal)", severity))

    # Staleness: the source still returns events, but has stopped adding new ones.
    base_latest = [run.get("latest_start") for run in history if run.get("latest_start")]
    if current.latest_start and base_latest and current.latest_start < max(base_latest):
        drifts.append(Drift(current.source, "latest_start", 0, 0,
                            f"furthest-out event moved backwards "
                            f"({max(base_latest)} -> {current.latest_start}) — source may have stopped publishing",
                            "warning"))

    return drifts


# --------------------------------------------------------------------------- #
# Baseline persistence
# --------------------------------------------------------------------------- #

def _registered_names() -> Optional[set]:
    """Names the registry still lists, or None if it cannot be read.

    Returning None rather than an empty set matters: a broken import must not
    silently disable every disappearance check.
    """
    try:
        from src.sources import SOURCES
        return {s.name for s in SOURCES}
    except Exception:
        return None


def load_baselines(path: Path | str = BASELINE_PATH) -> dict[str, list[dict]]:
    path = Path(path)
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f).get("sources", {})
    except (json.JSONDecodeError, OSError):
        return {}


def record(fingerprints: dict[str, Fingerprint], *, path: Path | str = BASELINE_PATH,
           run_id: Optional[str] = None) -> None:
    """Append this run's fingerprints to each source's history.

    Only call this for a run that passed the gate. Recording a bad run poisons
    the baseline, which is precisely how a slow degradation becomes normal.
    """
    path = Path(path)
    baselines = load_baselines(path)
    for name, fp in fingerprints.items():
        entry = fp.to_dict()
        entry["run_id"] = run_id
        baselines.setdefault(name, []).append(entry)
        baselines[name] = baselines[name][-MAX_HISTORY:]
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump({"sources": baselines}, f, indent=2, default=str)


def reset_baseline(sources: Iterable[str], *, path: Path | str = BASELINE_PATH) -> dict[str, int]:
    """Forget a source's history so drift relearns it from scratch.

    Necessary whenever a scraper is fixed. A source that was returning 4 events
    because it was broken has a baseline of 4, and the corrected 132 then reads
    as a 3300% duplicate explosion for as many runs as it takes to age out. The
    pre-fix history describes a broken system and should not define normal.

    Returns how many runs were discarded per source.
    """
    path = Path(path)
    baselines = load_baselines(path)
    dropped = {}
    for name in sources:
        if name in baselines:
            dropped[name] = len(baselines.pop(name))
    if dropped:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump({"sources": baselines}, f, indent=2, default=str)
    return dropped


def check_drift(events: Iterable[dict], *,
                path: Path | str = BASELINE_PATH) -> tuple[dict[str, Fingerprint], list[Drift]]:
    """Fingerprint everything and compare each source to its own baseline."""
    fingerprints = fingerprint_all(events)
    baselines = load_baselines(path)
    drifts: list[Drift] = []
    for name, fp in fingerprints.items():
        drifts.extend(compare(fp, baselines.get(name, [])))

    # A source with history that produced nothing at all this run has no
    # fingerprint to compare, so catch it here — but only if we still scrape it.
    # A retired source has no events by design, and reporting that forever is
    # noise the reader learns to skip.
    registered = _registered_names()
    for name, history in baselines.items():
        if name in fingerprints or len(history) < MIN_RUNS_FOR_DRIFT:
            continue
        if registered is not None and name not in registered:
            continue
        base = _baseline_of(history, "events") or 0
        if base >= MIN_EVENTS_FOR_DRIFT:
            drifts.append(Drift(name, "events", 0, base,
                                "source disappeared from the data entirely", "error"))

    order = {"error": 0, "warning": 1}
    return fingerprints, sorted(drifts, key=lambda d: (order[d.severity], d.source))
