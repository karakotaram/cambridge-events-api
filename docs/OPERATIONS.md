# Operations

Runbooks for running, diagnosing, repairing, and deploying this system.

Every recipe here has been executed against this repo. Where a future `cal`
command ([ROADMAP item 6](ROADMAP.md#6--the-cal-cli)) would replace a manual
recipe, that is noted — until it exists, the manual version is the real one.

---

## The map

| What | Where | Deploys from |
|---|---|---|
| Scrapers + API (this repo) | Railway, `https://web-production-00281.up.railway.app` | `main`, automatically |
| Frontend | Vercel, `cambridgecalendar.com` | `main` of `~/Projects/cambridge-event-compass` |
| Event data | `data/events.json`, served by the API | committed to `main` |
| Daily refresh | `.github/workflows/scrape-events.yml`, 06:00 UTC | commits and pushes unconditionally — see [ROADMAP item 3](ROADMAP.md#3--the-gate) |

The frontend proxies `/signup`, `/admin`, `/onboarding/*`, `/events/*`, and
`/featured` through to Railway, so those pages appear on the main domain while
being served by this repo.

**Pushing to `main` deploys to production.** There is no staging. Redeploy takes
about two minutes; watch `/health` until `total_events` changes.

## Local setup

```bash
python3.12 -m venv .venv          # 3.12; production pins 3.11, avoid 3.13+
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install pytest      # documented but not yet in requirements.txt
```

`.venv/` is gitignored. Use `.venv/bin/python` explicitly rather than relying on
an activated shell — activation does not survive between tool calls.

```bash
.venv/bin/python -m pytest tests/ -q                       # tests
.venv/bin/python -m uvicorn src.api.main:app --port 8199   # API on :8199
.venv/bin/python scrape.py                                 # full scrape (slow: ~20 min, needs Chrome)
```

---

## Runbook: a reader reports a wrong date

This is the highest-value runbook in the file, because it is the failure that
reaches readers. Recovered from the 2026-08-31 incident.

**1. Reproduce against production, not your checkout.** Your local
`data/events.json` may be far behind — it was 26 commits stale when this incident
was diagnosed.

```bash
git fetch origin && git rev-list --count main..origin/main   # 0 means you are current
```

**2. Get the day's actual contents from the live API.**

```bash
curl -s "https://web-production-00281.up.railway.app/events/slim?limit=5000" \
| python3 -c "
import sys, json
from collections import Counter
evs = json.load(sys.stdin)
c = Counter(e['start_datetime'][:10] for e in evs)
for d, n in sorted(c.items())[:40]: print(d, n)
print('max any day:', max(c.values()))
"
```

A day standing far above its neighbours is the tell. Healthy days run 40–70.

**3. Look at the *times*, not just the count.** This is the step that finds the
cause:

```bash
curl -s ".../events/slim?limit=5000" | python3 -c "
import sys, json
from collections import Counter
evs = [e for e in json.load(sys.stdin) if e['start_datetime'].startswith('2026-09-14')]
print(Counter(e['start_datetime'][11:] for e in evs))
"
```

More than a handful of events sharing one exact timestamp means a scraper substituted a clock
reading for a date it could not parse. Sub-minute precision (`13:29:26.288025`)
confirms it — real listings are always on the minute.

**4. Identify the source and check it against the venue's own page.**

```bash
curl -s ".../events?limit=5000" | python3 -c "
import sys, json
from collections import Counter
bad = [e for e in json.load(sys.stdin) if e['start_datetime'].startswith('2026-09-14T13:29:26')]
print(Counter(e['source_name'] for e in bad))
print(bad[0]['source_url'])
"
```

Open that `source_url` and compare. If the venue's page is right and ours is
wrong, the scraper is the bug.

**5. Fix the scraper so it skips rather than guesses**, add a test, then repair
the data (next runbook).

> `cal doctor` will collapse steps 1–4 into one command.

## Runbook: repair one source

Re-scrape a single source and splice it into `data/events.json`, leaving every
other source untouched. Use after fixing a scraper, when you do not want to wait
for the 06:00 UTC job or run a full 20-minute scrape.

```python
# repair.py — delete when cal repair exists
import json, uuid
from collections import defaultdict
from src.scrapers.cambridge_gov import CambridgeGovScraper   # <- the one you fixed
from src.utils.validator import EventValidator
from src.utils.deduplicator import EventDeduplicator
from src.models.event import Event, EventCreate

SOURCE, PATH = "City of Cambridge", "data/events.json"

existing = json.load(open(PATH))
others = [e for e in existing if e.get("source_name") != SOURCE]
print(f"keeping {len(others)}, replacing {len(existing) - len(others)}")

raw = CambridgeGovScraper().run()
validator = EventValidator()
valid, rejected = [], defaultdict(int)
for ev in raw:
    ev = validator.clean_and_enhance(ev)
    ok, err = validator.validate_event(ev)
    valid.append(ev) if ok else rejected.__setitem__(err, rejected[err] + 1)
print(f"scraped {len(raw)}, valid {len(valid)}, rejected {dict(rejected)}")

kept = EventDeduplicator.deduplicate_events(valid)

# drop anything duplicating a source we are keeping (bucket by day; the
# comparison is O(n*m) and does fuzzy title matching)
by_day = defaultdict(list)
for e in others:
    try:
        oc = EventCreate(**{k: v for k, v in e.items() if k != "id"})
    except Exception:
        continue
    by_day[EventDeduplicator.normalize_datetime(oc.start_datetime).date()].append(oc)

final = [e for e in kept
         if not any(EventDeduplicator.are_duplicates(e, o)
                    for o in by_day.get(EventDeduplicator.normalize_datetime(e.start_datetime).date(), ()))]

out = [Event(id=str(uuid.uuid4()), **e.model_dump()).model_dump(mode="json") for e in final] + others
json.dump(out, open(PATH, "w"), indent=2, default=str)
print(f"wrote {len(out)}")
```

Run it from the repo root (`src` must be importable), then verify with the
pre-push checklist below.

**Do not skip the dedup-against-others step.** Several venues are covered by both
their own scraper and an aggregator, and re-splicing without it produces visible
double listings.

## Runbook: a source went quiet

```bash
.venv/bin/python -m src.agents.health_monitor   # count vs. 5-run rolling average
.venv/bin/python -m src.agents.ci_monitor       # freshness / staleness
curl -s ".../health/scrapers"                   # the CI monitor report, live
```

Two known limits of these, both being addressed in
[ROADMAP items 1–2](ROADMAP.md):

- The rolling window is **5 runs**. A source that degrades over several days has
  its broken state absorbed into the baseline before anything alerts.
- Four registered scrapers are **not in `ci_monitor.REGISTERED_SOURCES`** at all —
  Harvard GSD, Museum of Science, Regent Theatre, The Sinclair. They are
  unmonitored. If one of those is your suspect, the monitors will not help; run
  it directly:

```bash
.venv/bin/python -c "
from src.scrapers.the_sinclair import TheSinclairScraper
evs = TheSinclairScraper().run()
print(len(evs))
for e in evs[:5]: print(' ', e.start_datetime, e.title[:60])
"
```

## Runbook: before you push data

Run all four. Each corresponds to a failure that has actually shipped.

```bash
python3 -c "
import json, re
from collections import Counter
evs = json.load(open('data/events.json'))
print('total', len(evs))

# 1. fabricated dates — sub-minute precision only comes from a clock reading
print('clock-stamped:', sum(1 for e in evs if re.search(r'T\d{2}:\d{2}:(?!00)', e['start_datetime'])))

# 2. timestamp pileups — legit max is 8 for one source; a big number means a date fallback fired
print('largest timestamp pileup:', Counter(e['start_datetime'] for e in evs).most_common(1))

# 3. tz-aware values — must be zero, everything is naive Eastern
A = re.compile(r'([+-]\d{2}:?\d{2}|Z)\$')
print('tz-aware:', sum(1 for e in evs for f in ('start_datetime','end_datetime')
                       if isinstance(e.get(f), str) and A.search(e[f])))

# 4. day distribution — a spike is the Sept 14 signature
c = Counter(e['start_datetime'][:10] for e in evs)
print('max any day:', max(c.values()), '| healthy range 40-70')
"
```

Then the API must actually serve it:

```bash
.venv/bin/python -m uvicorn src.api.main:app --port 8199 &
sleep 8
for p in /health /stats "/events?start_date=2026-09-14T00:00:00&end_date=2026-09-14T23:59:59"; do
  printf "%-70s " "$p"; curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:8199$p"
done
```

`/stats` and the date-filtered `/events` both returned 500 in production for an
unknown length of time. They are in this list because nothing else was checking
them.

> `cal check` will replace this whole section.

## Runbook: deploy and verify

```bash
.venv/bin/python -m pytest tests/ -q
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

- **Your checkout is probably stale.** The daily job commits to `main`. Always
  `git fetch` before diagnosing anything.
- **Local `data/events.json` is not what production serves** unless you are
  current with `origin/main`.
- **`Event.category` is a `str`, not an enum.** Both models set
  `use_enum_values = True`. Calling `.value` on it raises `AttributeError` — this
  is what made `/stats` return 500.
- **Mixed tz-awareness raises `TypeError` on comparison.** The model now
  normalizes to naive Eastern, but any datetime arriving from a query parameter
  still needs `as_local_naive()` in `src/api/main.py`.
- **Event IDs change on every scrape.** Do not build anything that assumes an ID
  survives the night ([ROADMAP item 4](ROADMAP.md#4--stable-event-identity)).
- **`CI_SKIP_SOURCES` sources are preserved, not re-scraped**, by CI. Their events
  can go stale for weeks without any signal. Run `scrape_local.py` to refresh
  them.
- **Selenium scrapers die mid-run and the pipeline continues.** A source silently
  returning a third of its events is the normal shape of that failure.
- **The daily job pushes whatever the scrape produced.** Until
  [ROADMAP item 3](ROADMAP.md#3--the-gate) exists, a bad scrape reaches readers
  within minutes.
