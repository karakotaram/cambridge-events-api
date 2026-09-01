# Roadmap

Sequenced work to close the gap between [ARCHITECTURE.md](ARCHITECTURE.md) and
the code. Ordered by leverage, not by size.

Every item traces to something that actually happened, with the measurement that
proves it. Nothing here is speculative tidying — if an item cannot cite evidence,
it does not belong on this list.

**Status key:** ✅ done · 🔜 next · ⬜ planned

---

## 0 ✅ Stop fabricating dates *(done 2026-08-31)*

**Evidence.** 117 events shipped on 2026-09-14 carrying an identical timestamp of
`13:29:26.288025` — a clock reading plus two weeks. Two of them, Danehy Park
Family Day and Cambridge NITES, were reported by a reader.

**Done.**

- `src/scrapers/cambridge_gov.py` reads dates from the listing markup and skips
  any row it cannot date. Also dropped Selenium, which recovered weeks 4–9 of the
  60-day window the browser crash had been silently eating (359 → 1075 events).
- Same clock fallback removed from `longy.py`, `mit_calendar.py`,
  `mount_auburn.py`, `base_scraper.py`.
- `EventValidator` rejects any start time carrying seconds or microseconds.
- A `field_validator` on `Event` and `EventCreate` stores all times as naive
  Eastern, closing failure mode 4 at the model boundary.
- 17 tests in `tests/`.

**Invariants established.** No fabricated dates. All times naive Eastern.

---

## 1 🔜 One source registry

**Evidence, measured today.** The set of sources is stored twice — the
`register_scraper()` calls in `scrape.py` and `REGISTERED_SOURCES` in
`src/agents/ci_monitor.py` — and has drifted:

```
scrape.py registers 40 scrapers; ci_monitor knows 38 sources

registered but UNMONITORED:  Harvard GSD, Museum of Science,
                             Regent Theatre, The Sinclair
monitored but NOT REGISTERED: Longfellow House, User Submitted
```

Four scrapers run every day with no freshness monitoring at all. If The Sinclair
dies tonight, nothing will ever say so. This is a live blind spot, not a
hypothetical.

**Build.** `src/sources.py` — one declarative registry, the only place a source
is described:

```python
@dataclass(frozen=True)
class Source:
    name: str                      # canonical, matches Event.source_name
    scraper: type[BaseScraper]
    runs_in_ci: bool = True        # replaces CI_SKIP_SOURCES
    kind: Literal["requests", "selenium", "playwright", "aggregator", "manual"]
    notes: str = ""                # e.g. "SSL handshake fails on GitHub IPs"

SOURCES: tuple[Source, ...] = (...)
```

Then delete the duplicates: `scrape.py` iterates `SOURCES` ordered by `kind`,
`ci_monitor.py` imports `SOURCES`, and `CI_SKIP_SOURCES` becomes
`[s.name for s in SOURCES if not s.runs_in_ci]`.

**Test that keeps it true.**

```python
def test_every_scraper_module_is_registered():
    """A scraper on disk but not in SOURCES is dead code or an oversight."""
```

**Effort** small · **Risk** low · **Unblocks** items 2, 3, 6 — all of which need
to enumerate sources.

---

## 2 🔜 The contract layer: invariants and fingerprints

**Evidence.** The health monitor watched event count and scored the broken
Cambridge.gov run as an *improvement* (359, or 143% of its 250.6 recent average). Four other
metrics on the same data were screaming. See the table in
[ARCHITECTURE.md §2](ARCHITECTURE.md#the-failure-that-defines-the-design).

**Build.** `src/quality/` with two clearly separated tiers.

`invariants.py` — absolute, no history needed, violation fails the run:

```python
def check_invariants(events: list[Event]) -> list[Violation]:
    # sub-minute precision in start_datetime   (117 during the incident)
    # tz-aware datetimes                       (56 before the fix)
    # > 20 events from one source sharing an exact timestamp
    #                                          (117 during; 8 is the legit max)
    # start_datetime outside [-30d, +2y]
    # title matching known navigation chrome
```

`fingerprint.py` — per-source shape, compared against that source's own history:

```python
@dataclass
class Fingerprint:
    source: str
    events: int
    date_span_days: int
    max_events_one_day: int
    max_events_one_timestamp: int
    distinct_titles_ratio: float
    distinct_venues: int
    distinct_images_ratio: float      # 326 city events share one stock city-manager photo
    median_description_len: int
    null_venue_rate: float
    latest_start: date                # catches staleness (failure mode 7)
```

**The design constraint that matters most.** Thresholds must be source-relative.
Naive global rules, run against today's healthy data, flag nine of twenty-four
sources — Brattle Theatre for having one venue (it is one cinema), A.R.T. for
having 11 distinct titles across 156 events (11 productions, many performances),
City of Cambridge for repeating titles (weekly story times). All three are
correct. **A monitor that cries wolf is worse than no monitor**, because its
reader learns to skip it. Compare each source to its own baseline; alert on
change, not on absolute value.

Store baselines in `data/fingerprints.json`, keyed by source, retaining
**30 runs** rather than the current 5 — Cambridge.gov degraded across several
days and the broken state became the baseline before anything noticed.

**Effort** medium · **Risk** low, it only reports at first · **Unblocks** item 3.

---

## 3 🔜 The gate

**Evidence.** `.github/workflows/scrape-events.yml` runs the scrape, then:

```yaml
git add data/events.json ...
git commit -m "Auto-update events - $(date ...)"
git push
```

Unconditional. There is no step between "the scraper finished" and "readers see
it." That is how 117 wrong dates reached a reader.

**Build.** One decision inserted before the commit:

```
run → invariants → fingerprint diff vs. last known-good
    ├─ pass  → commit, push, record as new baseline
    └─ fail  → write data/quarantine/<run-id>/, open an issue with the diff,
               leave production on the last good data, exit non-zero
```

Quarantine rather than discard: the bad run is the evidence for diagnosing what
broke.

**Escape hatch, mandatory.** `workflow_dispatch` input `force: true` to publish
past a failing gate, because a legitimate large change (a venue publishing its
whole spring season at once) will eventually trip it and the fix must not be
"disable the gate."

**Tuning period.** Run in report-only mode for two weeks first, so the alert rate
is known before it can block anything.

**Effort** small once item 2 exists · **Risk** medium — a mistuned gate blocks
good data, which is why report-only comes first · **Unblocks** trusting the daily
pipeline unattended.

---

## 4 ⬜ Stable event identity

**Evidence, measured across two consecutive daily runs:**

```
run A: 2263 events    run B: 2257 events    IDs in common: 269
```

**88% of events get a new UUID every day for no reason.** The 269 survivors are
only the preserved sources, copied verbatim rather than re-finalized.

Consequences, all of them real today:

- `get_interaction_counts_from_db()` aggregates 30 days of clicks by `event_id`
  and `score_events()` joins them onto today's events. Almost nothing joins. The
  popularity signal driving ranking is close to inert.
- The onboarding flow stores liked event IDs to compute preferences; they expire
  within a day.
- Editor's Picks is keyed on `title + source_name` — a workaround whose existence
  is the diagnosis.
- `git diff data/events.json` is ~9,000 lines of churn, so git cannot answer
  "what changed in this scrape?"
- `.git` is 136 MB against an 18 MB tree, from rewriting a 4 MB blob daily.

**Build.** In `src/models/event.py`:

```python
def event_identity(source_name: str, source_url: str,
                   start_datetime: datetime, title: str) -> str:
    """Stable across runs. Same occurrence -> same id."""
    basis = f"{source_name}|{source_url}|{start_datetime.isoformat()}|{normalize_title(title)}"
    return hashlib.sha256(basis.encode()).hexdigest()[:32]
```

Sort `data/events.json` by `(start_datetime, id)` on write.

**Migration.** Every ID changes once at cutover, orphaning ~30 days of
interaction history. Worth it, and worth saying out loud rather than discovering
later. `WebsiteInteraction` already denormalizes `event_title` and `source_name`
(added "for backwards compatibility" — someone had started working around this),
so a best-effort backfill on those two columns is possible.

**What it unlocks.** Interaction history compounds instead of resetting.
`git log -S<id>` answers "when did this event's time change?", which is currently
unanswerable. The daily diff becomes a readable changelog. Editor's Picks can key
on identity.

**Effort** medium · **Risk** medium — one-time history loss · **Unblocks** items
5 and 6, and makes the recommender's inputs real.

---

## 5 ⬜ Run records

**Evidence.** The Sept 14 root cause was recovered by noticing that
`13:29:26.288025` plus fourteen days equals Sept 14 — arithmetic on a corrupted
value, because no record of the run survived. `data/scraper_health.json` holds
five runs of bare per-source counts and nothing else.

**Build.** `data/runs/<iso-timestamp>.json`, one per scrape:

```json
{
  "run_id": "2026-08-31T13:29:26Z",
  "git_sha": "94f2b07",
  "duration_s": 1187,
  "scrapers": [
    {"source": "City of Cambridge", "status": "ok", "duration_s": 421,
     "returned": 1120, "rejected": {"Description is too short": 3},
     "fingerprint": {...}}
  ],
  "gate": {"decision": "pass", "violations": []},
  "diff": {"added": 41, "removed": 38, "changed": 12}
}
```

Retain 90 runs. Small files; the whole history stays greppable.

**Effort** small · **Risk** none · **Unblocks** item 6, and turns diagnosis from
archaeology into reading.

---

## 6 ⬜ The `cal` CLI

**Evidence.** Diagnosing the Sept 14 report took: read CLAUDE.md for the API URL,
curl `/stats` (500), fall back to `/events/slim`, pipe through a hand-written
`Counter`, notice the microsecond timestamps, do arithmetic on them, discover the
local checkout was 26 commits stale, then write a throwaway repair script in
`/tmp` — which is now gone, so the next person writes it again.

**Nothing accreted from an hour of work.** That is the problem this item solves.

**Build.** One verb per layer, so the mental model and the motor actions match:

| command | layer | answers |
|---|---|---|
| `cal doctor` | all | **what is wrong right now?** |
| `cal sources` | 0 | what do we scrape, when did each last produce, registry drift |
| `cal scrape <source> --dry-run` | 1 | what would this scraper produce, without writing |
| `cal check [<source>]` | 2 | invariant violations and drift in current state |
| `cal runs` / `cal run <id>` | 3 | run history; one run in full |
| `cal diff [--live]` | 5 | what changed vs. last run, or vs. production |
| `cal repair <source>` | 5 | re-scrape one source and splice it in |

`cal doctor` is the keystone — the one command worth having if only one gets
built. Run against the state of this repo at the start of the incident, it should
have printed, in under a second:

```
✗ City of Cambridge   117 events share start 2026-09-14T13:29:26.288025
✗ City of Cambridge   date span 16d, baseline 60d — weeks 4-9 missing
✗ GET /stats          500 Internal Server Error
! 56 events carry a UTC offset; 2,918 are naive
! local data/events.json is 26 commits behind origin/main
! ci_monitor is unaware of 4 registered scrapers
```

Every line is mechanically derivable. Every one was found by hand instead.

`cal repair <source>` promotes the throwaway script to a real verb: re-scrape one
source, validate, dedupe within the source and against the rest, splice into
`data/events.json`, leave everything else untouched.

**Effort** medium · **Risk** low · **Unblocks** every future diagnosis.

---

## 7 ⬜ Golden fixtures and scraper tests

**Evidence.** `.github/workflows/test-scrapers.yml` "tests" exactly two scrapers,
by fetching live pages and printing the first five events for a human to eyeball.
It is manual-dispatch only. Nothing asserts anything. Writing today's tests meant
hand-authoring HTML snippets from memory of the page structure.

**Build.** `tests/fixtures/<source>/<date>.html` — saved responses, captured by
`cal scrape <source> --save-fixture`. Then a table-driven test per source:
parse the fixture, assert the event count and a few known events with exact
dates.

The accretive rule: **every scraper bug leaves a fixture behind.** The page that
caused the Sept 14 incident should be in the repo so that failure can never
return silently.

Add a weekly job that re-fetches each source and diffs its structure against the
fixture — that is the only practical detector for failure mode 8, semantic drift.

**Effort** medium, and incremental — one source at a time · **Risk** none.

---

## 8 ⬜ Make documentation executable

**Evidence.** Documentation in this repo asserts things that are false:

- `CLAUDE.md` said `pytest tests/`; there was no `tests/` directory until
  2026-08-31, and `pytest` is still not in `requirements.txt` (nor are `black` or
  `flake8`, both also documented).
- `CLAUDE.md` documented `/events?start_date=` filtering; it returned 500.
- `docs/archive/LOVABLE_INTEGRATION.md` says "668 total events" against 2,974.
- `ci_monitor.REGISTERED_SOURCES` has drifted four scrapers from reality.

An agent reading these is actively misled and will waste a turn discovering it.
**Prose that nothing verifies decays into a trap.**

**Build.** `tests/test_docs.py`:

```python
def test_documented_commands_exist()      # every ```bash block in CLAUDE.md runs --help
def test_documented_endpoints_respond()   # every endpoint in docs/API.md returns non-5xx
def test_no_hardcoded_counts_in_docs()    # numbers describing data must be generated
def test_deviation_table_is_current()     # ARCHITECTURE.md §7 rows still true
```

Add `pytest`, `black`, `flake8` to `requirements.txt` — or stop documenting them.

**Effort** small · **Risk** none · **Unblocks** trusting the docs, which is the
whole point of writing them.

---

## 9 ⬜ Repo hygiene

**Evidence.** `git ls-files` at the top level returns 28 entries. An agent running
`ls` cannot tell which are load-bearing. Measured:

| file | status |
|---|---|
| `events.json` (root) | 10 events, last touched 2025-11-20. `data/events.json` has 2,974. Stale duplicate. |
| `1120cdevents.mhtml` | a saved browser archive |
| `aeronaut_audit.html`, `brattle_audit.html`, `comedy_dance_audit.html`, `first_parish_audit.html`, `harvard_art_museums_audit.html`, `harvard_square_audit.html` | one-off debug artifacts, referenced by nothing |
| `cambridge_sample.html`, `presentation.html`, `events.html` | referenced by nothing |
| `user_submitted_audit.html` | **generated** by `sync_user_events.py` — keep, but it belongs in `data/` and `.gitignore` |

**Build.** Delete the dead ones, move generated output under `data/`, add a
`.gitignore` rule so audit HTML stops being committed. Cheap, and it makes the
repo root legible at a glance.

**Effort** trivial · **Risk** none — but confirm with the owner before deleting
anything, since "referenced by no code" is not the same as "wanted by no human."

---

## Sequencing

```
1 registry ──┬── 2 contract ── 3 gate          the loud, non-shipping half
             │
             └── 6 CLI ◄── 5 run records ◄── 4 stable identity
                                                the legible, accretive half

7 fixtures, 8 executable docs, 9 hygiene — independent, do any time
```

Items 1–3 make failure loud and non-shipping. Items 4–6 make the system legible
and accretive. 7–9 keep it from rotting.

If only one item is ever built: **item 3, the gate.** It is the one that turns
"a reader emailed us" into "CI caught it."
