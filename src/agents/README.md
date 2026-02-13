# Agent System

Automated monitoring, quality improvement, and venue discovery agents for the Cambridge Event Scraper.

## Agents

| # | Agent | Purpose | LLM Required | CLI |
|---|-------|---------|-------------|-----|
| 1 | **CI Monitor** | Track source freshness, detect stale/missing sources | No | `python -m src.agents.ci_monitor` |
| 2 | **Health Monitor** | Detect broken scrapers via rolling event count history | Optional (Groq for diagnosis) | `python -m src.agents.health_monitor` |
| 3 | **Enrichment** | Improve categories, family-friendly tags, cross-source dedup | Optional (Groq for categories) | `python -m src.agents.enrichment` |
| 4 | **Source Discovery** | Find new Cambridge/Somerville venues to scrape | Groq | `python -m src.agents.source_discovery` |
| 5 | **Scraper Generator** | Auto-generate scraper code from a venue URL | Anthropic | `python -m src.agents.scraper_generator --url URL --venue "Name"` |
| 6 | **Chat Quality** | Test the /chat endpoint with predefined queries | Optional (Groq for grading) | `python -m src.agents.chat_quality --base-url URL` |

## Environment Variables

| Variable | Used By | Purpose |
|----------|---------|---------|
| `GROQ_API_KEY` | Enrichment, Source Discovery, Chat Quality, Health Monitor | LLM calls (llama-3.3-70b) |
| `ANTHROPIC_API_KEY` | Scraper Generator | Code generation (Claude Sonnet) |
| `GH_TOKEN` / `GITHUB_TOKEN` | CI Monitor, Health Monitor, Source Discovery | GitHub issue creation via `gh` CLI |

## Pipeline Integration

Agents run automatically in the scrape pipeline (`scrape.py`):

1. **After deduplication**: Enrichment agent improves categories, family-friendly tags, and runs fuzzy cross-source dedup
2. **After scrape completes**: Health Monitor records event counts; CI Monitor checks source freshness

Both are wrapped in try/except — agent failures never block the scrape pipeline.

## API Endpoint

`GET /health/scrapers` — Returns the CI Monitor report (source freshness data).

## CI/CD

- **Daily** (`scrape-events.yml`): Runs health_monitor and ci_monitor after scraping, commits `data/scraper_health.json` and `data/agent_reports/`
- **Weekly** (`agent-source-discovery.yml`): Runs source discovery every Monday, creates GitHub issues for validated suggestions

## Data Files

| File | Created By | Purpose |
|------|-----------|---------|
| `data/scraper_health.json` | Health Monitor | Rolling 5-run history of event counts per source |
| `data/agent_reports/*.json` | All agents | Per-agent output reports |

## Scraper Generator Examples

```bash
# Analyze a venue page (no code generation)
python -m src.agents.scraper_generator --url "https://example.com/events" --venue "Example Venue" --dry-run

# Generate scraper code (prints to report)
python -m src.agents.scraper_generator --url "https://example.com/events" --venue "Example Venue"

# Generate and write to src/scrapers/
python -m src.agents.scraper_generator --url "https://example.com/events" --venue "Example Venue" --write
```

## Adding a New Agent

1. Create `src/agents/your_agent.py`
2. Extend `BaseAgent`, implement `execute() -> dict` (must return dict with `status` key)
3. Add `if __name__ == "__main__"` block for CLI usage
4. Use `self.llm_complete(prompt, system, provider)` for LLM calls — returns None gracefully if key missing
5. Use `self.save_report(data, filename)` to save output
6. Use `self.create_github_issue(title, body)` for alerts (auto-dedupes by title prefix)

## Design Principles

- **Non-fatal**: Agent failures are wrapped in try/except, never block the scrape pipeline
- **Graceful degradation**: Missing API keys skip LLM steps, return partial results
- **GitHub issue dedup**: Checks for existing open issues before creating new ones
- **Consistent reporting**: Every `execute()` returns a dict with `status` key
