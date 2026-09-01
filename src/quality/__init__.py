"""Layer 2 — the contract: what a valid event and a plausible run look like.

Two tiers, deliberately separate:

  invariants.py   absolute rules, no history needed, a violation means broken
  fingerprint.py  per-source shape compared to that source's own baseline

See docs/ARCHITECTURE.md "Layer 2 — Contract" for why the distinction is
load-bearing rather than stylistic.
"""
from src.quality.invariants import Violation, check_invariants, errors
from src.quality.fingerprint import (
    Drift,
    Fingerprint,
    check_drift,
    fingerprint_all,
    fingerprint_source,
    load_baselines,
    record,
)

__all__ = [
    "Violation", "check_invariants", "errors",
    "Drift", "Fingerprint", "check_drift", "fingerprint_all",
    "fingerprint_source", "load_baselines", "record",
]
