"""Layer 3 — an immutable record of what one scrape actually did.

The 2026-08-31 root cause was recovered by noticing that `13:29:26.288025` plus
fourteen days equals Sept 14 — arithmetic on a corrupted value, because nothing
about the run survived it. `data/scraper_health.json` held five runs of bare
per-source counts and nothing else: no per-scraper timing, no failure reasons, no
record of what validation threw away, no shape.

A run record is the evidence. Debugging should be reading, not archaeology.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

RUNS_DIR = Path(__file__).resolve().parents[2] / "data" / "runs"

# Small files; 90 days of them stays greppable and costs almost nothing.
MAX_RUNS = 90


def git_sha() -> Optional[str]:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=5).stdout.strip() or None
    except Exception:
        return None


@dataclass
class ScraperResult:
    """What one scraper did. `returned` is before validation and dedup."""
    source: str
    status: str                       # ok | failed | skipped
    returned: int = 0
    duration_s: float = 0.0
    error: Optional[str] = None


@dataclass
class RunRecord:
    run_id: str
    started_at: str
    finished_at: Optional[str] = None
    duration_s: float = 0.0
    git_sha: Optional[str] = field(default_factory=git_sha)
    is_ci: bool = False

    scrapers: list[ScraperResult] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)      # scraped/validated/deduped/final
    rejected: dict[str, int] = field(default_factory=dict)    # validator reason -> count
    fingerprints: dict[str, dict] = field(default_factory=dict)
    gate: dict[str, Any] = field(default_factory=dict)
    diff: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def start(cls, *, is_ci: bool = False) -> "RunRecord":
        """Run ids are UTC, and the trailing Z means it.

        They were local time with a Z pasted on, which is only harmless while
        every run happens in one timezone. CI runs in UTC and a developer does
        not, so `data/runs/` ended up holding two clocks under one naming
        scheme: a 09:44 EDT run sorted *before* an 11:32 UTC one that had
        actually happened two hours earlier. `cal doctor` reads the newest run
        by id, so it reported a stale failure.
        """
        now = datetime.now(timezone.utc)
        return cls(run_id=now.strftime("%Y-%m-%dT%H-%M-%SZ"),
                   started_at=now.isoformat(timespec="seconds"), is_ci=is_ci)

    def finish(self) -> None:
        end = datetime.now(timezone.utc)
        self.finished_at = end.isoformat(timespec="seconds")
        self.duration_s = round((end - datetime.fromisoformat(self.started_at)).total_seconds(), 1)

    def add_scraper(self, result: ScraperResult) -> None:
        self.scrapers.append(result)

    def to_dict(self) -> dict:
        return asdict(self)

    def save(self, directory: Path | str = RUNS_DIR) -> Path:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.run_id}.json"
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
        prune(directory)
        return path

    # -- reading back ------------------------------------------------------
    @property
    def failed_scrapers(self) -> list[ScraperResult]:
        return [s for s in self.scrapers if s.status == "failed"]

    def summary(self) -> str:
        ok = sum(1 for s in self.scrapers if s.status == "ok")
        decision = self.gate.get("decision", "?")
        return (f"{self.run_id}  {decision:<9} {self.counts.get('final', 0):>5} events  "
                f"{ok}/{len(self.scrapers)} scrapers  {self.duration_s:.0f}s")


def prune(directory: Path | str = RUNS_DIR, keep: int = MAX_RUNS) -> int:
    """Keep the most recent `keep` run records. Returns how many were removed."""
    directory = Path(directory)
    records = sorted(directory.glob("*.json"))
    stale = records[:-keep] if len(records) > keep else []
    for path in stale:
        path.unlink()
    return len(stale)


def load(run_id: str, directory: Path | str = RUNS_DIR) -> dict:
    with open(Path(directory) / f"{run_id}.json") as f:
        return json.load(f)


def recent(limit: int = 20, directory: Path | str = RUNS_DIR) -> list[dict]:
    """Most recent run records, newest first."""
    out = []
    for path in sorted(Path(directory).glob("*.json"), reverse=True)[:limit]:
        try:
            with open(path) as f:
                out.append(json.load(f))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def latest(directory: Path | str = RUNS_DIR) -> Optional[dict]:
    found = recent(1, directory)
    return found[0] if found else None
