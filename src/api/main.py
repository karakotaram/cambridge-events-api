"""FastAPI application for event data access"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import List, Optional
from datetime import datetime, timedelta
import json
import os

from src.models.event import Event, EventCategory

app = FastAPI(
    title="Cambridge-Somerville Event Scraper API",
    description="REST API for accessing scraped event data",
    version="1.0.0"
)

# Enable CORS for Lovable app
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for now
    allow_credentials=False,  # Must be False when using allow_origins=["*"]
    allow_methods=["*"],
    allow_headers=["*"],
)

# Data storage path - use absolute path for Railway deployment
import pathlib
BASE_DIR = pathlib.Path(__file__).parent.parent.parent
DATA_DIR = BASE_DIR / "data"
EVENTS_FILE = DATA_DIR / "events.json"


def load_events() -> List[Event]:
    """Load events from JSON file"""
    if not EVENTS_FILE.exists():
        print(f"Warning: Events file not found at {EVENTS_FILE}")
        return []

    try:
        with open(EVENTS_FILE, 'r') as f:
            data = json.load(f)
            return [Event(**event) for event in data]
    except Exception as e:
        print(f"Error loading events: {e}")
        return []


@app.get("/")
async def root():
    """API root endpoint"""
    return {
        "message": "Cambridge-Somerville Event Scraper API",
        "version": "1.0.0",
        "endpoints": {
            "/events": "Get all events",
            "/events/{event_id}": "Get specific event",
            "/events/search": "Search events",
            "/health": "Health check"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    events = load_events()
    return {
        "status": "healthy",
        "total_events": len(events),
        "last_updated": datetime.utcnow().isoformat()
    }


@app.get("/events", response_model=List[Event])
async def get_events(
    category: Optional[EventCategory] = None,
    city: Optional[str] = None,
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0)
):
    """
    Get events with optional filtering

    Parameters:
    - category: Filter by event category
    - city: Filter by city
    - start_date: Filter events starting after this date
    - end_date: Filter events starting before this date
    - limit: Maximum number of events to return
    - offset: Number of events to skip
    """
    events = load_events()

    # Apply filters
    if category:
        events = [e for e in events if e.category == category]

    if city:
        events = [e for e in events if e.city and e.city.lower() == city.lower()]

    if start_date:
        events = [e for e in events if e.start_datetime >= start_date]

    if end_date:
        events = [e for e in events if e.start_datetime <= end_date]

    # Sort by start date
    events.sort(key=lambda x: x.start_datetime)

    # Apply pagination
    total = len(events)
    events = events[offset:offset + limit]

    return events


@app.get("/events/{event_id}", response_model=Event)
async def get_event(event_id: str):
    """Get a specific event by ID"""
    events = load_events()

    for event in events:
        if event.id == event_id:
            return event

    raise HTTPException(status_code=404, detail=f"Event {event_id} not found")


@app.get("/events/search", response_model=List[Event])
async def search_events(
    q: str = Query(..., min_length=2, description="Search query"),
    limit: int = Query(50, ge=1, le=500)
):
    """
    Search events by keyword in title or description

    Parameters:
    - q: Search query string
    - limit: Maximum number of results
    """
    events = load_events()
    query = q.lower()

    # Search in title and description
    results = []
    for event in events:
        if (query in event.title.lower() or
            query in event.description.lower()):
            results.append(event)

    # Sort by relevance (title matches first)
    results.sort(key=lambda x: 0 if query in x.title.lower() else 1)

    return results[:limit]


@app.get("/categories")
async def get_categories():
    """Get list of all event categories"""
    return {
        "categories": [cat.value for cat in EventCategory]
    }


@app.get("/sources")
async def get_sources():
    """Get list of all event sources with counts"""
    events = load_events()

    sources = {}
    for event in events:
        source = event.source_name
        if source in sources:
            sources[source] += 1
        else:
            sources[source] = 1

    return {"sources": sources}


@app.get("/stats")
async def get_stats():
    """Get statistics about scraped events"""
    events = load_events()

    if not events:
        return {"message": "No events found"}

    # Calculate stats
    categories = {}
    sources = {}
    cities = {}

    for event in events:
        # Count by category
        if event.category:
            cat = event.category.value
            categories[cat] = categories.get(cat, 0) + 1

        # Count by source
        source = event.source_name
        sources[source] = sources.get(source, 0) + 1

        # Count by city
        if event.city:
            cities[event.city] = cities.get(event.city, 0) + 1

    # Find date range
    dates = [e.start_datetime for e in events]
    earliest = min(dates)
    latest = max(dates)

    return {
        "total_events": len(events),
        "categories": categories,
        "sources": sources,
        "cities": cities,
        "date_range": {
            "earliest": earliest.isoformat(),
            "latest": latest.isoformat()
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
