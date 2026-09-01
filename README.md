# Cambridge Calendar

Event aggregation for Cambridge and Somerville, Massachusetts. Forty scrapers
collect listings from municipal calendars, libraries, theaters, music venues, and
community organizations into one dataset, served by a FastAPI backend to a public
calendar at **[cambridgecalendar.com](https://cambridgecalendar.com)**.

This repository is the scrapers, the data, and the API. The frontend lives in
[`cambridge-event-compass`](https://github.com/karakotaram/cambridge-event-compass).

---

## Quick start

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
alias cal='.venv/bin/python -m src.cli'

cal doctor                                                  # is anything wrong?
cal sources                                                 # what we scrape, and is it fresh?
.venv/bin/python -m uvicorn src.api.main:app --port 8000    # API on :8000, docs at /docs
.venv/bin/python scrape.py                                  # full scrape (~20 min, needs Chrome)
.venv/bin/python -m pytest tests/ -q                        # tests (offline, ~2s)
```

The public API is live at `https://web-production-00281.up.railway.app` and
documented in **[docs/API.md](docs/API.md)**.

## Documentation

| | |
|---|---|
| **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** | How the system is built and why — the layers, the invariants, the failure taxonomy |
| **[docs/OPERATIONS.md](docs/OPERATIONS.md)** | Runbooks: diagnose a bad date, repair a source, deploy, roll back |
| **[docs/ROADMAP.md](docs/ROADMAP.md)** | What is being built next, and the evidence for each item |
| **[docs/API.md](docs/API.md)** | Public API reference |
| **[CLAUDE.md](CLAUDE.md)** | Entry point for coding agents |
| **[src/agents/README.md](src/agents/README.md)** | The monitoring and quality agents |

`docs/archive/` holds superseded documents, including the original PRD. They are
kept for history; their numbers are stale.

## How it works

```
registry → scrapers → validate → deduplicate → enrich → identify
                                                            ↓
                                                    fingerprint → GATE
                                                            ↓         ↓
                                              data/events.json    quarantine
                                                            ↓     + issue
                                        Railway (API) → Vercel (frontend)
```

Every scraper eventually breaks, usually silently, so the system's job is to make
breakage **loud** and **non-shipping**. A run that violates the contract in
`src/quality/` is quarantined instead of published, and readers keep yesterday's
data — a much smaller harm than today's wrong data.

A GitHub Action re-scrapes daily at 06:00 UTC and commits only if the gate
passes. Pushing to `main` deploys to production.

Sources are declared once in `src/sources.py`; `scrape.py`, monitoring, and the
CLI all derive from it. Each venue's scraper lives in `src/scrapers/`, extends
`BaseScraper`, and implements `scrape_events() -> List[EventCreate]`.

## Contributing a scraper

See [CLAUDE.md § Adding a new scraper](CLAUDE.md#adding-a-new-scraper) for the
full checklist. The short version:

```python
from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate

class MyVenueScraper(BaseScraper):
    def __init__(self):
        super().__init__(
            source_name="My Venue",
            source_url="https://example.com/events",
            use_selenium=False,   # prefer False; plain requests is faster and more reliable
        )

    def scrape_events(self) -> list[EventCreate]:
        soup = self.parse_html(self.fetch_html(self.source_url))
        events = []
        for row in soup.select(".event"):
            start = parse_the_date(row)
            if start is None:
                continue          # skip, never guess — a wrong date is worse than a missing event
            events.append(EventCreate(...))
        return events
```

Then add one row to `src/sources.py` — that is the whole registration — and a
test backed by a saved fixture (`cal scrape "<name>" --save-fixture`) rather than
a live fetch.

## Event schema

`Event` in `src/models/event.py`. Required: `title`, `description`,
`start_datetime`, `source_url`, `source_name`.

Categories: `music`, `arts and culture`, `food and drink`, `theater`, `lectures`,
`sports`, `community`, `other`.

**All datetimes are naive Eastern wall clock.** Every venue is in Greater Boston,
so a published time is an Eastern time whether the source said so or not; the
model enforces this.

**Event ids are a content hash**, stable across scrapes, so click history joins,
`git diff data/events.json` reads as a changelog, and `git log -S<id>` answers
"when did this event's time change?". Mint them only via `Event.from_create()`.
See [docs/ARCHITECTURE.md § Layer 5](docs/ARCHITECTURE.md#layer-5--state).

## License

Proprietary — Cambridge-Somerville Event Aggregation Project.
