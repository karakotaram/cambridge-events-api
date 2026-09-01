# CLAUDE.md

Forty hostile HTML sources go in. One trustworthy JSON file comes out, and an API
serves it to a public calendar at cambridgecalendar.com.

**Every scraper will break, and most breakage is silent.** The system's job is not
to scrape correctly — it is to make breakage loud and non-shipping. Read that
sentence before any structural change; the design follows from it.

## Start here

```bash
alias cal='.venv/bin/python -m src.cli'
cal doctor          # what is wrong right now — run this first, always
```

One verb per layer of the system. `cal sources` (what we scrape),
`cal scrape <name>` (run one, write nothing), `cal check` (invariants + drift),
`cal runs` / `cal run <id>` (what a scrape did), `cal diff [--live]`,
`cal repair <name>` (re-scrape one source and splice it in).

## Where to look

| Question | Document |
|---|---|
| Why is the system shaped this way? What are the layers and invariants? | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| How do I diagnose a bad date, repair a source, deploy, roll back? | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| What is being built next, and what evidence justifies it? | [docs/ROADMAP.md](docs/ROADMAP.md) |
| What does the public API return? | [docs/API.md](docs/API.md) |
| What do the monitoring agents do? | [src/agents/README.md](src/agents/README.md) |

`docs/archive/` holds superseded documents. Do not trust their numbers.

## Rules that must not be broken

These are load-bearing. Each exists because of a specific failure.

1. **Never fabricate a date.** A scraper that cannot read a date skips the event.
   A missing event costs one reader one listing; a wrong date costs every reader
   that day's trust.

   ```python
   # never
   start_datetime = parsed if parsed else current_date
   # always
   if parsed is None:
       logger.warning(f"Skipping '{title}' - no parseable date ({url})")
       continue
   ```

   `EventValidator` rejects any start time carrying seconds or microseconds —
   real listings are on the minute, so sub-minute precision only ever comes from
   a clock reading. This is what put 117 events on one day in Aug 2026.

2. **All times are naive Eastern wall clock.** A `field_validator` on `Event` and
   `EventCreate` converts tz-aware values and strips the offset. Do not
   reintroduce offsets: mixing aware and naive raises `TypeError` on comparison
   and moves evening events across days. Query parameters still need
   `as_local_naive()` in `src/api/main.py`.

3. **Scrapers are pure.** URL in, `EventCreate[]` out. No file writes, no globals,
   no clock. Purity is what makes offline fixture tests possible.

4. **Never mint an event id by hand.** `Event.from_create()` is the only
   supported way; ids are a content hash of `source_name | source_url |
   start_datetime | normalized title`. `uuid4()` would break the interaction
   join, the diff, and the git changelog.

5. **Add a source in exactly one place.** `src/sources.py` is the registry;
   `scrape.py` and `ci_monitor` both derive from it. Two hand-kept lists drifted
   once and left four scrapers unmonitored.

6. **Three things block a publish, and only one is tunable.** Catastrophic
   collapse (the run would delete most of the calendar) and invariant violations
   are absolute. Drift is source-relative and governed by `GATE_DRIFT`. Do not
   make the first two configurable.

7. **No hand-typed counts in documentation.** If a number describes the data, it
   must be generated or checked by a test. Typed numbers rot — the archived
   integration guide still claims 668 events against today's ~2,970.

## Where things run

| | |
|---|---|
| **Frontend** | `cambridgecalendar.com` — Vercel, from `main` of `~/Projects/cambridge-event-compass` |
| **API + data** | `https://web-production-00281.up.railway.app` — Railway, from `main` of this repo |
| **Daily refresh** | `.github/workflows/scrape-events.yml`, 06:00 UTC, commits and pushes unconditionally |

**Pushing to `main` deploys to production.** No staging; redeploy takes ~2 min.

Static pages in `static/` are served by FastAPI and proxied onto the main domain
by the frontend's `vercel.json`:

| Public URL | Route | Source |
|---|---|---|
| `/signup` | `/signup` | `static/onboarding/index.html` |
| `/admin` | `/admin` | `static/admin/index.html` |
| `/admin/featured` | `/admin/featured` | `static/admin/featured.html` |

`vercel.json` also proxies `/onboarding/*`, `/events/*`, and `/featured`.

## Commands

Use `.venv/bin/python` explicitly — shell activation does not persist between
tool calls. The venv is Python 3.12; production pins 3.11.

```bash
.venv/bin/python -m pytest tests/ -q                        # tests (offline, ~2s)
.venv/bin/python -m uvicorn src.api.main:app --port 8199    # API locally
.venv/bin/python scrape.py                                  # full scrape, ~20 min, needs Chrome
.venv/bin/python scrape.py --force                          # publish past a failing gate
.venv/bin/python scrape_local.py                            # only the CI-blocked sources
.venv/bin/python -m flake8 --max-line-length=200 src/       # lint (repo is not black-formatted)
```

Tests are offline — scrapers parse saved pages in `tests/fixtures/`, and an
`offline` fixture makes any network call in a test raise. Capture a new fixture
with `cal scrape "<source>" --save-fixture`.

## Traps

Full list in [docs/OPERATIONS.md](docs/OPERATIONS.md#known-traps). The five that
cost the most time:

- **Your checkout is probably stale.** The daily job commits to `main`.
  `cal doctor` checks this for you.
- **`Event.category` is a `str`, not an enum** (`use_enum_values = True` on both
  models). `.value` raises `AttributeError`; that is what made `/stats` 500.
- **A source can look alive and contribute nothing.** If everything it returns is
  over 30 days old, `EventValidator` drops all of it. Check a run record's
  `rejected` counts, not just the scraper's status.
- **18 of 42 sources currently contribute zero events.** Diagnosed, not fixed —
  three separate causes. See
  [ROADMAP § What `doctor` found](docs/ROADMAP.md#what-doctor-found).
- **Drift does not block yet** (`GATE_DRIFT=report`). Invariants do, from day one.

## Structure

Layered — each layer owns one kind of fact and has one source of truth. See
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for why.

| Layer | Code | Data |
|---|---|---|
| 0 Sources | `src/sources.py` — the registry, the only place a source is described | — |
| 1 Scrapers | `src/scrapers/` — pure: URL in, `EventCreate[]` out | `tests/fixtures/` |
| 2 Contract | `src/quality/invariants.py` (absolute), `src/quality/fingerprint.py` (source-relative drift) | `data/fingerprints.json` |
| 3 Run | `src/quality/run_record.py` | `data/runs/` |
| 4 Gate | `src/quality/gate.py` — decides whether a run may ship | `data/quarantine/` |
| 5 State | `src/utils/storage.py`, `src/models/event.py` | `data/events.json` |
| 6 Surface | `src/api/` | — |

Other things worth knowing:

- `scrape.py` orchestrates: run all → validate → dedupe → enrich → assign ids →
  **gate** → write. A blocked run exits non-zero and writes nothing.
- `use_selenium=True` exists but prefer `False`. Plain `requests` is faster and
  does not die mid-run, which is the normal shape of a Selenium failure.
- `CI_SKIP_SOURCES` is derived from `runs_in_ci=False` in the registry. CI
  preserves those sources' events rather than re-scraping, so they go stale
  silently; `scrape_local.py` refreshes them.
- `src/agents/` — six non-fatal monitoring and quality agents. They degrade
  silently when a key is missing, so treat "the step was skipped" as a finding.

## Adding a new scraper

1. Create `src/scrapers/venue_name.py`, extend `BaseScraper`, set `source_name`,
   `source_url`, `use_selenium`.
2. Implement `scrape_events() -> List[EventCreate]`. Prefer a machine-readable
   date on the listing page over parsing a detail page — but verify it: the
   Cambridge.gov `<time datetime>` attribute is a 12-hour clock with no meridiem,
   so 5 PM is written `05:00:00`.
3. Skip any event you cannot date. Never guess.
4. Add one row to `src/sources.py`. That is the whole registration —
   `scrape.py`, `ci_monitor`, and `cal` all derive from it. Set
   `runs_in_ci=False` if the venue blocks GitHub's IP ranges.
5. `cal scrape "<name>"` to see what it produces and whether it satisfies the
   invariants, then `--save-fixture` and add a test.

## Design system (Broadsheet)

`static/onboarding/index.html` and the admin pages use the same editorial theme
as the frontend. Match it rather than generic app UI:

- Playfair Display (headings), Source Sans 3 (body), via Google Fonts
- Warm ivory `hsl(40,33%,97%)`, dark navy `hsl(240,27%,14%)`, rose-red accent
  `hsl(355,76%,56%)`
- Zero border-radius throughout, double-border masthead, sharp editorial style

## Frontend

`~/Projects/cambridge-event-compass` (React/Vite, Vercel). Its `vercel.json`
proxies the routes above to Railway.
