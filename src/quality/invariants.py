"""Absolute rules every event must satisfy — the first tier of layer 2.

An invariant holds for every event from every source, needs no history, and is
never a judgement call. If one is violated, something is broken; there is no
"but this source is different."

That distinction matters. The other tier — drift, in `fingerprint.py` — must be
measured against each source's own baseline, because global shape thresholds
produce mostly false positives here (Brattle Theatre legitimately has one venue;
it is one cinema). Invariants are the rules where a global threshold is correct.

Each rule below is here because it fired in production, or would have.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable, Literal, Optional

from dateutil import parser as date_parser

# 8 events from one source sharing an exact start time is the highest legitimate
# value observed (Cambridge library programmes at 6pm across branches). The
# 2026-08-31 failure produced 117. 20 leaves generous headroom.
MAX_EVENTS_PER_TIMESTAMP = 20

# The absolute cap above is blind to small sources. Cambridge Public Library
# fabricated `datetime.now().replace(hour=10, minute=0, second=0)` for every
# event it could not parse — 17 events on one timestamp, under the cap, with the
# seconds zeroed so the clock_stamped rule could not see it either. A share of
# the source's own output catches that regardless of size.
UNIFORM_TIMESTAMP_SHARE = 0.70
UNIFORM_TIMESTAMP_MIN_EVENTS = 8

# Deliberately wide. The rule catches *impossible* dates — a year-parsing bug
# putting an event in 2025 or 2126 — not merely stale ones. data/events.json
# legitimately holds 258 past events: User Submitted is never re-scraped, and
# CI-blocked sources keep their listings until scrape_local.py runs, so they age
# past any tight bound as a matter of course. A 30-day window flagged all 258 and
# would have taught its reader to ignore this check entirely.
#
# Freshness is a drift concern (latest_start, date_span_days), and rejecting
# newly scraped stale events is EventValidator's job at scrape time.
MAX_PAST_DAYS = 730
MAX_FUTURE_DAYS = 730

# Selector drift onto page chrome produces titles like these.
NAV_TITLE_PATTERNS = (
    r"^(home|menu|search|login|sign in|subscribe|newsletter|cookie|privacy)$",
    r"^(read more|learn more|click here|view all|see all|load more|next|previous)$",
    r"^(skip to|jump to)\b",
    r"^\W*$",
)

_AWARE = re.compile(r"([+-]\d{2}:?\d{2}|Z)$")

Severity = Literal["error", "warning"]


@dataclass(frozen=True)
class Violation:
    rule: str
    severity: Severity
    detail: str
    source: Optional[str] = None
    count: int = 1
    sample: Optional[str] = None

    def __str__(self) -> str:
        where = f"{self.source}: " if self.source else ""
        tail = f"  e.g. {self.sample}" if self.sample else ""
        return f"[{self.severity.upper()}] {where}{self.detail}{tail}"


def _parse(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return date_parser.parse(str(value))
    except (ValueError, OverflowError, TypeError):
        return None


def check_invariants(events: Iterable[dict], *, now: Optional[datetime] = None) -> list[Violation]:
    """Every absolute rule, checked against a set of events.

    Takes plain dicts so it works equally on a scraper's output, on
    data/events.json, and on a quarantined run.
    """
    events = list(events)
    now = now or datetime.now()
    violations: list[Violation] = []

    def add(rule, severity, detail, source=None, count=1, sample=None):
        violations.append(Violation(rule, severity, detail, source, count, sample))

    # --- required fields ----------------------------------------------------
    missing = defaultdict(list)
    for e in events:
        for f in ("title", "description", "start_datetime", "source_url", "source_name"):
            if not e.get(f):
                missing[f].append(e)
    for field, bad in missing.items():
        add("missing_required", "error", f"{len(bad)} events missing {field}",
            bad[0].get("source_name"), len(bad), (bad[0].get("title") or "")[:60])

    # --- id uniqueness ------------------------------------------------------
    # Stable ids are only useful if they are unique; a collision means two
    # occurrences are indistinguishable and one will shadow the other.
    dupes = {i: n for i, n in Counter(e.get("id") for e in events if e.get("id")).items() if n > 1}
    if dupes:
        worst = max(dupes, key=dupes.get)
        add("duplicate_id", "error", f"{len(dupes)} ids used by more than one event",
            None, len(dupes), f"{worst} x{dupes[worst]}")

    # --- clock-stamped start times ------------------------------------------
    # Published listings are on the minute. Sub-minute precision means a scraper
    # substituted datetime.now() for a date it could not read. This is the
    # signature of the 2026-08-31 failure: 117 events at 13:29:26.288025.
    stamped = defaultdict(list)
    for e in events:
        dt = _parse(e.get("start_datetime"))
        if dt and (dt.second or dt.microsecond):
            stamped[e.get("source_name")].append(e)
    for source, bad in stamped.items():
        add("clock_stamped", "error",
            f"{len(bad)} start times carry seconds/microseconds — a clock reading, not a listing",
            source, len(bad), str(bad[0].get("start_datetime")))

    # --- timezone ------------------------------------------------------------
    aware = defaultdict(list)
    for e in events:
        for f in ("start_datetime", "end_datetime"):
            v = e.get(f)
            if isinstance(v, str) and _AWARE.search(v):
                aware[e.get("source_name")].append(v)
            elif isinstance(v, datetime) and v.tzinfo is not None:
                aware[e.get("source_name")].append(v.isoformat())
    for source, bad in aware.items():
        add("tz_aware", "error",
            f"{len(bad)} tz-aware datetimes — everything is stored naive Eastern",
            source, len(bad), bad[0])

    # --- timestamp pileups ---------------------------------------------------
    per_ts = Counter((e.get("source_name"), str(e.get("start_datetime"))) for e in events)
    for (source, when), n in per_ts.items():
        if n > MAX_EVENTS_PER_TIMESTAMP:
            add("timestamp_pileup", "error",
                f"{n} events share the exact start {when} (limit {MAX_EVENTS_PER_TIMESTAMP}) "
                "— almost always a date fallback",
                source, n, when)

    # --- a source whose output is mostly one timestamp -----------------------
    per_source = Counter(e.get("source_name") for e in events)
    for (source, when), n in per_ts.items():
        total = per_source[source]
        if (total >= UNIFORM_TIMESTAMP_MIN_EVENTS
                and n / total > UNIFORM_TIMESTAMP_SHARE
                and n <= MAX_EVENTS_PER_TIMESTAMP):   # the cap already reported it
            add("uniform_timestamp", "error",
                f"{n} of {total} events ({n / total:.0%}) share the exact start {when} "
                "— a real listing page does not schedule everything at one moment",
                source, n, when)

    # --- plausible date range ------------------------------------------------
    out_of_range = defaultdict(list)
    for e in events:
        dt = _parse(e.get("start_datetime"))
        if dt is None:
            continue
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        if dt < now - timedelta(days=MAX_PAST_DAYS) or dt > now + timedelta(days=MAX_FUTURE_DAYS):
            out_of_range[e.get("source_name")].append(e)
    for source, bad in out_of_range.items():
        add("date_out_of_range", "error",
            f"{len(bad)} events dated outside [-{MAX_PAST_DAYS}d, +{MAX_FUTURE_DAYS}d] — impossible date, usually a year-parsing bug",
            source, len(bad), str(bad[0].get("start_datetime")))

    # --- navigation chrome in titles -----------------------------------------
    nav = defaultdict(list)
    for e in events:
        title = (e.get("title") or "").strip().lower()
        if any(re.match(p, title) for p in NAV_TITLE_PATTERNS):
            nav[e.get("source_name")].append(e)
    for source, bad in nav.items():
        add("nav_title", "error",
            f"{len(bad)} titles look like page chrome — selector drifted off the listing",
            source, len(bad), (bad[0].get("title") or "")[:60])

    order = {"error": 0, "warning": 1}
    return sorted(violations, key=lambda v: (order[v.severity], -v.count))


def errors(violations: Iterable[Violation]) -> list[Violation]:
    return [v for v in violations if v.severity == "error"]
