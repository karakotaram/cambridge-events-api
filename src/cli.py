"""`cal` — one verb per layer of the system.

Diagnosing the 2026-08-31 incident took: read CLAUDE.md for the API URL, curl
/stats (500), fall back to /events/slim, pipe it through a hand-written Counter,
notice the microsecond timestamps, do arithmetic on them to find the fallback,
discover the local checkout was 26 commits stale, then write a throwaway repair
script in /tmp — which is gone, so the next person writes it again.

An hour of work and nothing accreted. That is what this fixes.

    cal doctor                    what is wrong right now?              (all layers)
    cal sources                   what do we scrape, and is it fresh?   (layer 0)
    cal scrape <source>           what would this scraper produce?      (layer 1)
    cal check                     invariants and drift on current state (layer 2)
    cal runs / cal run <id>       what did recent scrapes do?           (layer 3)
    cal diff                      what changed vs. last run or prod?    (layer 5)
    cal repair <source>           re-scrape one source and splice it in (layer 5)

Run as `python -m src.cli <verb>`, or add the `cal` alias from docs/OPERATIONS.md.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from src.sources import BY_NAME, SOURCES

REPO = Path(__file__).resolve().parents[1]
LIVE_API = "https://web-production-00281.up.railway.app"

OK, WARN, BAD, DOT = "✓", "!", "✗", "·"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #

def _events(path: Optional[str] = None) -> list[dict]:
    from src.utils.storage import load_events
    return load_events(path) if path else load_events()


def _git(*args: str) -> str:
    try:
        return subprocess.run(["git", *args], cwd=REPO, capture_output=True,
                              text=True, timeout=20).stdout.strip()
    except Exception:
        return ""


def _fetch_live(path: str, timeout: int = 25):
    import urllib.request
    with urllib.request.urlopen(f"{LIVE_API}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode())


def _rule(title: str) -> None:
    print(f"\n{title}\n{'-' * len(title)}")


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #

def cmd_doctor(args) -> int:
    """The one command worth having if only one gets built."""
    from src.quality import check_drift, check_invariants
    from src.quality.run_record import latest

    findings: list[tuple[str, str]] = []

    def note(level, message):
        findings.append((level, message))

    events = _events()
    print(f"cal doctor {DOT} {len(events)} events in data/events.json")

    # -- layer 2: is the data itself sound? ---------------------------------
    for v in check_invariants(events):
        note(BAD if v.severity == "error" else WARN,
             f"{v.source or 'data'}: {v.detail}" + (f"  e.g. {v.sample}" if v.sample else ""))

    fingerprints, drifts = check_drift(events)
    for d in drifts:
        note(BAD if d.severity == "error" else WARN,
             f"{d.source}: {d.metric} {d.current:g} vs baseline {d.baseline:g} — {d.detail}")

    # -- is the checkout current? this cost an hour once --------------------
    _git("fetch", "--quiet", "origin")
    behind = _git("rev-list", "--count", "HEAD..origin/main")
    if behind and behind != "0":
        note(WARN, f"local checkout is {behind} commits behind origin/main — "
                   "data/events.json may not be what production serves")
    dirty = _git("status", "--porcelain", "--", "data/events.json")
    if dirty:
        note(WARN, "data/events.json has uncommitted changes")

    # -- layer 0: does monitoring see everything we run? --------------------
    try:
        from src.agents.ci_monitor import REGISTERED_SOURCES
        unmonitored = {s.name for s in SOURCES} - set(REGISTERED_SOURCES)
        if unmonitored:
            note(BAD, f"unmonitored sources: {', '.join(sorted(unmonitored))}")
    except Exception as e:
        note(WARN, f"could not check monitoring coverage: {e}")

    # -- sources present in the registry but absent from the data -----------
    present = {e.get("source_name") for e in events}
    missing = sorted({s.name for s in SOURCES} - present)
    if missing:
        note(WARN, f"{len(missing)} registered source(s) contribute no events: {', '.join(missing)}")

    # -- layer 3: how did the last run go? ----------------------------------
    last = latest()
    if last is None:
        note(WARN, "no run records yet — data/runs/ is empty")
    else:
        decision = last.get("gate", {}).get("decision", "?")
        if decision != "pass":
            note(BAD, f"last run {last['run_id']} gate={decision}")
        failed = [s["source"] for s in last.get("scrapers", []) if s.get("status") == "failed"]
        if failed:
            note(BAD, f"last run: {len(failed)} scraper(s) failed: {', '.join(failed[:5])}")
        started = datetime.fromisoformat(last["started_at"])
        reference = datetime.now(started.tzinfo) if started.tzinfo else datetime.now()
        age = reference - started
        if age > timedelta(days=2):
            note(WARN, f"last run was {age.days} days ago ({last['run_id']})")

    # -- layer 6: is production actually serving? ---------------------------
    if args.live:
        for endpoint in ("/health", "/stats", "/events/slim?limit=1",
                         "/events?start_date=2026-01-01T00:00:00&end_date=2026-12-31T00:00:00&limit=1"):
            try:
                _fetch_live(endpoint)
            except Exception as e:
                note(BAD, f"GET {endpoint} -> {type(e).__name__}: {e}")
        try:
            live_total = _fetch_live("/health").get("total_events")
            if live_total != len(events):
                note(WARN, f"production serves {live_total} events, local file has {len(events)} "
                           "— a deploy may be pending")
        except Exception:
            pass

    _rule("findings")
    if not findings:
        print(f"{OK} nothing wrong")
        return 0
    for level, message in sorted(findings, key=lambda f: f[0] != BAD):
        print(f"{level} {message}")
    errors = sum(1 for level, _ in findings if level == BAD)
    print(f"\n{errors} error(s), {len(findings) - errors} warning(s)")
    return 1 if errors else 0


# --------------------------------------------------------------------------- #
# sources
# --------------------------------------------------------------------------- #

def cmd_sources(args) -> int:
    from dateutil import parser as date_parser

    events = _events()
    counts = Counter(e.get("source_name") for e in events)
    latest_start: dict[str, str] = {}
    latest_scrape: dict[str, str] = {}
    for e in events:
        name = e.get("source_name")
        start = str(e.get("start_datetime") or "")
        if start > latest_start.get(name, ""):
            latest_start[name] = start
        seen = str(e.get("scraped_at") or "")
        if seen > latest_scrape.get(name, ""):
            latest_scrape[name] = seen

    if args.json:
        print(json.dumps([{
            "name": s.name, "kind": s.kind, "runs_in_ci": s.runs_in_ci,
            "events": counts.get(s.name, 0), "latest_start": latest_start.get(s.name),
            "last_scraped": latest_scrape.get(s.name), "notes": s.notes,
        } for s in SOURCES], indent=2))
        return 0

    now = datetime.now()
    print(f"{'source':<34}{'kind':<12}{'ci':<5}{'events':>7}{'furthest out':>15}  last scraped")
    print("-" * 92)
    for s in SOURCES:
        n = counts.get(s.name, 0)
        flag = OK if n else BAD
        furthest = latest_start.get(s.name, "")[:10] or "-"
        seen = latest_scrape.get(s.name, "")
        if seen:
            try:
                days = (now - date_parser.parse(seen).replace(tzinfo=None)).days
                seen = f"{days}d ago" if days else "today"
            except Exception:
                seen = seen[:10]
        print(f"{flag} {s.name:<32}{s.kind:<12}{'yes' if s.runs_in_ci else 'NO':<5}"
              f"{n:>7}{furthest:>15}  {seen or '-'}")

    silent = [s.name for s in SOURCES if not counts.get(s.name)]
    print(f"\n{len(SOURCES)} sources registered, {len(SOURCES) - len(silent)} contributing events")
    if silent:
        print(f"{BAD} contributing nothing: {', '.join(silent)}")
    for s in SOURCES:
        if s.notes:
            print(f"{DOT} {s.name}: {s.notes}")
    return 0


# --------------------------------------------------------------------------- #
# scrape one source
# --------------------------------------------------------------------------- #

def cmd_scrape(args) -> int:
    from src.quality import check_invariants
    from src.quality.fingerprint import compare, fingerprint_source, load_baselines

    source = BY_NAME.get(args.source)
    if source is None:
        print(f"{BAD} unknown source {args.source!r}. Try: cal sources")
        return 2
    if not source.is_scraped:
        print(f"{BAD} {source.name} has no scraper: {source.notes}")
        return 2

    print(f"running {source.name} ({source.kind}) ...")
    started = datetime.now()
    events = source.load().run()
    elapsed = (datetime.now() - started).total_seconds()
    print(f"{OK} {len(events)} events in {elapsed:.0f}s\n")

    as_dicts = [e.model_dump(mode="json") for e in events]

    for e in events[: args.limit]:
        print(f"  {e.start_datetime}  {(e.title or '')[:56]:<56} {(e.venue_name or '-')[:28]}")
    if len(events) > args.limit:
        print(f"  ... {len(events) - args.limit} more")

    _rule("invariants")
    violations = check_invariants(as_dicts)
    print(f"{OK} clean" if not violations else "\n".join(f"{BAD} {v}" for v in violations))

    _rule("shape")
    fp = fingerprint_source(source.name, as_dicts)
    for key, value in fp.to_dict().items():
        if key != "source":
            print(f"  {key:<26}{value}")
    drifts = compare(fp, load_baselines().get(source.name, []))
    for d in drifts:
        print(f"{WARN if d.severity == 'warning' else BAD} {d}")

    if args.save_fixture:
        path = _save_fixture(source)
        print(f"\n{OK} fixture saved to {path}")

    if not args.dry_run:
        print(f"\n{DOT} dry run only — nothing written. Use `cal repair {source.name}` to store this.")
    return 0


def _save_fixture(source) -> Path:
    """Snapshot a source's listing HTML so its tests can run offline."""
    import urllib.request

    scraper = source.load()
    target = REPO / "tests" / "fixtures" / source.module.rsplit(".", 1)[-1]
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{datetime.now():%Y-%m-%d}.html"
    request = urllib.request.Request(
        scraper.source_url,
        headers=getattr(scraper, "get_browser_headers", lambda: {})() or {})
    with urllib.request.urlopen(request, timeout=30) as r:
        path.write_bytes(r.read())
    return path.relative_to(REPO)


# --------------------------------------------------------------------------- #
# check
# --------------------------------------------------------------------------- #

def cmd_check(args) -> int:
    from src.quality import check_drift, check_invariants, reset_baseline

    if args.rebaseline:
        if not args.source:
            print(f"{BAD} --rebaseline needs a source name")
            return 2
        dropped = reset_baseline([args.source])
        if dropped:
            print(f"{OK} forgot {dropped[args.source]} run(s) of history for {args.source}. "
                  "Drift stays quiet for it until three fresh runs accumulate.")
        else:
            print(f"{DOT} {args.source} had no recorded history")
        return 0

    events = _events()
    if args.source:
        events = [e for e in events if e.get("source_name") == args.source]
        if not events:
            print(f"{BAD} no events from {args.source!r}")
            return 2

    print(f"checking {len(events)} events" + (f" from {args.source}" if args.source else ""))

    _rule("invariants (absolute)")
    violations = check_invariants(events)
    print(f"{OK} clean" if not violations else "\n".join(f"  {v}" for v in violations))

    _rule("drift (vs each source's own baseline)")
    _, drifts = check_drift(events)
    if args.source:
        drifts = [d for d in drifts if d.source == args.source]
    print(f"{OK} clean" if not drifts else "\n".join(f"  {d}" for d in drifts))

    errors = ([v for v in violations if v.severity == "error"]
              + [d for d in drifts if d.severity == "error"])
    return 1 if errors else 0


# --------------------------------------------------------------------------- #
# runs
# --------------------------------------------------------------------------- #

def cmd_runs(args) -> int:
    from src.quality.run_record import recent

    records = recent(args.limit)
    if not records:
        print("no run records yet — data/runs/ is empty")
        return 0
    print(f"{'run':<24}{'gate':<10}{'events':>7}{'scrapers':>10}{'time':>7}  changes")
    print("-" * 78)
    for r in records:
        ok = sum(1 for s in r.get("scrapers", []) if s.get("status") == "ok")
        d = r.get("diff", {})
        changes = f"+{d.get('added', 0)} -{d.get('removed', 0)} ~{d.get('changed', 0)}" if d else "-"
        print(f"{r['run_id']:<24}{r.get('gate', {}).get('decision', '?'):<10}"
              f"{r.get('counts', {}).get('final', 0):>7}"
              f"{ok}/{len(r.get('scrapers', [])):>9}{r.get('duration_s', 0):>6.0f}s  {changes}")
    return 0


def cmd_run(args) -> int:
    from src.quality.run_record import load

    try:
        r = load(args.run_id)
    except FileNotFoundError:
        print(f"{BAD} no run record {args.run_id!r}. Try: cal runs")
        return 2

    print(f"run {r['run_id']}  git {r.get('git_sha')}  {r.get('duration_s')}s"
          f"{'  [CI]' if r.get('is_ci') else ''}")

    _rule("gate")
    print(f"  decision: {r.get('gate', {}).get('decision')} (mode {r.get('gate', {}).get('mode')})")
    for reason in r.get("gate", {}).get("reasons", []):
        print(f"  {reason}")
    for v in r.get("gate", {}).get("violations", []):
        print(f"  {BAD} {v['source']}: {v['detail']}")
    for d in r.get("gate", {}).get("drifts", []):
        print(f"  {BAD if d['severity'] == 'error' else WARN} {d['source']}: {d['detail']}")

    _rule("counts")
    for stage, n in r.get("counts", {}).items():
        print(f"  {stage:<16}{n}")
    if r.get("diff"):
        print(f"  {'diff':<16}" + " ".join(f"{k}={v}" for k, v in r["diff"].items()))

    if r.get("rejected"):
        _rule("rejected by validation")
        for reason, n in sorted(r["rejected"].items(), key=lambda kv: -kv[1]):
            print(f"  {n:>5}  {reason}")

    _rule("scrapers")
    for s in sorted(r.get("scrapers", []), key=lambda s: -s.get("duration_s", 0)):
        mark = OK if s["status"] == "ok" else BAD
        print(f"  {mark} {s['source']:<34}{s.get('returned', 0):>6} events{s.get('duration_s', 0):>8.0f}s"
              + (f"   {s['error'][:60]}" if s.get("error") else ""))
    return 0


# --------------------------------------------------------------------------- #
# diff
# --------------------------------------------------------------------------- #

def cmd_diff(args) -> int:
    from src.utils.storage import diff_events

    after = _events()
    if args.live:
        print(f"comparing local data/events.json against {LIVE_API}")
        before = _fetch_live("/events?limit=5000")
        label = "production"
    else:
        raw = _git("show", "HEAD:data/events.json")
        if not raw:
            print(f"{BAD} could not read data/events.json at HEAD")
            return 2
        before = json.loads(raw)
        label = "HEAD"

    d = diff_events(before, after)
    print(f"vs {label}: +{len(d['added'])} added, -{len(d['removed'])} removed, "
          f"~{len(d['changed'])} changed, {d['unchanged']} unchanged\n")

    for e in d["added"][: args.limit]:
        print(f"  + {e.get('start_datetime')}  {(e.get('title') or '')[:52]:<52} {e.get('source_name')}")
    for e in d["removed"][: args.limit]:
        print(f"  - {e.get('start_datetime')}  {(e.get('title') or '')[:52]:<52} {e.get('source_name')}")
    for c in d["changed"][: args.limit]:
        print(f"  ~ {(c.get('title') or '')[:52]:<52} {c['source']}  {', '.join(c['fields'])}")

    by_source = Counter(e.get("source_name") for e in d["added"] + d["removed"])
    if by_source:
        print("\nby source: " + ", ".join(f"{s} {n}" for s, n in by_source.most_common(8)))
    return 0


# --------------------------------------------------------------------------- #
# repair
# --------------------------------------------------------------------------- #

def cmd_repair(args) -> int:
    """Re-scrape one source and splice it in, leaving every other source alone.

    Promoted from the throwaway script written during the 2026-08-31 incident,
    which is exactly the kind of work that should never have to be redone.
    """
    from src.models.event import Event
    from src.quality import check_invariants
    from src.utils.deduplicator import EventDeduplicator
    from src.utils.storage import diff_events, load_events, write_events
    from src.utils.validator import EventValidator

    source = BY_NAME.get(args.source)
    if source is None or not source.is_scraped:
        print(f"{BAD} unknown or unscrapable source {args.source!r}. Try: cal sources")
        return 2

    existing = load_events()
    others = [e for e in existing if e.get("source_name") != source.name]
    print(f"{len(existing)} events on disk; keeping {len(others)}, "
          f"replacing {len(existing) - len(others)} from {source.name}")

    raw = source.load().run()
    print(f"scraped {len(raw)}")

    validator = EventValidator()
    valid, rejected = [], Counter()
    for event in raw:
        event = validator.clean_and_enhance(event)
        ok, error = validator.validate_event(event)
        valid.append(event) if ok else rejected.update([error])
    print(f"valid {len(valid)}" + (f", rejected {dict(rejected)}" if rejected else ""))

    kept = EventDeduplicator.deduplicate_events(valid)
    print(f"after internal dedup {len(kept)}")

    # Several venues are covered by both their own scraper and an aggregator;
    # skipping this produces visible double listings. Bucket by day because the
    # comparison does fuzzy title matching and is O(n*m).
    by_day = defaultdict(list)
    for e in others:
        try:
            from src.models.event import EventCreate
            oc = EventCreate(**{k: v for k, v in e.items() if k != "id"})
        except Exception:
            continue
        by_day[EventDeduplicator.normalize_datetime(oc.start_datetime).date()].append(oc)

    final = [e for e in kept
             if not any(EventDeduplicator.are_duplicates(e, o)
                        for o in by_day.get(
                            EventDeduplicator.normalize_datetime(e.start_datetime).date(), ()))]
    print(f"after cross-source dedup {len(final)}")

    publish = [Event.from_create(e).model_dump(mode="json") for e in final] + others

    violations = check_invariants(publish)
    blocking = [v for v in violations if v.severity == "error"]
    _rule("invariants")
    print(f"{OK} clean" if not violations else "\n".join(f"  {v}" for v in violations))

    d = diff_events(existing, publish)
    print(f"\nchange: +{len(d['added'])} -{len(d['removed'])} ~{len(d['changed'])}")

    if blocking and not args.force:
        print(f"\n{BAD} refusing to write with invariant violations. Fix the scraper, "
              "or pass --force if you are certain.")
        return 1
    if args.dry_run:
        print(f"\n{DOT} dry run — nothing written.")
        return 0

    write_events(publish)
    print(f"\n{OK} wrote {len(publish)} events to data/events.json")
    print(f"{DOT} verify with `cal check`, then commit and push to deploy.")
    return 0


# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="cal", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    d = sub.add_parser("doctor", help="what is wrong right now")
    d.add_argument("--live", action="store_true", help="also probe the production API")
    d.set_defaults(func=cmd_doctor)

    s = sub.add_parser("sources", help="the registry, with live counts and freshness")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_sources)

    sc = sub.add_parser("scrape", help="run one scraper and show what it would produce")
    sc.add_argument("source")
    sc.add_argument("--dry-run", action="store_true", default=True)
    sc.add_argument("--limit", type=int, default=15)
    sc.add_argument("--save-fixture", action="store_true", help="snapshot the page for offline tests")
    sc.set_defaults(func=cmd_scrape)

    c = sub.add_parser("check", help="invariants and drift against current state")
    c.add_argument("source", nargs="?")
    c.add_argument("--rebaseline", action="store_true",
                   help="forget a source's fingerprint history — use after fixing its scraper")
    c.set_defaults(func=cmd_check)

    r = sub.add_parser("runs", help="recent scrape runs")
    r.add_argument("-n", "--limit", type=int, default=20)
    r.set_defaults(func=cmd_runs)

    r1 = sub.add_parser("run", help="one run in full")
    r1.add_argument("run_id")
    r1.set_defaults(func=cmd_run)

    df = sub.add_parser("diff", help="what changed vs HEAD, or vs production")
    df.add_argument("--live", action="store_true")
    df.add_argument("--limit", type=int, default=15)
    df.set_defaults(func=cmd_diff)

    rp = sub.add_parser("repair", help="re-scrape one source and splice it in")
    rp.add_argument("source")
    rp.add_argument("--dry-run", action="store_true")
    rp.add_argument("--force", action="store_true", help="write even with invariant violations")
    rp.set_defaults(func=cmd_repair)

    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
