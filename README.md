# Cambridge-Somerville Event Scraper

A specialized web scraping system that automatically collects event data from Cambridge and Somerville municipal websites, community organizations, and local venues to populate a centralized events database.

## Features

✓ **Multi-Source Scraping** - Collect events from multiple Cambridge-Somerville sources
✓ **Custom Scrapers** - High-priority sources get custom scrapers for accuracy
✓ **Data Validation** - Automatic quality checking and cleaning
✓ **Duplicate Detection** - Intelligent deduplication across sources
✓ **REST API** - FastAPI endpoints for easy data access
✓ **Structured Data** - Standardized event schema with rich metadata

## Quick Start

### Installation

```bash
# Install dependencies
pip install -r requirements.txt
```

### Run Scraper

```bash
# Execute full scrape
python scrape.py
```

This will:
1. Scrape events from all configured sources
2. Validate and clean the data
3. Remove duplicates
4. Save results to `data/events.json`
5. Log activity to `logs/scraper.log`

### Start API Server

```bash
# Run FastAPI server
python -m src.api.main
```

The API will be available at `http://localhost:8000`

API Documentation: `http://localhost:8000/docs`

## API Endpoints

### Get Events
```bash
GET /events
```

Query parameters:
- `category` - Filter by event category
- `city` - Filter by city
- `start_date` - Filter events after this date
- `end_date` - Filter events before this date
- `limit` - Max results (default: 100)
- `offset` - Skip N events (for pagination)

Example:
```bash
curl "http://localhost:8000/events?category=music&city=Cambridge&limit=10"
```

### Search Events
```bash
GET /events/search?q=concert
```

### Get Event by ID
```bash
GET /events/{event_id}
```

### Get Statistics
```bash
GET /stats
```

### Get Categories
```bash
GET /categories
```

### Get Sources
```bash
GET /sources
```

## Project Structure

```
cambridgescraper/
├── src/
│   ├── models/          # Data models (Event, ScraperConfig)
│   │   └── event.py
│   ├── scrapers/        # Scraping logic
│   │   ├── base_scraper.py       # Base classes
│   │   └── cambridge_gov.py      # Custom scrapers
│   ├── api/             # REST API
│   │   └── main.py
│   └── utils/           # Utilities
│       ├── validator.py          # Data validation
│       └── deduplicator.py       # Duplicate detection
├── data/                # Scraped data
├── logs/                # Log files
├── scrape.py           # Main scraper orchestrator
└── requirements.txt     # Dependencies
```

## Data Schema

Events follow this standardized schema:

```python
{
  "id": "uuid",
  "title": "Event Name",
  "description": "Event description...",
  "start_datetime": "2024-03-15T19:00:00",
  "end_datetime": "2024-03-15T21:00:00",
  "venue_name": "Cambridge Public Library",
  "street_address": "449 Broadway",
  "city": "Cambridge",
  "state": "MA",
  "zip_code": "02138",
  "category": "community",
  "tags": ["family-friendly"],
  "cost": "Free",
  "source_url": "https://...",
  "source_name": "City of Cambridge"
}
```

## Event Categories

- `music` - Concerts, performances, live music
- `arts and culture` - Art exhibits, galleries, cultural events
- `food and drink` - Food festivals, dining events
- `theater` - Plays, performances, drama
- `lectures` - Talks, presentations, seminars
- `sports` - Sports events, fitness activities
- `community` - Community meetings, public events
- `other` - Uncategorized events

## Adding Custom Scrapers

To add a custom scraper for a high-priority source:

1. Create a new file in `src/scrapers/`
2. Extend `BaseScraper` class
3. Implement `scrape_events()` method
4. Register in `scrape.py`

Example:

```python
from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate

class MyCustomScraper(BaseScraper):
    def __init__(self):
        super().__init__(
            source_name="My Source",
            source_url="https://example.com/events",
            use_selenium=False  # Set to True if JS rendering needed
        )

    def scrape_events(self) -> List[EventCreate]:
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        # Your custom extraction logic here
        events = []
        # ... extract events ...

        return events
```

## Configuration

### Logging

Logs are written to `logs/scraper.log` with configurable levels in `scrape.py`.

### Data Storage

Events are saved to `data/events.json` after each scrape run.

## Development

### Run Tests

```bash
pytest tests/
```

### Code Quality

```bash
# Format code
black src/

# Lint
flake8 src/
```

## Requirements

See PRD document (`scraperPRD.md`) for full business and technical requirements.

### Key Requirements

- **Data Coverage**: Successfully scrape 95% of publicly available events
- **Data Freshness**: Deliver new events within 4 hours of publication
- **Content Quality**: Maintain 98% accuracy in extracted event details
- **Source Reliability**: Achieve 99% uptime across critical scrapers

## License

Proprietary - Cambridge-Somerville Event Aggregation Project

## Support

For issues or questions, please contact the development team.
