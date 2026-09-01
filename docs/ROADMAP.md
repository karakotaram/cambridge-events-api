# Roadmap

Sequenced work to close the gap between [ARCHITECTURE.md](ARCHITECTURE.md) and
the code. Ordered by leverage, not by size.

Every item traces to something that actually happened, with the measurement that
proves it. Nothing here is speculative tidying — if an item cannot cite evidence,
it does not belong on this list.

**Status key:** ✅ done · 🔜 next · ⬜ planned

All nine items shipped on 2026-08-31. What each one actually produced is
recorded under **Shipped** below; the evidence that motivated it is kept
verbatim, because the reasoning is the part worth preserving.

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

## 1 ✅ One source registry

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

**Shipped.** `src/sources.py` — 42 sources in one frozen-dataclass registry with
lazy scraper imports so listing stays fast. `scrape.py` iterates
`in_run_order(is_ci=…)`; `ci_monitor.REGISTERED_SOURCES` is now a comprehension
over `SOURCES`; `CI_SKIP_SOURCES` is derived. The drift is structurally
impossible rather than merely fixed.

Registering everything also revealed **Longfellow House**: 185 lines of working
Playwright scraper that had never been wired into `scrape.py`, producing nothing
for months and raising no alarm. `test_every_scraper_module_is_registered` now
fails if that recurs.

**Effort** small · **Risk** low · **Unblocks** items 2, 3, 6 — all of which need
to enumerate sources.

---

## 2 ✅ The contract layer: invariants and fingerprints

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

**Shipped.** `src/quality/invariants.py` (7 absolute rules) and
`src/quality/fingerprint.py` (11-metric shape + source-relative drift), with
baselines in `data/fingerprints.json` retaining 30 runs.

Verified both ways: **0 violations against today's clean data, 5 against the run
that shipped the bug.** Drift against a proper baseline produces three errors on
that run (events 33% of normal, date span 25%, max-events-one-day 310%).

One rule needed retuning against reality. `date_out_of_range` at ±30 days flagged
258 events in *clean* data — User Submitted is never re-scraped and CI-blocked
sources keep their listings, so both age past any tight bound. Widened to ±2
years, which is what the rule was actually for: impossible dates, not stale ones.
Catching that during the build is the whole argument for tuning against real
data before enforcing anything.

Baselines were seeded from the last 10 daily scrapes in git, **excluding City of
Cambridge** — its pre-fix history describes a broken system and must not define
normal. It starts fresh and stays silent until three post-fix runs accumulate.

**Effort** medium · **Risk** low, it only reports at first · **Unblocks** item 3.

---

## 3 ✅ The gate

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

**Shipped.** `src/quality/gate.py`, wired into `scrape.py` between finalize and
write, and into `scrape-events.yml` — which no longer commits unconditionally,
because a blocked run exits non-zero and stops the job.

The tuning period turned out to be better expressed as a **split**, not a
calendar reminder: invariants are absolute and proven clean, so they block from
day one; drift compares against a learned baseline where a mistuned threshold
would block good data, so it reports until `GATE_DRIFT=enforce`. Drift is also
self-tuning — it stays silent until a source has three runs of history — so the
"two weeks" is built into the mechanism.

Confirmed against the real failure: **the Sept 14 run is blocked on invariants
alone**, with or without drift enforcement. Quarantine writes `events.json`,
`gate.json`, and a readable `report.txt`; a GitHub issue carries the report.

Rehearsing the gate against the real orchestrator then found a hole in it. With
every scraper failing, the publish set was just the always-preserved user
submissions — 232 events replacing 2,974 — and the gate **passed**, because the
fifteen "source disappeared" findings were drift and drift reports rather than
blocks. Added `check_collapse()`: a run may not drop below 50% of the currently
published event count, or lose more than 40% of contributing sources. It compares
against the data being replaced rather than a baseline, so it needs no history
and is never subject to `GATE_DRIFT`.

Losing almost the whole calendar is exactly the class of outcome that must not
depend on a tunable. `tests/test_gate.py` pins all of it.

**Effort** small once item 2 exists · **Risk** medium — a mistuned gate blocks
good data, which is why report-only comes first · **Unblocks** trusting the daily
pipeline unattended.

---

## 4 ✅ Stable event identity

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

**Shipped.** `event_identity()` in `src/models/event.py`, minted through the one
supported entry point `Event.from_create()`, plus `src/utils/storage.py` for
canonical ordering and a real `diff_events()`.

The basis is deliberately only `source_name | source_url | start_datetime |
normalized title`. Venue, description, image, and category are all fields we
expect to get *better* at extracting; folding them in would rotate every id the
next time a scraper improved. Measured 0 collisions across all 2,974 events.

Migration done: all ids recomputed, file rewritten in canonical order. A
simulated nightly run now diffs to **+1 −2 ~1 with 2,971 unchanged**, against
9,000 lines of churn before.

As predicted, the cutover orphaned the existing interaction history once.

**Effort** medium · **Risk** medium — one-time history loss · **Unblocks** items
5 and 6, and makes the recommender's inputs real.

---

## 5 ✅ Run records

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

**Shipped.** `src/quality/run_record.py`. Every scrape writes
`data/runs/<run-id>.json` with per-scraper status, duration and yield, the
validator's rejection reasons by count, per-source fingerprints, the gate
decision, and the diff summary. 90 records retained.

**Effort** small · **Risk** none · **Unblocks** item 6, and turns diagnosis from
archaeology into reading.

---

## 6 ✅ The `cal` CLI

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

**Shipped.** `src/cli.py`, all seven verbs.

`cal doctor` earned its place immediately. Run against the current repo it found,
in about two seconds, that **18 of 42 registered sources contribute zero
events** — a fact that had been true in production for weeks with nothing
reporting it. See "What doctor found" at the end of this file.

**Effort** medium · **Risk** low · **Unblocks** every future diagnosis.

---

## 7 ✅ Golden fixtures and scraper tests

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

**Shipped.** `tests/fixtures/` with gzipped snapshots (496 KB total), a
`fixture_html` helper, and an `offline` fixture that makes any outbound HTTP call
raise.

That guard paid for itself during the build: the first Rockwell test patched
`fetch_html`, which that scraper never calls — it reads a JSON API — and `offline`
caught it reaching the real endpoint instead of silently passing over live data.
Rockwell now has a JSON fixture and its own test.

The Cambridge.gov week page that caused the incident is committed, with tests
asserting Danehy Park Family Day parses to Sept 19 11:00 and the Comedy Studio
show to Sept 16 17:00.

**Effort** medium, and incremental — one source at a time · **Risk** none.

---

## 8 ✅ Make documentation executable

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

**Shipped.** `tests/test_docs.py` — link resolution, scraper registration,
registry/scraper name agreement, monitoring coverage, and the data invariants.
Each was mutation-tested to confirm it is not vacuous. `pytest`, `flake8`, and
`black` added to `requirements.txt`. `test-scrapers.yml` replaced with a real
suite that runs on push.

**Effort** small · **Risk** none · **Unblocks** trusting the docs, which is the
whole point of writing them.

---

## 9 ✅ Repo hygiene

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

**Shipped.** Top-level tracked files: 28 → 13. Deleted the stale root
`events.json` (10 events, last touched 2025-11-20), an 8.8 MB `.mhtml` browser
archive, and six one-off `*_audit.html` debug artifacts. Generated HTML now
writes to `data/` and is gitignored.

`presentation.html` was **left in place** deliberately: it is a project-overview
deck, and "referenced by no code" is not the same as "wanted by no human".

**Effort** trivial · **Risk** none — but confirm with the owner before deleting
anything, since "referenced by no code" is not the same as "wanted by no human."

---

## Sequencing

```
1 registry ──┬── 2 contract ── 3 gate          the loud, non-shipping half
             │
             └── 6 CLI ◄── 5 run records ◄── 4 stable identity
                                                the legible, accretive half

7 fixtures, 8 executable docs, 9 hygiene — independent
```

Items 1–3 made failure loud and non-shipping. Items 4–6 made the system legible
and accretive. 7–9 keep it from rotting.

---

## What `doctor` found

The point of building this was to stop finding things by hand. Two seconds after
`cal doctor` first ran, it reported something nobody knew:

**18 of 42 registered sources contributed zero events**, and had for weeks — the
preceding eight daily commits all carried 24–25 contributing sources. Every one
has now been diagnosed, and all but three fixed.

### The single cause behind five of them

`scrape-events.yml` installed Chrome for Selenium but never ran
`playwright install`. All nine Playwright scrapers failed in CI every night
while working locally. Adding one step to the workflow recovered **MIT Events,
MIT Open Space, MIT Music & Theater, Longy School of Music, and Skip the Small
Talk** on the next run.

### The other thirteen

No shared cause. The run record attributed each one:

| source | was | cause | now |
|---|---|---|---|
| The Dance Complex | 0 | iCal feed returns HTTP 200 with an empty body, every variant; HTML renders a day at a time in JS | **420** via Tribe REST API |
| Harvard Square | 0 | walked `/events/YYYY-MM-DD/` for 30 days at 0.5s each — those URLs stopped listing anything, so it burned 127s producing nothing | **343** via Tribe REST API |
| Longfellow House | 0 | written months earlier, never registered in `scrape.py` | **180** |
| Porter Square Books | 0 | Selenium fingerprinted and 403'd; real data hidden inside `<template>` elements | **78** |
| Regent Theatre | 0 | EventON loads over AJAX; the scraper parsed the empty shell | **36** |
| Sanders Theatre | 0 | `calendar.college.harvard.edu` 404s; listings moved to the Harvard Box Office | **19** |
| Grolier Poetry Book Shop | 0 | venue renamed `/upcoming-readings` to `/upcoming-events` | **14** |
| Theatre at First | 0 | homepage still showed a November 2025 production; all 7 events rejected as too old | **6** via their public Google Calendar |
| Museum of Science | 0 | selectors matched an older page | **5** |
| Harvard-Radcliffe Dramatic Club | 0 | selectors matched an older page; also needlessly on Selenium | **4** (all they have posted) |
| First Parish in Cambridge | 0 | **not broken** — the venue's own JSON reports `upcoming: 0` | 0, correctly |
| Cambridge Public Library | 0 | fabricated every date, and wholly redundant | **retired** |
| Harvard Memorial Church | 0 | 403 on every path, to every client | **retired** |

### Three findings worth keeping

**The Playwright base was spoofing its user-agent**, claiming macOS while the
browser's client hints said Linux. Bot protection reads that contradiction for
exactly what it is: Porter Square Books returns 403 for the spoofed UA and 200
for the browser's own, from the same headless Chromium. Removing the spoof made
one scraper work and affected none of the others.

**Cambridge Public Library was the Sept 14 bug in a second scraper.** It
defaulted to `datetime.now().replace(hour=10, minute=0, second=0)` for anything
it could not parse — every event, at 10:00, on the day of the scrape. The
existing invariants missed it twice over: the seconds were zeroed, so
`clock_stamped` saw nothing, and 17 events was under the 20-event
`timestamp_pileup` cap. Added `uniform_timestamp`, which fires when more than
70% of a source's output shares one exact start, so size no longer matters. The
source itself is retired rather than fixed — all 17 of its events already appear
in City of Cambridge, which carries 802 library listings with their real times.

**A failed scrape was deleting data.** Harvard Book Store began 403-ing from
every IP, not just CI's. Because "preserve on failure" only applied inside CI, a
local run dropped all 21 of its events and Somerville Theatre's with them.
`build_publish_set` now keeps the still-upcoming events of any source that
failed or came back empty. A scrape that failed is not evidence that a venue
cancelled its programme.

### And one the fixes surfaced

Reading a progressively-rendered list races the render. Longfellow House yielded
54 cards run alone and 4 inside a full scrape, because under load the first card
existed long before the rest — `wait_for_selector` returns on the first match.
Added `wait_for_stable_count()` to the Playwright base for the general case, and
moved Longfellow itself onto the JSON its page fetches, which removes the race
rather than timing it.

## What is deliberately still open

- **`GATE_DRIFT=report`.** Drift is recorded but does not block. Invariants and
  the collapse check do, from day one. Flip it in
  `.github/workflows/scrape-events.yml` once a few weeks of alert volume looks
  sane. It is already earning its keep in report mode: it flagged The Dance
  Complex at "2341% of normal" the run after that scraper was fixed, which is a
  true observation about a stale baseline and exactly the kind of alert that
  should not have been able to block a deploy.
- **Skip the Small Talk is national.** It returns events in Chicago, New York,
  Washington, Austin, and Richmond, all stamped `city="Boston"`. Most land in
  the past and are rejected, so the leakage is small, but the scraper should
  filter on venue rather than trust the site's `?category=Boston` parameter.
- **Harvard Book Store and Longy are being blocked**, the first permanently so
  far, the second by rate limiting. Their events are preserved rather than
  dropped; `scrape_local.py` is the intended path for the first.
- **Editor's Picks still matches on `title + source_name`.** Now that ids are
  stable it could key on identity, but the existing mechanism works and changing
  it risks dropping live picks for no user-visible gain.
- **One-time interaction history loss.** The id migration orphaned ~30 days of
  click data. Expected and accepted.
- **`presentation.html`** is unreferenced but kept — see item 9.
