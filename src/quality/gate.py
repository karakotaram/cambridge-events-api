"""Layer 4 — the one decision between "the scraper finished" and "readers see it".

Before this existed, `scrape-events.yml` ran the scrape and unconditionally
committed and pushed whatever came out. That is how 117 events with fabricated
dates reached a reader who then had to email about it.

The gate asks one question: does this run satisfy the contract, relative to the
last known-good run? Pass and it publishes and becomes the new baseline. Fail and
it quarantines, opens an issue, and leaves production on yesterday's data.

Stale data is a far smaller harm than wrong data. A calendar a day behind is
mildly annoying. A calendar confidently showing the wrong day is what makes
someone stop trusting it.

Three deliberate safety valves:

  invariant/drift  the two tiers of the contract are enforced separately.
                   Invariants are absolute and proven clean against current
                   data, so they block from day one. Drift compares a source to
                   its own learned baseline, where a mistuned threshold would
                   block good data, so it reports until GATE_DRIFT=enforce.
  report mode      GATE_MODE=report evaluates and records but never blocks.
  force            an explicit override, because a legitimate large change (a
                   venue dropping its whole spring season at once) will
                   eventually trip the gate, and the fix for that must never be
                   "delete the gate".
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from src.quality.fingerprint import BASELINE_PATH, Drift, Fingerprint, check_drift
from src.quality.invariants import Violation, check_invariants

# GATE_MODE=report evaluates without blocking; enforce is the default.
MODE_ENV = "GATE_MODE"

# Catastrophic collapse is checked against the data being replaced, not against
# a learned baseline, so it needs no history and is never subject to GATE_DRIFT.
#
# This exists because a rehearsal of the gate caught it letting through a run
# that replaced 2,974 events with 232: every scraper had failed, only the
# always-preserved user submissions survived, and the fifteen "source
# disappeared" findings were drift — which was in report mode. Losing almost the
# whole calendar is the one outcome that must never depend on a tunable.
MIN_EVENTS_RATIO = 0.50      # of what is currently published
MIN_SOURCES_RATIO = 0.60     # of the sources currently contributing

# Invariants and drift are enforced separately, because they earn trust
# differently. An invariant is absolute, needs no history, and is proven clean
# against current data — there is nothing to tune, so it blocks from day one.
# Drift compares a source to its own learned baseline, and a mistuned threshold
# blocks good data, so it starts in report mode. Note that drift is already
# self-tuning: fingerprint.compare() stays silent until a source has
# MIN_RUNS_FOR_DRIFT runs of post-fix history, so the "tuning period" is built
# into the mechanism rather than bolted on with a calendar reminder.
DRIFT_ENV = "GATE_DRIFT"


@dataclass
class GateDecision:
    passed: bool
    mode: str                                     # enforce | report | force
    violations: list[Violation] = field(default_factory=list)
    drifts: list[Drift] = field(default_factory=list)
    fingerprints: dict[str, Fingerprint] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    drift_enforced: bool = False

    @property
    def blocking(self) -> bool:
        """True when this decision should actually stop a publish."""
        return not self.passed and self.mode == "enforce"

    @property
    def decision(self) -> str:
        if self.passed:
            return "pass"
        return "BLOCKED" if self.mode == "enforce" else f"fail({self.mode})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "mode": self.mode,
            "drift_enforced": self.drift_enforced,
            "passed": self.passed,
            "reasons": self.reasons,
            "violations": [
                {"rule": v.rule, "severity": v.severity, "source": v.source,
                 "detail": v.detail, "count": v.count, "sample": v.sample}
                for v in self.violations
            ],
            "drifts": [
                {"source": d.source, "metric": d.metric, "severity": d.severity,
                 "current": d.current, "baseline": d.baseline, "detail": d.detail}
                for d in self.drifts
            ],
        }

    def report(self) -> str:
        """Human- and agent-readable summary. This is what lands in the issue."""
        lines = [f"Gate: {self.decision}"]
        for reason in self.reasons:
            lines.append(f"  {reason}")
        blocking_v = [v for v in self.violations if v.severity == "error"]
        blocking_d = [d for d in self.drifts if d.severity == "error"]
        collapse = [r for r in self.reasons if "collapsed" in r or "vanished" in r]
        if collapse:
            lines.append("\nCatastrophic collapse (absolute, never tunable):")
            lines += [f"  {c}" for c in collapse]
        if blocking_v:
            lines.append("\nInvariant violations (absolute rules, always blocking):")
            lines += [f"  {v}" for v in blocking_v]
        if blocking_d:
            suffix = "" if self.drift_enforced else " — reported only, set GATE_DRIFT=enforce to block"
            lines.append(f"\nDrift from each source's own baseline{suffix}:")
            lines += [f"  {d}" for d in blocking_d]
        warnings = ([v for v in self.violations if v.severity == "warning"]
                    + [d for d in self.drifts if d.severity == "warning"])
        if warnings:
            lines.append("\nWarnings (not blocking):")
            lines += [f"  {w}" for w in warnings]
        return "\n".join(lines)


def resolve_mode(*, force: bool = False, mode: Optional[str] = None) -> str:
    if force:
        return "force"
    return (mode or os.environ.get(MODE_ENV, "enforce")).lower()


def drift_enforced(drift_mode: Optional[str] = None) -> bool:
    """Whether drift errors may block a publish. See DRIFT_ENV above."""
    return (drift_mode or os.environ.get(DRIFT_ENV, "report")).lower() == "enforce"


def check_collapse(events: list[dict], previous: Optional[list[dict]]) -> list[str]:
    """Absolute stop: is this run about to delete most of the calendar?

    Compares the publish set against what it would overwrite. Independent of
    fingerprints, baselines, and GATE_DRIFT, because "we lost 90% of the data"
    is never a threshold judgement.
    """
    if not previous:
        return []

    reasons = []
    if len(events) < len(previous) * MIN_EVENTS_RATIO:
        reasons.append(
            f"event count collapsed: {len(events)} vs {len(previous)} currently published "
            f"({len(events) / len(previous):.0%}, floor {MIN_EVENTS_RATIO:.0%})")

    now_sources = {e.get("source_name") for e in events if e.get("source_name")}
    was_sources = {e.get("source_name") for e in previous if e.get("source_name")}
    if was_sources and len(now_sources) < len(was_sources) * MIN_SOURCES_RATIO:
        lost = sorted(was_sources - now_sources)
        reasons.append(
            f"{len(lost)} of {len(was_sources)} sources vanished "
            f"(floor {MIN_SOURCES_RATIO:.0%}): {', '.join(lost[:8])}"
            + (" ..." if len(lost) > 8 else ""))
    return reasons


def evaluate(events: Iterable[dict], *,
             previous: Optional[Iterable[dict]] = None,
             baseline_path: Path | str = BASELINE_PATH,
             force: bool = False,
             mode: Optional[str] = None,
             drift_mode: Optional[str] = None) -> GateDecision:
    """Decide whether a set of events may be published.

    `previous` is the data this run would overwrite; pass it so that a
    catastrophic collapse can be caught without any baseline.
    """
    events = list(events)
    previous = list(previous) if previous is not None else None
    resolved = resolve_mode(force=force, mode=mode)
    enforce_drift = drift_enforced(drift_mode)

    violations = check_invariants(events)
    fingerprints, drifts = check_drift(events, path=baseline_path)

    noted: list[str] = []
    blocking_v = [v for v in violations if v.severity == "error"]
    blocking_d = [d for d in drifts if d.severity == "error"]

    reasons: list[str] = []
    if not events:
        reasons.append("the run produced no events at all")
    reasons += check_collapse(events, previous)
    if blocking_v:
        reasons.append(f"{len(blocking_v)} invariant violation(s)")
    if blocking_d:
        if enforce_drift:
            reasons.append(f"{len(blocking_d)} source(s) drifted beyond tolerance")
        else:
            # Recorded and reported, but not a reason to block.
            noted.append(f"{len(blocking_d)} source(s) drifted beyond tolerance "
                         f"— not blocking, set {DRIFT_ENV}=enforce to change that")

    passed = not reasons
    if resolved == "force" and not passed:
        reasons.append("overridden by --force")
        passed = True
    reasons += noted

    return GateDecision(passed=passed, mode=resolved, violations=violations,
                        drifts=drifts, fingerprints=fingerprints, reasons=reasons,
                        drift_enforced=enforce_drift)


def quarantine(events: Iterable[dict], decision: GateDecision, run_id: str,
               root: Path | str | None = None) -> Path:
    """Write a rejected run to disk rather than discarding it.

    The bad run is the evidence. Throwing it away means the next person
    diagnoses from the symptom instead of the artefact.
    """
    import json

    root = Path(root) if root else Path(__file__).resolve().parents[2] / "data" / "quarantine"
    target = root / run_id
    target.mkdir(parents=True, exist_ok=True)
    with open(target / "events.json", "w") as f:
        json.dump(list(events), f, indent=2, default=str)
    with open(target / "gate.json", "w") as f:
        json.dump(decision.to_dict(), f, indent=2, default=str)
    with open(target / "report.txt", "w") as f:
        f.write(decision.report() + "\n")
    return target
