# Architecture

How this system is meant to hold together, why it is shaped this way, and where
the code does not match yet.

Read this before making a structural change. For day-to-day procedures see
[OPERATIONS.md](OPERATIONS.md); for what is being built next and why, see
[ROADMAP.md](ROADMAP.md).

---

## 1. What this system is

Forty hostile HTML sources go in. One trustworthy JSON file comes out, and an
API serves it to a public calendar at cambridgecalendar.com.

That is the whole job. Everything in `src/` exists to make the middle of that
sentence true.

## 2. The one hard problem

**Every scraper will break, and most breakage is silent.**

Venues redesign pages, add bot checks, rename CSS classes, and change date
formats without warning. A scraper that worked yesterday returning garbage today
is the normal case, not the exception. No amount of care in any individual
scraper changes this.

So the system's real job is not "scrape correctly." It is:

> Make breakage **loud** and **non-shipping**.

Loud: a failure produces a specific, findable signal rather than a plausible
wrong answer. Non-shipping: a run that fails its checks does not reach readers.

Every design decision below follows from that sentence. When a decision seems
arbitrary, this is why it exists.

### The failure that defines the design

On 2026-08-31 the Cambridge.gov scraper's browser died a third of the way
through its run. The scraper fell back to a clock reading for every event it
could no longer date, and 117 events landed on one day carrying an identical
microsecond timestamp. It shipped to production and a reader emailed about it.

The scraper bug was ordinary and was fixed in an hour. The interesting question
is why the system published it. Here is what the health monitor saw:

| what it measured | value | what it concluded |
|---|---|---|
| events from City of Cambridge | 359 | 143% of its 250.6 recent average — healthy |

And here is what was actually true of that run, next to the same source after
the fix:

| metric | shipped (broken) | after fix | would have caught it |
|---|---|---|---|
| events | 359 | 1075 | no — count went **up** |
| date span (days) | 16 | 63 | yes, vs. own baseline |
| max events on one day | 124 | 40 | yes, vs. own baseline |
| max events on one **timestamp** | 117 | 8 | yes, absolute invariant |
| clock-stamped start times | 117 | 0 | yes, absolute invariant |
| distinct start times | 140 | 596 | yes, vs. own baseline |

Four independent signals were available. Two need no history at all. The system
watched the single metric that pointed the wrong way.

**The lesson, and the thesis of this document: the system measures volume when
the thing that breaks is shape.**

## 3. The tower

Seven layers. Each owns exactly one kind of fact, has exactly one source of
truth for it, and is verifiable against the layer below.

| # | Layer | Owns | Single source of truth | Invariant |
|---|---|---|---|---|
| 0 | **Sources** | which venues exist, how each is reached | one registry | every scraper appears exactly once, and monitoring derives from it |
| 1 | **Scrapers** | HTML → `EventCreate[]` | `src/scrapers/*.py` | pure: URL in, events out, no writes, no clock fallbacks |
| 2 | **Contract** | what a valid event and a plausible run look like | `src/utils/validator.py` + fingerprints | invariants are absolute; drift is source-relative |
| 3 | **Run** | what one scrape actually did | run record | immutable, inspectable, retained |
| 4 | **Gate** | whether a run may ship | CI | a run that violates the contract quarantines instead of publishing |
| 5 | **State** | the current set of events | `data/events.json` | stable identity, deterministic order, diffable |
| 6 | **Surface** | what the world sees | `src/api/`, `docs/API.md` | derived from state, never a second copy of it |

The value of the tower is that a question always has one place to go. "What do
we scrape?" is layer 0. "Why is this event wrong?" is layer 3. "Why did this
ship?" is layer 4. An agent that knows the tower does not have to search.

### Layer 0 — Sources

One registry. Today this fact is stored twice — the registration calls in
`scrape.py` and `REGISTERED_SOURCES` in `src/agents/ci_monitor.py` — and they
have already drifted:

- **4 scrapers run daily but are invisible to monitoring**: Harvard GSD, Museum
  of Science, Regent Theatre, The Sinclair. If any dies, nothing will say so.
- **2 monitored sources are not registered**: Longfellow House, User Submitted.

Nobody did anything wrong. Two hand-maintained lists of the same fact drift;
that is what they do. The fix is not vigilance, it is deleting one of the lists.
See ROADMAP item 1.

### Layer 1 — Scrapers

A scraper is a function from a URL to events. It must not write files, mutate
globals, or consult the clock to fill in a missing value.

The clock rule is absolute and is the direct lesson of the Sept 14 incident:

```python
# never
start_datetime = parsed_date if parsed_date else current_date

# always
if parsed_date is None:
    logger.warning(f"Skipping '{title}' - no parseable date ({url})")
    continue
```

**A missing event costs one reader one listing. A wrong date costs every reader
that day's trust.** Absence is always the cheaper failure.

Purity is what makes fixtures possible: a pure scraper can be run against saved
HTML, which makes its tests fast, offline, and deterministic (ROADMAP item 7).

### Layer 2 — Contract

Two tiers, and the distinction matters more than any individual rule.

**Invariants** are absolute. They hold for every event from every source, need
no history, and a violation fails the run:

| invariant | why it exists |
|---|---|
| `start_datetime` has zero seconds and microseconds | real listings are on the minute; sub-minute precision only comes from a clock reading |
| `start_datetime` is naive Eastern | mixed offsets raise `TypeError` on comparison and move evening events across days |
| no more than ~20 events from one source share an exact timestamp | 117 did during the incident; 8 is the highest legitimate value observed (11 across all sources combined) |
| `start_datetime` within [-30d, +2y] | catches year-parsing errors |
| title is not navigation text | catches selector drift onto chrome |

**Drift checks** are source-relative. Each source has a fingerprint learned from
its own recent good runs, and the check compares this run to that baseline.

This distinction is not pedantry — a global threshold is actively harmful here.
Measured against today's data, naive global rules flag:

- **Brattle Theatre**: 1 distinct venue across 90 events → correct, it is one cinema
- **American Repertory Theater**: 11 distinct titles across 156 events, 3% distinct
  images → correct, it is 11 productions with many performances
- **City of Cambridge**: 299 distinct titles across 1075 events → correct,
  weekly library story times

All three are healthy. A global rule would cry wolf on nine of twenty-four
sources, and **a monitor that cries wolf is worse than no monitor**, because it
trains its reader to skip the output. Source-relative baselines flag "Brattle
suddenly has 1 venue when it always had 12" while staying quiet on "Brattle has
1 venue, as always."

The fingerprint is also the accretive artifact: the longer a source runs, the
better the system knows its shape.

### Layer 3 — Run

A scrape must leave evidence: which scrapers ran, how long each took, what each
returned, what validation rejected and why, the fingerprint per source, and the
resulting diff summary.

Today this layer barely exists. `data/scraper_health.json` keeps five runs of
bare per-source counts. When the Sept 14 failure was investigated, the cause was
recovered by noticing that `13:29:26.288025` plus fourteen days is Sept 14 —
arithmetic on a corrupted value, because no record of the run survived.

Five runs is also too short a memory. Cambridge.gov degraded across several days
and the broken state became the baseline before anything noticed.

### Layer 4 — Gate

Today `scrape-events.yml` runs the scrape and unconditionally commits and pushes
whatever came out. There is no step between "the scraper finished" and "readers
see it."

The gate is one decision: does this run satisfy the contract relative to the
last known-good run? If yes, publish and record it as the new baseline. If no,
write it to a quarantine path, open an issue with the fingerprint diff, and
leave production on the last good data.

Stale data is a much smaller harm than wrong data. A calendar a day behind is
mildly annoying; a calendar confidently showing the wrong day is the thing that
makes someone stop using it.

### Layer 5 — State

`data/events.json` is the state. Two properties it needs and does not have:

**Stable identity.** Every scrape assigns fresh `uuid4()` to every event.
Measured across two consecutive daily runs: **269 of ~2,260 IDs survived — 88%
of events got a new ID for no reason.** The 269 survivors are only the preserved
sources that are copied verbatim rather than re-finalized.

The damage radiates:

- `/analytics/interactions` and `score_events()` join 30 days of clicks on
  `event_id`. Almost none of that history joins. The popularity signal that
  drives ranking is close to inert.
- The onboarding flow stores liked event IDs to compute preferences; they expire
  within a day.
- Editor's Picks had to be keyed on `title + source_name` — a workaround whose
  existence is the tell.
- `git diff data/events.json` is 9,000 lines of pure churn, so git cannot answer
  "what changed in this scrape?"
- `.git` is **136 MB** against an 18 MB working tree, because a 4 MB JSON blob is
  rewritten daily.

The fix is content-derived identity: hash `source_name | source_url |
start_datetime | normalized title`. Same event next run, same ID. A rescheduled
event gets a new ID, which is correct — it is a different occurrence, and the
diff shows one removal and one addition, which reads clearly.

**Deterministic order.** Sort by `(start_datetime, id)` on write. Combined with
stable IDs, the daily diff becomes a real changelog: added events, removed
events, changed fields, and nothing else. `git log -S<id>` then answers "when
did this event's time change?" — a question that is currently unanswerable.

### Layer 6 — Surface

The API and the public docs are projections of state. They must never become a
second place where a fact lives.

`docs/API.md` is the contract with consumers. Numbers in it that describe the
data (event counts, source lists) should be generated, not typed — the archived
`LOVABLE_INTEGRATION.md` still says "668 total events" against today's 2,974,
which is what typed numbers do.

## 4. Failure taxonomy

Every way this system has broken or can break, and whether anything would notice.

| # | Failure | Volume signal | Detected today | Caught by |
|---|---|---|---|---|
| 1 | Silent partial collapse — a source loses a subset | drops, not to zero | weakly (5-run window absorbs it) | drift: date span, event count |
| 2 | Date fabrication — clock substituted for missing date | flat or **up** | **yes, as of 2026-08-31** | invariant: sub-minute precision, timestamp pileup |
| 3 | Field collapse — descriptions become boilerplate, titles become nav text | flat | no | drift: distinct-value ratios |
| 4 | Timezone drift — mixed aware/naive | flat | **yes, as of 2026-08-31** | invariant: naive Eastern, enforced in the model |
| 5 | Duplicate explosion — each event emitted N times | up | no | drift: dedup-removal ratio |
| 6 | Total source death — zero events | zero | yes | health monitor |
| 7 | Staleness — source stops publishing, old events persist via preservation | flat | partly | drift: max start date not advancing |
| 8 | Semantic drift — selectors still match but mean something else | flat | no | golden fixtures + periodic re-fetch |

**Six of eight are invisible to volume monitoring.** Two were closed on
2026-08-31. The rest are ROADMAP items 2 and 3.

## 5. Data lifecycle

```
  registry (layer 0)
      │
      ▼
  scrapers ──── EventCreate[] ─────────────────────────────► layer 1
      │
      ▼
  validate ──── invariants; reject and log, never repair ──► layer 2
      │
      ▼
  deduplicate ─ within run, then across sources
      │
      ▼
  enrich ────── categories, geocoding (non-fatal)
      │
      ▼
  identify ──── content hash, deterministic sort ──────────► layer 5
      │
      ▼
  fingerprint ─ per-source shape ──────────────────────────► layer 2
      │
      ▼
  GATE ──────── vs. last known-good ───────────────────────► layer 4
      │                                    │
   pass                                  fail
      │                                    │
      ▼                                    ▼
  data/events.json                   quarantine + issue
      │                              production unchanged
      ▼
  Railway → API → Vercel → readers ────────────────────────► layer 6
```

The gate is the only new box, and it is the one that would have stopped Sept 14.

## 6. Design principles

Ten rules. Each exists because of something that actually happened.

1. **Never fabricate.** A missing value is a missing value. Absence is always
   cheaper than a plausible wrong answer.
2. **Measure shape, not volume.** The metric that moves during a real failure is
   rarely the count.
3. **Identity is content-derived and stable.** Nothing accumulates on top of a
   value that changes daily for no reason.
4. **Every assertion is executable or derived.** No hand-typed counts in prose.
   If a document claims something, a test should check it or it should be
   generated.
5. **One registry per fact.** Two hand-maintained lists of the same thing have
   already drifted; assume they always will.
6. **Runs are immutable and inspectable.** Debugging should be reading, not
   archaeology.
7. **Failing loud beats shipping quiet.** A gate that refuses to publish costs a
   day of staleness. Not having one cost a reader's trust.
8. **Repair is a first-class verb.** If fixing a source requires writing a
   throwaway script, the next person writes it again and nothing accretes.
9. **Scrapers are pure.** URL in, events out. Purity is what makes offline,
   deterministic tests possible.
10. **Degrade visibly.** The agents already degrade gracefully — missing API key,
    skip the step, return partial results. Graceful *and silent* is how the
    Sept 14 run passed every check it had.

### The accretion loop

The system should end every incident smarter than it started. Five artifacts
carry that, and each is a ROADMAP item rather than a good intention:

| after an incident | the system keeps | so that |
|---|---|---|
| a scraper broke | a golden fixture of the page that broke it | the bug can never return silently |
| a run was bad | its run record and fingerprint | the next diagnosis is reading, not archaeology |
| a source was diagnosed | an updated baseline | the system knows that source's normal better |
| a fix was found | a runbook entry in OPERATIONS.md | the next person executes instead of re-deriving |
| a fact was learned | an executable check | the fact cannot rot into a lie |

Prose alone does not accrete. It decays into a trap — which is exactly what
happened to the docs this system already had.

## 7. Known deviations

This document describes the target. Here is where the code differs today, so
that reading it does not mislead. Each links to its ROADMAP item.

| Layer | Deviation | Item |
|---|---|---|
| 0 | Source registry duplicated in `scrape.py` and `ci_monitor.py`; 4 scrapers unmonitored | 1 |
| 2 | Invariants exist; fingerprints and drift checks do not | 2 |
| 3 | Run records are 5 runs of bare counts | 5 |
| 4 | No gate — CI commits and pushes unconditionally | 3 |
| 5 | IDs regenerate every run; file order is nondeterministic | 4 |
| 6 | Some published numbers are hand-typed | 8 |
| — | No `cal` CLI; diagnosis and repair are ad-hoc scripts | 6 |
| — | `pytest` is documented but absent from `requirements.txt` | 9 |

If you change the code so that a row here is no longer true, delete the row.
A stale deviation list is the same failure as a stale README.
