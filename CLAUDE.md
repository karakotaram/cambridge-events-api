# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Live URLs

- **Domain**: `cambridgecalendar.com` (Vercel — frontend)
- **API**: `https://web-production-00281.up.railway.app` (Railway — this repo)
- **Frontend repo**: `~/cambridge-event-compass` (React/Vite, deployed on Vercel)

### Pages served by this API but accessible via the domain

These static HTML pages live in `static/` and are served by FastAPI. Vercel rewrites proxy them through to Railway so they appear on the main domain:

| Public URL | Route served by Railway | Source file |
|---|---|---|
| `cambridgecalendar.com/signup` | `/signup` | `static/onboarding/index.html` |
| `cambridgecalendar.com/admin` | `/admin` | `static/admin/index.html` |
| `cambridgecalendar.com/admin/featured` | `/admin/featured` | `static/admin/featured.html` |

The Vercel `vercel.json` in the frontend repo also proxies `/onboarding/*`, `/events/*`, and `/featured` to Railway so the pages' relative fetch calls work.

## Commands

```bash
# Run full scrape (all sources)
python scrape.py

# Run local-only scrapers (for sources blocked in CI)
python scrape_local.py

# Start API server
python -m src.api.main

# Run tests
pytest tests/

# Format code
black src/

# Lint
flake8 src/
```

## Architecture

### Two-Part System

1. **Scrapers** (`scrape.py`, `src/scrapers/`) - Collect events from 20+ Cambridge/Somerville venues
2. **API** (`src/api/main.py`) - FastAPI server deployed on Railway serving events + AI chat

### Scraper Architecture

- `BaseScraper` (`src/scrapers/base_scraper.py`) - Abstract base class all scrapers extend
  - `use_selenium=True` for JS-rendered sites, `False` for static HTML
  - Provides `fetch_html()`, `parse_html()`, `clean_text()`, image extraction
- Each venue has its own scraper in `src/scrapers/` implementing `scrape_events() -> List[EventCreate]`
- `ScraperOrchestrator` in `scrape.py` runs all scrapers, validates, deduplicates, saves to `data/events.json`

### CI vs Local Scraping

Some sources block GitHub's cloud IPs. These are handled specially:
- `CI_SKIP_SOURCES` in `scrape.py` lists blocked sources
- GitHub Actions runs `scrape.py` which preserves events from skipped sources
- Run `scrape_local.py` locally to update blocked sources (Harvard Book Store, Boston Swing Central, Aeronaut, Somerville Theatre)

### Data Flow

```
Scrapers → EventCreate → Validator → Deduplicator → Event (with ID) → data/events.json
                                                                            ↓
                                                              Railway auto-deploys on push
                                                                            ↓
                                                              API serves events + chat
                                                                            ↓
                                                         Vercel frontend fetches from API
                                                         (cambridgecalendar.com)
```

### API Features

- REST endpoints: `/events`, `/events/slim`, `/events/search`, `/stats`, `/sources`, `/categories`
- AI Chat (`/chat`): Uses Groq (openai/gpt-oss-120b) with 500 events in context
- Chat has age-appropriate guidance (toddlers → story time, not theater)
- Editor's Picks: `/featured` (GET/PUT), `/events/{id}/feature` (POST/DELETE)

### Onboarding & Email System

- **Signup flow** (`src/api/onboarding.py`, `static/onboarding/index.html`):
  - Step 1: User thumbs-up events from `/onboarding/sample-events`
  - Step 2: Email capture → `/onboarding/submit` creates user, computes preferences
- **Weekly digest emails** via Resend SDK (`src/services/email_service.py`)
- **Recommendation engine** (`src/services/recommendation.py`) — LightFM + content-based scoring
- **Admin dashboard** (`static/admin/index.html`) — user management, digest preview/override, email history
- **Tracking**: open pixel (`/onboarding/track/open/:id`), click redirects (`/onboarding/track/click/:id`)
- **Unsubscribe**: token-based via `/onboarding/unsubscribe/:token`

### Event Model

`EventCreate` (before ID) and `Event` (with ID) in `src/models/event.py`:
- Required: `title`, `description`, `start_datetime`, `source_url`, `source_name`
- Categories: music, arts and culture, food and drink, theater, lectures, sports, community, other
- `family_friendly: bool` used for kid-appropriate filtering

**Times are always naive Eastern wall clock.** A `field_validator` on both
models converts any tz-aware `start_datetime`/`end_datetime` to Eastern and
strips the offset — every venue is in Greater Boston, so a published time is an
Eastern time whether the source said so or not. Don't reintroduce offsets:
mixing aware and naive values raises `TypeError` on comparison and shifts
evening events onto the wrong day for anyone outside ET. Use `as_local_naive()`
in `src/api/main.py` when comparing a query parameter against an event.

**Never fall back to `datetime.now()` for a start time.** A scraper that can't
read a date must skip the event — a wrong date lands it on someone else's day.
`EventValidator` rejects any start time carrying seconds or microseconds, since
real listings are always on the minute.

## Adding a New Scraper

1. Create `src/scrapers/venue_name.py`
2. Extend `BaseScraper`, set `source_name`, `source_url`, `use_selenium`
3. Implement `scrape_events() -> List[EventCreate]`
4. Register in `scrape.py` (non-Selenium scrapers first, then Selenium scrapers)
5. If source blocks CI, add to `CI_SKIP_SOURCES` and `scrape_local.py`

## Agent System

6 automated agents in `src/agents/` for monitoring, quality, and discovery. See `src/agents/README.md` for full docs.

```bash
# Run individual agents
python -m src.agents.ci_monitor         # Source freshness tracking
python -m src.agents.health_monitor     # Broken scraper detection
python -m src.agents.enrichment         # Data quality improvement
python -m src.agents.source_discovery   # Find new venues (needs GROQ_API_KEY)
python -m src.agents.chat_quality --base-url http://localhost:8000  # Test chat

# Generate a scraper from a URL (needs ANTHROPIC_API_KEY)
python -m src.agents.scraper_generator --url URL --venue "Name" [--write] [--dry-run]
```

- `BaseAgent` (`src/agents/base_agent.py`) mirrors `BaseScraper` pattern
- Agents are integrated into `scrape.py` pipeline (enrichment after dedup, monitors after scrape)
- Agent failures are non-fatal — never block the scrape pipeline
- `GET /health/scrapers` API endpoint returns CI monitor report

## Deployment

- **API** hosted on Railway, auto-deploys from `main` branch
- **Frontend** hosted on Vercel at `cambridgecalendar.com`, auto-deploys from `main` of `cambridge-event-compass`
- Push `data/events.json` changes to update the live event database
- GitHub Actions runs daily at 6 AM UTC to refresh events
- Vercel rewrites in the frontend repo proxy `/signup`, `/admin`, `/onboarding/*`, `/events/*`, `/featured` to Railway

### Design System (Broadsheet theme)

The signup page (`static/onboarding/index.html`) uses the same editorial "Broadsheet" design as the frontend:
- Fonts: Playfair Display (headings), Source Sans 3 (body) — loaded via Google Fonts
- Colors: warm ivory background `hsl(40,33%,97%)`, dark navy foreground `hsl(240,27%,14%)`, rose-red accent `hsl(355,76%,56%)`
- Zero border-radius everywhere, double-border masthead, sharp editorial style
- When updating these pages, match this theme — not the generic app UI
