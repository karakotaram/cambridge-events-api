"""FastAPI application for event data access"""
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from typing import List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel
import json
import os
import pytz
from groq import Groq

from src.models.event import Event, EventCategory, EASTERN_TZ


class ChatRequest(BaseModel):
    message: str
    conversation_history: Optional[List[dict]] = None


class ChatResponse(BaseModel):
    response: str
    events: Optional[List[Event]] = None

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


@app.get("/version")
async def version_check():
    """Version check endpoint to verify deployment"""
    return {
        "version": "1.8.0",
        "context_events": 500,
        "message": "500 events, 30 days, stricter age guidance"
    }


@app.get("/events", response_model=List[Event])
async def get_events(
    category: Optional[EventCategory] = None,
    city: Optional[str] = None,
    source: Optional[str] = Query(None, description="Filter by event source name"),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    upcoming_only: bool = Query(False, description="Show only upcoming events"),
    family_friendly: Optional[bool] = Query(None, description="Filter for family-friendly events"),
    sort_order: str = Query("asc", regex="^(asc|desc)$", description="Sort order: asc or desc"),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0)
):
    """
    Get events with optional filtering

    Parameters:
    - category: Filter by event category
    - city: Filter by city
    - source: Filter by event source name
    - start_date: Filter events starting after this date
    - end_date: Filter events starting before this date
    - upcoming_only: If true, only show events from today forward
    - family_friendly: If true, only show family-friendly events
    - sort_order: Sort by date (asc = oldest first, desc = newest first)
    - limit: Maximum number of events to return
    - offset: Number of events to skip
    """
    events = load_events()

    # Filter upcoming events if requested (using Eastern Time since all events are in Cambridge/Somerville)
    if upcoming_only:
        now = datetime.now(EASTERN_TZ)
        # Normalize timezone comparison to handle both aware and naive datetimes
        filtered_events = []
        for e in events:
            event_dt = e.start_datetime
            # Ensure both datetimes have matching timezone awareness
            if event_dt.tzinfo is None and now.tzinfo is not None:
                # Event is naive, make comparison naive
                now_compare = now.replace(tzinfo=None)
            elif event_dt.tzinfo is not None and now.tzinfo is None:
                # Event is aware, make comparison aware
                now_compare = EASTERN_TZ.localize(now)
            else:
                # Both have same timezone awareness
                now_compare = now if now.tzinfo is not None else now.replace(tzinfo=None)

            if event_dt >= now_compare:
                filtered_events.append(e)
        events = filtered_events

    # Apply filters
    if category:
        events = [e for e in events if e.category == category]

    if city:
        events = [e for e in events if e.city and e.city.lower() == city.lower()]

    if source:
        events = [e for e in events if e.source_name and e.source_name.lower() == source.lower()]

    if start_date:
        events = [e for e in events if e.start_datetime >= start_date]

    if end_date:
        events = [e for e in events if e.start_datetime <= end_date]

    if family_friendly is not None:
        events = [e for e in events if getattr(e, 'family_friendly', False) == family_friendly]

    # Sort by start date (normalize timezone-aware vs naive datetimes for comparison)
    def get_sort_key(event):
        dt = event.start_datetime
        # Convert to naive datetime for consistent sorting
        if dt.tzinfo is not None:
            return dt.replace(tzinfo=None)
        return dt

    events.sort(key=get_sort_key, reverse=(sort_order == "desc"))

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


def format_events_for_context(events: List[Event], limit: int = 500) -> str:
    """Format events into a compressed context string for the LLM"""
    # Sort by date and take upcoming events
    now = datetime.now(EASTERN_TZ)
    upcoming = []
    for e in events:
        event_dt = e.start_datetime
        if event_dt.tzinfo is None:
            event_dt = EASTERN_TZ.localize(event_dt)
        if event_dt >= now:
            upcoming.append(e)

    # Normalize timezone for sorting
    def get_sort_dt(event):
        dt = event.start_datetime
        if dt.tzinfo is None:
            return EASTERN_TZ.localize(dt)
        return dt

    upcoming.sort(key=get_sort_dt)

    # Spread events across days AND times of day to ensure coverage
    from collections import defaultdict
    events_by_date = defaultdict(list)
    for e in upcoming:
        date_key = get_sort_dt(e).date()
        events_by_date[date_key].append(e)

    selected = []
    for date_key in sorted(events_by_date.keys())[:30]:  # Next month
        day_events = events_by_date[date_key]
        # Bucket by time of day: morning (<12), afternoon (12-17), evening (>=17)
        morning = [e for e in day_events if get_sort_dt(e).hour < 12]
        afternoon = [e for e in day_events if 12 <= get_sort_dt(e).hour < 17]
        evening = [e for e in day_events if get_sort_dt(e).hour >= 17]

        # Prioritize family-friendly events in each bucket
        def prioritize_family(events):
            family = [e for e in events if getattr(e, 'family_friendly', False)]
            other = [e for e in events if not getattr(e, 'family_friendly', False)]
            return family + other

        # Take up to 7 from each time bucket, family-friendly first
        day_sample = prioritize_family(morning)[:7] + prioritize_family(afternoon)[:7] + prioritize_family(evening)[:7]
        selected.extend(day_sample)
        if len(selected) >= limit:
            break

    selected = selected[:limit]

    # Compressed format: title | Fri 12/5 7PM | venue | cat | [F] | url
    lines = []
    for e in selected:
        dt = e.start_datetime
        # Compact date: "Fri 12/5 7PM"
        date_str = dt.strftime("%a %m/%d %I%p").replace(" 0", " ").replace("AM", "am").replace("PM", "pm")

        # Family-friendly flag
        family_flag = " [F]" if getattr(e, 'family_friendly', False) else ""

        # Short category
        cat = e.category
        if cat is None:
            cat_str = ""
        elif hasattr(cat, 'value'):
            cat_str = cat.value
        else:
            cat_str = str(cat)

        title_short = e.title[:50] if len(e.title) > 50 else e.title
        venue_short = e.venue_name[:25] if len(e.venue_name) > 25 else e.venue_name
        lines.append(f"- {title_short} | {date_str} | {venue_short} | {cat_str}{family_flag} | {e.source_url}")

    return "\n".join(lines)


def get_chat_system_prompt(events_context: str) -> str:
    """Build the system prompt with event data"""
    today = datetime.now(EASTERN_TZ)
    today_str = today.strftime("%A, %B %d, %Y")

    return f"""You are a friendly local guide for Cambridge and Somerville, MA.

TODAY: {today_str}

RULES:
- Recommend only 2-3 best matches
- "date night", "evening" = after 5PM
- If no good matches exist, say so honestly. Don't recommend inappropriate events.

AGE GUIDANCE (STRICTLY FOLLOW):
- Toddlers (1-3): ONLY recommend events with "story time", "lapsit", "sing-along", "songs & stories", "baby" in title. If none exist for that day, say "I don't see toddler-specific events on that day. Weekday mornings typically have more story times."
- Young kids (4-7): family shows, kid concerts, art activities
- Older kids (8+): theater, workshops, museums

NEVER recommend yoga, theater, jazz, concerts, book groups, or art receptions for toddlers.

EVENTS (title | date | venue | cat | [F] | url):
{events_context}

IMPORTANT - USE THIS EXACT FORMAT FOR EACH EVENT:
[Event Title](url) - Time at Venue

Example: [Baby Lapsit](https://cambridgema.gov/event123) - 11am at Collins Branch"""


@app.post("/chat", response_model=ChatResponse)
async def chat_with_events(request: ChatRequest):
    """
    Chat with an AI assistant about local events

    The assistant has knowledge of all upcoming events and can help
    users find events based on natural language queries like:
    - "What's happening this weekend?"
    - "I'm looking for live music next Saturday"
    - "Find something fun for kids this Sunday"
    """
    # Check for API key
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="Chat service not configured. Missing GROQ_API_KEY."
        )

    # Load events and build context
    events = load_events()
    events_context = format_events_for_context(events)
    system_prompt = get_chat_system_prompt(events_context)

    # Build messages for Groq (OpenAI-compatible format)
    messages = [{"role": "system", "content": system_prompt}]
    if request.conversation_history:
        messages.extend(request.conversation_history)
    messages.append({"role": "user", "content": request.message})

    # Call Groq
    try:
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=1024
        )

        return ChatResponse(
            response=response.choices[0].message.content,
            events=None
        )

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"AI service error: {str(e)} - {error_details}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
