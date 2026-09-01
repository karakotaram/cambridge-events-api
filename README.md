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

.venv/bin/python -m uvicorn src.api.main:app --port 8000   # API on :8000, docs at /docs
.venv/bin/python scrape.py                                  # full scrape (~20 min, needs Chrome)
.venv/bin/python -m pytest tests/ -q                        # tests
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
scrapers → validate → deduplicate → enrich → data/events.json
                                                    ↓
                                    Railway (API) → Vercel (frontend)
```

A GitHub Action re-scrapes daily at 06:00 UTC and commits the result. Pushing to
`main` deploys to production.

Each venue has a scraper in `src/scrapers/` extending `BaseScraper` and
implementing `scrape_events() -> List[EventCreate]`. `scrape.py` orchestrates
them. Events are validated against the invariants in `src/utils/validator.py`,
deduplicated across sources, and written to `data/events.json`.

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

Then register it in `scrape.py` **and** `src/agents/ci_monitor.py`, and add a
test backed by a saved HTML fixture rather than a live fetch.

## Event schema

`Event` in `src/models/event.py`. Required: `title`, `description`,
`start_datetime`, `source_url`, `source_name`.

Categories: `music`, `arts and culture`, `food and drink`, `theater`, `lectures`,
`sports`, `community`, `other`.

**All datetimes are naive Eastern wall clock.** Every venue is in Greater Boston,
so a published time is an Eastern time whether the source said so or not; the
model enforces this. See [docs/ARCHITECTURE.md § Layer 2](docs/ARCHITECTURE.md#layer-2--contract).

## License

Proprietary — Cambridge-Somerville Event Aggregation Project.
