# CLAUDE.md

Forty hostile HTML sources go in. One trustworthy JSON file comes out, and an API
serves it to a public calendar at cambridgecalendar.com.

**Every scraper will break, and most breakage is silent.** The system's job is not
to scrape correctly — it is to make breakage loud and non-shipping. Read that
sentence before any structural change; the design follows from it.

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

4. **No hand-typed counts in documentation.** If a number describes the data, it
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
.venv/bin/python -m pytest tests/ -q                        # tests
.venv/bin/python -m uvicorn src.api.main:app --port 8199    # API locally
.venv/bin/python scrape.py                                  # full scrape, ~20 min, needs Chrome
.venv/bin/python scrape_local.py                            # only the CI-blocked sources
.venv/bin/python -m src.agents.health_monitor               # per-source count vs. rolling average
.venv/bin/python -m src.agents.ci_monitor                   # source freshness
.venv/bin/python -m flake8 --max-line-length=200 src/       # lint (repo is not black-formatted)
```

`pytest`, `black`, and `flake8` are installed in `.venv` but are **not** in
`requirements.txt`.

## Traps

Full list with detail in [docs/OPERATIONS.md](docs/OPERATIONS.md#known-traps).
The five that cost the most time:

- **Your checkout is probably stale.** The daily job commits to `main`. Run
  `git fetch origin && git rev-list --count main..origin/main` before diagnosing
  anything — local `data/events.json` was 26 commits behind during the last
  incident.
- **`Event.category` is a `str`, not an enum** (`use_enum_values = True` on both
  models). `.value` raises `AttributeError`; that is what made `/stats` 500.
- **Event IDs change on every scrape.** ~88% get a fresh UUID daily. Do not build
  anything that assumes an ID survives the night
  ([ROADMAP item 4](docs/ROADMAP.md#4--stable-event-identity)).
- **Four registered scrapers are unmonitored** — Harvard GSD, Museum of Science,
  Regent Theatre, The Sinclair. `ci_monitor.REGISTERED_SOURCES` has drifted from
  `scrape.py` ([ROADMAP item 1](docs/ROADMAP.md#1--one-source-registry)).
- **The daily job ships whatever the scrape produced.** There is no gate
  ([ROADMAP item 3](docs/ROADMAP.md#3--the-gate)).

## Structure

Only the parts that are not obvious from `ls`:

- `src/scrapers/base_scraper.py` — `BaseScraper`; `use_selenium=True` for
  JS-rendered sites. Prefer `False`: plain `requests` is faster and does not die
  mid-run, which is the normal shape of a Selenium failure.
- `scrape.py` — `ScraperOrchestrator`: run all → validate → dedupe → enrich →
  assign IDs → write `data/events.json`. Also holds `CI_SKIP_SOURCES`, sources
  that block GitHub's IPs; CI preserves their existing events rather than
  re-scraping, so they can silently go stale for weeks.
- `src/models/event.py` — `EventCreate` (no ID) and `Event` (with ID). Required:
  `title`, `description`, `start_datetime`, `source_url`, `source_name`.
  Categories: music, arts and culture, food and drink, theater, lectures, sports,
  community, other.
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
4. Register in `scrape.py` (non-Selenium first) **and** add it to
   `REGISTERED_SOURCES` in `src/agents/ci_monitor.py`, or it will be unmonitored.
   These two lists are meant to become one.
5. If the source blocks CI, add it to `CI_SKIP_SOURCES` and `scrape_local.py`.
6. Add a test with a saved HTML fixture, not a live fetch.

## Design system (Broadsheet)

`static/onboarding/index.html` and the admin pages use the same editorial theme
as the frontend. Match it rather than generic app UI:

- Playfair Display (headings), Source Sans 3 (body), via Google Fonts
- Warm ivory `hsl(40,33%,97%)`, dark navy `hsl(240,27%,14%)`, rose-red accent
  `hsl(355,76%,56%)`
- Zero border-radius throughout, double-border masthead, sharp editorial style
