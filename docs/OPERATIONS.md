# Operations

Runbooks for running, diagnosing, repairing, and deploying this system.

Every recipe here has been executed against this repo.

Almost everything runs through `cal` (`src/cli.py`), which has one verb per layer
of [the architecture](ARCHITECTURE.md#3-the-tower):

```bash
alias cal='.venv/bin/python -m src.cli'     # or python -m src.cli

cal doctor          # what is wrong right now                    (all layers)
cal sources         # what we scrape, counts, freshness, notes   (layer 0)
cal scrape <name>   # run one scraper, show what it would produce (layer 1)
cal check [<name>]  # invariants and drift on current state      (layer 2)
cal runs            # recent scrape runs
cal run <id>        # one run in full                            (layer 3)
cal diff [--live]   # what changed vs HEAD, or vs production     (layer 5)
cal repair <name>   # re-scrape one source and splice it in      (layer 5)
```

**Start with `cal doctor`.** It is the only command you need to remember.

---

## The map

| What | Where | Deploys from |
|---|---|---|
| Scrapers + API (this repo) | Railway, `https://web-production-00281.up.railway.app` | `main`, automatically |
| Frontend | Vercel, `cambridgecalendar.com` | `main` of `~/Projects/cambridge-event-compass` |
| Event data | `data/events.json`, served by the API | committed to `main` |
| Daily refresh | `.github/workflows/scrape-events.yml`, 06:00 UTC | commits only if the gate passes; a blocked run quarantines and opens an issue |

The frontend proxies `/signup`, `/admin`, `/onboarding/*`, `/events/*`, and
`/featured` through to Railway, so those pages appear on the main domain while
being served by this repo.

**Pushing to `main` deploys to production.** There is no staging. Redeploy takes
about two minutes; watch `/health` until `total_events` changes.

## Local setup

```bash
python3.12 -m venv .venv          # 3.12; production pins 3.11, avoid 3.13+
.venv/bin/pip install -r requirements.txt   # includes pytest, flake8, black
alias cal='.venv/bin/python -m src.cli'
```

`.venv/` is gitignored. Use `.venv/bin/python` explicitly rather than relying on
an activated shell — activation does not survive between tool calls.

```bash
.venv/bin/python -m pytest tests/ -q                       # tests
.venv/bin/python -m uvicorn src.api.main:app --port 8199   # API on :8199
.venv/bin/python scrape.py                                 # full scrape (slow: ~20 min, needs Chrome)
.venv/bin/python scrape.py --force                         # publish past a failing gate
```

Tests are offline: scrapers are parsed against saved pages in `tests/fixtures/`,
and an `offline` fixture makes any outbound HTTP call in a test raise. Capture a
new fixture with `cal scrape "<source>" --save-fixture`.

---

## Runbook: a reader reports a wrong date

The highest-value runbook here, because it is the failure that reaches readers.

```bash
git fetch origin && git rev-list --count main..origin/main   # 0 = your checkout is current
cal doctor --live
```

`doctor` checks the invariants and per-source drift, flags a stale checkout,
compares local event count against production, and probes the endpoints. During
the 2026-08-31 incident it would have printed, in about two seconds:

```
✗ City of Cambridge: 117 start times carry seconds/microseconds — a clock reading, not a listing
✗ City of Cambridge: 117 events share the exact start 2026-09-14T13:29:26.288025
✗ City of Cambridge: date_span_days 16 vs baseline 63 — reaching less far ahead
✗ GET /stats -> HTTPError: 500
! local checkout is 26 commits behind origin/main
```

Each of those took minutes to find by hand. All are mechanically derivable.

Then narrow it down:

```bash
cal check "City of Cambridge"      # that source's invariants and drift
cal run <run-id>                   # what that scrape did: per-scraper yield, rejections, gate
cal scrape "City of Cambridge"     # re-run the scraper now, see what it produces, write nothing
```

`cal scrape` prints the parsed events, runs the invariants over them, and shows
the shape against baseline — so you can compare our output against the venue's
own page directly.

Reading the signals:

- **Many events sharing one exact timestamp** means a scraper substituted a clock
  reading for a date it could not parse. Sub-minute precision (`13:29:26.288025`)
  confirms it — real listings are always on the minute.
- **A short `date_span_days`** means the run was truncated: pagination broke, or a
  browser died mid-run.
- **A day standing far above its neighbours** is the same fabrication seen from
  the other end. Healthy days run 40–70.

Fix the scraper so it *skips* rather than guesses, add a fixture-backed test
(`cal scrape <name> --save-fixture`), then repair the data.

## Runbook: repair one source

Re-scrape a single source and splice it in, leaving every other source untouched.
Use after fixing a scraper, when you do not want to wait for 06:00 UTC or run a
full 20-minute scrape.

```bash
cal repair "City of Cambridge" --dry-run   # scrape, validate, dedupe, report — write nothing
cal repair "City of Cambridge"             # same, then write data/events.json
cal check                                  # confirm
git add data/events.json && git commit && git push
```

It validates, dedupes within the source *and* against every other source, runs
the invariants, and refuses to write if any are violated (`--force` overrides).

The cross-source dedup step is not optional: several venues are covered by both
their own scraper and an aggregator, and splicing without it produces visible
double listings.

## Runbook: a source went quiet

```bash
cal sources             # every source with its event count, furthest-out date, last scrape
cal scrape "The Sinclair"   # run just that one and see what comes back
cal runs                # was it failing in recent runs?
cal run <run-id>        # per-scraper status and error for that run
```

`cal sources` marks any source contributing zero events. There are three
different reasons a source goes quiet, and they need different fixes — `cal run`
distinguishes them:

| what you see | cause | fix |
|---|---|---|
| scraper status `failed` in the run record | the venue changed or is blocking us | fix the scraper |
| scrapes fine locally, `failed` only in CI | environment — a missing browser, or the venue blocking GitHub's IPs | fix the workflow, or add `runs_in_ci=False` in `src/sources.py` |
| status `ok` with events, but none in the data | validation rejected them all — check the run record's `rejected` counts | usually the scraper is returning stale or misdated events |

That third case is easy to miss. Theatre at First returns seven events every run,
all of them more than 30 days old, all correctly dropped by `EventValidator` —
so it looks alive and contributes nothing.

## Runbook: before you push data

```bash
cal check                                # invariants + drift
.venv/bin/python -m pytest tests/ -q     # includes the data invariants
cal diff                                 # exactly what changed vs HEAD
```

`cal diff` is meaningful because event ids are stable: it reports added, removed,
and field-level changes rather than the whole file. A normal daily run is tens of
changes. Thousands means something rotated every id — which should not be
possible, and is worth stopping to understand.

The same checks run inside `scrape.py` as the gate, so a normal scrape enforces
them without anyone remembering to. This runbook is for hand-edited data.

## Runbook: deploy and verify

```bash
.venv/bin/python -m pytest tests/ -q
cal check
git push origin main          # Railway redeploys automatically, ~2 minutes
```

Poll until the new data is live, then verify the specific thing you changed:

```bash
for i in $(seq 1 15); do
  n=$(curl -s ".../health" | python3 -c "import sys,json;print(json.load(sys.stdin)['total_events'])")
  echo "$n"; [ "$n" = "<expected>" ] && break; sleep 20
done
```

`/health` reporting the old count means the deploy has not landed yet — not that
the fix failed.

## Runbook: roll back

Data and code deploy together from the same commit, so rollback is a git
operation:

```bash
git revert <sha> && git push origin main
```

To roll back **only** the data while keeping a code fix:

```bash
git checkout <good-sha> -- data/events.json
git commit -m "Restore events.json from <good-sha>" && git push origin main
```

Prefer reverting to hand-editing `data/events.json`. The file is 92,000 lines and
a hand edit is not reviewable.

---

## Environment

| Variable | Used by | Required |
|---|---|---|
| `DATABASE_URL` | interactions, users, email logs | yes, in production |
| `GROQ_API_KEY` | `/chat`, enrichment, source discovery, chat quality | degrades gracefully |
| `ANTHROPIC_API_KEY` | scraper generator | only for that agent |
| `RESEND_API_KEY` | weekly digest email | only for email |
| `ADMIN_API_KEY` | `/analytics/interactions`, admin endpoints | for admin routes |
| `ADMIN_EMAIL` | user-submission notifications | optional |
| `POSTHOG_API_KEY`, `POSTHOG_HOST`, `POSTHOG_DEBUG` | analytics | optional |
| `API_BASE_URL` | agents calling the live API | optional |

Missing keys skip their feature rather than crashing. That is deliberate — and it
is also how a degraded run can look healthy, so treat "the step was skipped"
as a finding, not a non-event.

## Known traps

Things that have cost real time. Add to this list whenever something surprises
you — that is how this file earns its keep.

- **Your checkout is probably stale.** The daily job commits to `main`.
  `cal doctor` checks this for you.
- **`Event.category` is a `str`, not an enum.** Both models set
  `use_enum_values = True`. Calling `.value` on it raises `AttributeError` —
  that is what made `/stats` return 500.
- **Mixed tz-awareness raises `TypeError` on comparison.** The model normalizes
  to naive Eastern, but a datetime arriving from a query parameter still needs
  `as_local_naive()` in `src/api/main.py`.
- **`CI_SKIP_SOURCES` sources are preserved, not re-scraped**, so their events go
  stale for weeks with no signal. `cal sources` shows the age in "last scraped";
  run `scrape_local.py` to refresh them.
- **A source can look alive and contribute nothing.** If everything it returns is
  more than 30 days old, `EventValidator` drops all of it. Check the run record's
  `rejected` counts, not just the scraper's status.
- **Playwright scrapers need `playwright install` in CI.** Nine sources failed
  every night in GitHub Actions while working locally, because the workflow
  installed Chrome for Selenium but never the Playwright browsers.
- **Selenium scrapers die mid-run and the pipeline continues.** A source silently
  returning a third of its events is the normal shape of that failure — which is
  why `date_span_days` is a drift metric.
- **Never mint an event id by hand.** `Event.from_create()` is the only supported
  way; `uuid4()` would break the interaction join, the diff, and the changelog.
- **Do not record a fingerprint for a run that failed the gate.** That is exactly
  how a slow degradation becomes the baseline. `scrape.py` only records after a
  pass.

## When the gate blocks a run

A blocked run leaves production untouched and writes everything you need:

```bash
cal runs                                  # find the run id
cal run <run-id>                          # gate reasons, per-scraper detail, rejections
cat data/quarantine/<run-id>/report.txt   # the same report the issue carries
```

The report names which of three checks fired:

| check | tunable? | means |
|---|---|---|
| **catastrophic collapse** | no | the run would delete most of the calendar — usually mass scraper failure, not a data problem |
| **invariant violation** | no | an event is malformed: a fabricated date, a tz-aware value, chrome in a title |
| **drift** | yes (`GATE_DRIFT`) | a source's shape moved against its own baseline |

`data/quarantine/<run-id>/events.json` is the rejected output, kept because the
bad run is the evidence. Options, in order of preference:

1. **Fix the scraper**, then `cal repair <source>` — the usual case.
2. **The change is real and large** (a venue posted its whole spring season):
   re-run with `python scrape.py --force`, or dispatch the workflow with
   `force: true`.
3. **The threshold is wrong**: tune it in `src/quality/fingerprint.py` and say so
   in the commit. Do not disable the gate.
