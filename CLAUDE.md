# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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
```

### API Features

- REST endpoints: `/events`, `/events/search`, `/stats`, `/sources`, `/categories`
- AI Chat (`/chat`): Uses Groq (llama-3.3-70b) with 500 events in context
- Chat has age-appropriate guidance (toddlers → story time, not theater)

### Event Model

`EventCreate` (before ID) and `Event` (with ID) in `src/models/event.py`:
- Required: `title`, `description`, `start_datetime`, `source_url`, `source_name`
- Categories: music, arts and culture, food and drink, theater, lectures, sports, community, other
- `family_friendly: bool` used for kid-appropriate filtering

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

- API hosted on Railway, auto-deploys from `main` branch
- Push `data/events.json` changes to update the live database
- GitHub Actions runs daily at 6 AM UTC to refresh events
