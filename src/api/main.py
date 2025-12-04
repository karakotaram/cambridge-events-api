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
import google.generativeai as genai

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
        "version": "1.1.0",
        "commit": "ff2ca5a",
        "default_limit": 1000,
        "max_limit": 5000,
        "message": "API limit increased to 1000 events by default"
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


def format_events_for_context(events: List[Event], limit: int = 200) -> str:
    """Format events into a concise context string for the LLM"""
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
    upcoming = upcoming[:limit]

    lines = []
    for e in upcoming:
        date_str = e.start_datetime.strftime("%A, %B %d, %Y at %I:%M %p")
        cost_str = f" (${e.cost})" if e.cost else " (free/unspecified)"
        family_str = " [Family-friendly]" if getattr(e, 'family_friendly', False) else ""
        # Handle category as either enum or string
        cat = e.category
        if cat is None:
            cat_str = "general"
        elif hasattr(cat, 'value'):
            cat_str = cat.value
        else:
            cat_str = str(cat)
        lines.append(
            f"- **{e.title}**{family_str}\n"
            f"  Date: {date_str}\n"
            f"  Venue: {e.venue_name}, {e.city}\n"
            f"  Category: {cat_str}{cost_str}\n"
            f"  Description: {e.description[:200]}...\n"
            f"  Link: {e.source_url}\n"
            f"  ID: {e.id}"
        )

    return "\n\n".join(lines)


def get_chat_system_prompt(events_context: str) -> str:
    """Build the system prompt with event data"""
    today = datetime.now(EASTERN_TZ)
    today_str = today.strftime("%A, %B %d, %Y")

    return f"""You are a friendly, enthusiastic local guide for Cambridge and Somerville, Massachusetts! You help people discover fun events happening in the area.

TODAY'S DATE: {today_str}

You have access to a database of upcoming local events. When users ask about events, recommend relevant ones from the list below. Be warm, conversational, and helpful!

GUIDELINES:
- Parse natural language dates: "this weekend" = upcoming Saturday/Sunday, "next Sunday" = the Sunday after this one, "tonight" = today's evening
- Match user interests to event categories and descriptions
- For family/kid requests, look for family-friendly events or appropriate categories
- Always include the event title, date/time, venue, and a brief description
- Include the source URL so they can get more details
- If no events match, be helpful and suggest checking back or broadening their search
- Keep responses concise but warm - 2-4 event recommendations is usually ideal
- Use a friendly, upbeat tone! You love Cambridge/Somerville and want to share great experiences

AVAILABLE EVENTS:

{events_context}

Remember: Be helpful, be specific, and help people find something wonderful to do!"""


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
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail="Chat service not configured. Missing GOOGLE_API_KEY."
        )

    # Load events and build context
    events = load_events()
    events_context = format_events_for_context(events)
    system_prompt = get_chat_system_prompt(events_context)

    # Build conversation history for Gemini
    history = []
    if request.conversation_history:
        for msg in request.conversation_history:
            role = "user" if msg["role"] == "user" else "model"
            history.append({"role": role, "parts": [msg["content"]]})

    # Call Gemini
    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel("gemini-pro")

        # For gemini-pro, prepend system prompt to first message if no history
        if not history:
            full_prompt = f"{system_prompt}\n\nUser question: {request.message}"
            response = model.generate_content(full_prompt)
        else:
            chat = model.start_chat(history=history)
            response = chat.send_message(request.message)

        return ChatResponse(
            response=response.text,
            events=None
        )

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"AI service error: {str(e)} - {error_details}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
