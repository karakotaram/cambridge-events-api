"""FastAPI application for event data access"""
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from typing import List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel
import hashlib
import json
import os
import pytz
import time
from groq import Groq

from src.models.event import Event, EventCategory, EASTERN_TZ
from src.api.onboarding import router as onboarding_router
from src.models.interactions import WebsiteInteraction
from src.services.scoring import calculate_event_score
from src.db.database import SessionLocal

# PostHog analytics (no-op if POSTHOG_API_KEY not set)
_posthog = None
_POSTHOG_API_KEY = os.environ.get("POSTHOG_API_KEY", "")
if _POSTHOG_API_KEY:
    try:
        import posthog
        posthog.api_key = _POSTHOG_API_KEY
        posthog.host = os.environ.get("POSTHOG_HOST", "https://us.i.posthog.com")
        posthog.debug = os.environ.get("POSTHOG_DEBUG", "").lower() in ("1", "true")
        posthog.disabled = False
        _posthog = posthog
        print(f"[POSTHOG] Initialized (host={posthog.host}, key={_POSTHOG_API_KEY[:8]}...)")
    except ImportError:
        print("[POSTHOG] posthog package not installed, skipping")
else:
    print("[POSTHOG] No POSTHOG_API_KEY set, analytics disabled")


def _posthog_capture(request: Request, event_name: str, properties: dict = None):
    """Fire a PostHog event using SHA256-hashed IP as distinct_id, then flush."""
    if _posthog is None:
        return
    try:
        client_ip = request.client.host if request.client else "unknown"
        distinct_id = hashlib.sha256(client_ip.encode()).hexdigest()[:16]
        _posthog.capture(distinct_id, event_name, properties or {})
        _posthog.flush()
    except Exception as e:
        print(f"[POSTHOG] Capture error: {e}")


# In-memory cache for events
_events_cache = {
    "events": None,
    "loaded_at": 0,
    "ttl": 300  # 5 minutes cache
}


class ChatRequest(BaseModel):
    message: str
    conversation_history: Optional[List[dict]] = None


class ChatResponse(BaseModel):
    response: str
    events: Optional[List[Event]] = None


class EventSlim(BaseModel):
    """Lightweight event model for list/map views"""
    id: str
    title: str
    start_datetime: datetime
    end_datetime: Optional[datetime] = None
    venue_name: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    category: Optional[EventCategory] = None
    family_friendly: bool = False
    image_url: Optional[str] = None
    source_url: str
    source_name: Optional[str] = None
    cost: Optional[str] = None
    score: Optional[float] = None
    featured: bool = False


class TrackRequest(BaseModel):
    """Request model for tracking interactions"""
    event_id: str
    interaction_type: str
    position: Optional[int] = None
    score: Optional[float] = None
    event_title: Optional[str] = None
    source_name: Optional[str] = None


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

# Enable GZip compression for responses > 1KB
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Data storage path - use absolute path for Railway deployment
import pathlib
BASE_DIR = pathlib.Path(__file__).parent.parent.parent
DATA_DIR = BASE_DIR / "data"
EVENTS_FILE = DATA_DIR / "events.json"
FEATURED_FILE = DATA_DIR / "featured.json"
STATIC_DIR = BASE_DIR / "static"

# Register onboarding router
app.include_router(onboarding_router)

# Mount static files (mockups, onboarding, admin pages)
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")


def load_featured() -> list:
    """Load featured events list (title+source_name pairs)"""
    if not FEATURED_FILE.exists():
        return []
    try:
        with open(FEATURED_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return []


def save_featured(featured: list):
    """Save featured events list"""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(FEATURED_FILE, 'w') as f:
        json.dump(featured, f, indent=2)


def _is_featured(event, featured_list: list) -> bool:
    """Check if an event matches any featured entry by title+source_name"""
    for entry in featured_list:
        if event.title == entry.get("title") and event.source_name == entry.get("source_name"):
            return True
    return False


def load_events(use_cache: bool = True) -> List[Event]:
    """Load events from JSON file with optional caching"""
    global _events_cache

    # Check cache first
    if use_cache and _events_cache["events"] is not None:
        if time.time() - _events_cache["loaded_at"] < _events_cache["ttl"]:
            return _events_cache["events"]

    if not EVENTS_FILE.exists():
        print(f"Warning: Events file not found at {EVENTS_FILE}")
        return []

    try:
        with open(EVENTS_FILE, 'r') as f:
            data = json.load(f)
            featured_list = load_featured()
            events = []
            for event_data in data:
                # Check featured before construction (mutation may not work in Pydantic v2)
                title = event_data.get("title", "")
                source = event_data.get("source_name", "")
                is_feat = any(
                    e.get("title") == title and e.get("source_name") == source
                    for e in featured_list
                )
                if is_feat:
                    event_data = {**event_data, "featured": True}
                event = Event(**event_data)
                events.append(event)

            # Update cache
            _events_cache["events"] = events
            _events_cache["loaded_at"] = time.time()

            return events
    except Exception as e:
        print(f"Error loading events: {e}")
        return []


@app.get("/")
async def root():
    """API root endpoint"""
    return {
        "message": "Cambridge-Somerville Event Scraper API",
        "version": "1.11.0",
        "endpoints": {
            "/events": "Get all events (full data)",
            "/events/slim": "Get events with minimal fields (faster, for list/map views)",
            "/events/{event_id}": "Get specific event",
            "/events/{event_id}/calendar.ics": "Download ICS calendar file for event",
            "/events/search": "Search events",
            "/signup": "Sign up for personalized weekly event emails",
            "/onboarding/sample-events": "Get diverse events for onboarding picker",
            "/onboarding/submit": "Submit liked events and subscribe",
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


@app.post("/track")
async def track_interaction(body: TrackRequest, http_request: Request):
    """
    Track a user interaction with an event (fire-and-forget).

    Interaction types:
    - card_expand: User expanded event card to see details
    - click_external: User clicked through to source URL
    - calendar_add: User added event to calendar

    This endpoint is designed to be non-blocking. Errors are logged
    but don't fail the request.
    """
    try:
        if SessionLocal is None:
            # Database not configured, silently skip
            return {"status": "ok"}

        # Validate interaction type
        valid_types = {'card_expand', 'click_external', 'calendar_add'}
        if body.interaction_type not in valid_types:
            return {"status": "ok"}  # Silently ignore invalid types

        db = SessionLocal()
        try:
            interaction = WebsiteInteraction(
                event_id=body.event_id,
                interaction_type=body.interaction_type,
                position=body.position,
                score=body.score,
                event_title=body.event_title[:256] if body.event_title else None,
                source_name=body.source_name[:128] if body.source_name else None,
            )
            db.add(interaction)
            db.commit()
        finally:
            db.close()

        _posthog_capture(http_request, "event_interaction", {
            "event_id": body.event_id,
            "type": body.interaction_type,
            "position": body.position,
            "score": body.score,
        })
    except Exception as e:
        # Don't fail the request, just log
        print(f"Track error: {e}")

    return {"status": "ok"}


@app.get("/version")
async def version_check():
    """Version check endpoint to verify deployment"""
    return {
        "version": "1.12.0",
        "posthog_enabled": _posthog is not None,
        "posthog_host": _posthog.host if _posthog else None,
    }


@app.get("/health/scrapers")
async def scraper_health():
    """Run CI monitor agent and return source freshness report"""
    try:
        from src.agents.ci_monitor import CIMonitorAgent
        agent = CIMonitorAgent()
        return agent.run()
    except Exception as e:
        return {"status": "error", "error": str(e)}


@app.get("/events", response_model=List[Event])
async def get_events(
    category: Optional[EventCategory] = None,
    city: Optional[str] = None,
    source: Optional[str] = Query(None, description="Filter by event source name"),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    upcoming_only: bool = Query(False, description="Show only upcoming events"),
    family_friendly: Optional[bool] = Query(None, description="Filter for family-friendly events"),
    ranked: bool = Query(False, description="Sort by relevance score instead of date"),
    sort_order: str = Query("asc", regex="^(asc|desc)$", description="Sort order: asc or desc (ignored when ranked=true)"),
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
    - ranked: If true, sort by relevance score (considers popularity, recency, temporal urgency)
    - sort_order: Sort by date (asc = oldest first, desc = newest first), ignored when ranked=true
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

    # Sort
    if ranked:
        event_scores = score_events(events, use_interactions=True)
        events.sort(key=lambda e: event_scores.get(e.id, 0), reverse=True)
    else:
        def get_sort_key(event):
            dt = event.start_datetime
            if dt.tzinfo is not None:
                return dt.replace(tzinfo=None)
            return dt
        events.sort(key=get_sort_key, reverse=(sort_order == "desc"))

    # Apply pagination
    total = len(events)
    events = events[offset:offset + limit]

    return events


def get_interaction_counts_from_db() -> dict:
    """
    Fetch interaction counts for all events from the last 30 days.

    Returns:
        Dict mapping event_id -> {interaction_type: count}
    """
    if SessionLocal is None:
        return {}

    try:
        from sqlalchemy import func, text, case
        db = SessionLocal()
        try:
            # Position bias correction: interactions from lower positions
            # (further down the list) are weighted higher since the user
            # scrolled past many other events — a stronger interest signal.
            position_weight = case(
                (WebsiteInteraction.position.is_(None), 1.0),
                (WebsiteInteraction.position <= 5, 1.0),
                (WebsiteInteraction.position <= 10, 1.5),
                (WebsiteInteraction.position <= 20, 2.0),
                else_=3.0
            )

            # Query position-weighted counts grouped by event_id and interaction_type
            thirty_days_ago = datetime.utcnow() - timedelta(days=30)
            results = db.query(
                WebsiteInteraction.event_id,
                WebsiteInteraction.interaction_type,
                func.sum(position_weight).label('count')
            ).filter(
                WebsiteInteraction.created_at >= thirty_days_ago
            ).group_by(
                WebsiteInteraction.event_id,
                WebsiteInteraction.interaction_type
            ).all()

            # Build nested dict: event_id -> {type: weighted_count}
            counts = {}
            for event_id, interaction_type, count in results:
                if event_id not in counts:
                    counts[event_id] = {}
                counts[event_id][interaction_type] = float(count)

            return counts
        finally:
            db.close()
    except Exception as e:
        print(f"Error fetching interaction counts: {e}")
        return {}


def score_events(events: list, use_interactions: bool = True) -> dict:
    """
    Calculate scores for a list of events.

    Args:
        events: List of Event objects to score
        use_interactions: If True, fetch interaction counts from DB.
                         If False, use empty counts (for chat context, avoids DB latency).

    Returns:
        Dict mapping event_id -> score
    """
    interaction_counts = get_interaction_counts_from_db() if use_interactions else {}
    now = datetime.utcnow()

    scores = {}
    for e in events:
        cat_str = None
        if e.category:
            cat_str = e.category.value if hasattr(e.category, 'value') else str(e.category)

        scores[e.id] = calculate_event_score(
            source_name=e.source_name,
            category=cat_str,
            cost=e.cost,
            start_datetime=e.start_datetime,
            scraped_at=e.scraped_at,
            interaction_counts=interaction_counts.get(e.id, {}),
            now=now
        )

    return scores


@app.get("/events/slim", response_model=List[EventSlim])
async def get_events_slim(
    http_request: Request,
    category: Optional[EventCategory] = None,
    city: Optional[str] = None,
    source: Optional[str] = Query(None, description="Filter by event source name"),
    free_only: Optional[bool] = Query(None, description="Filter for free events only"),
    upcoming_only: bool = Query(True, description="Show only upcoming events (default: true)"),
    family_friendly: Optional[bool] = Query(None, description="Filter for family-friendly events"),
    ranked: bool = Query(True, description="Sort by relevance score instead of date (default: true)"),
    user_id: Optional[str] = Query(None, description="User UUID for personalized ranking"),
    limit: int = Query(1000, ge=1, le=5000),
    offset: int = Query(0, ge=0)
):
    """
    Get lightweight event data optimized for list/map views.

    Returns only essential fields (no descriptions) for faster loading.
    Use /events/{event_id} to get full details for a specific event.

    Parameters:
    - category: Filter by event category
    - city: Filter by city
    - source: Filter by event source name
    - free_only: If true, only show free events
    - upcoming_only: If true (default), only show events from today forward
    - family_friendly: If true, only show family-friendly events
    - ranked: If true, sort by relevance score (considers popularity, recency, temporal urgency)
    - limit: Maximum number of events to return (default: 500)
    - offset: Number of events to skip
    """
    events = load_events()

    # Filter upcoming events (default behavior for slim endpoint)
    if upcoming_only:
        now = datetime.now(EASTERN_TZ)
        filtered_events = []
        for e in events:
            event_dt = e.start_datetime
            if event_dt.tzinfo is None and now.tzinfo is not None:
                now_compare = now.replace(tzinfo=None)
            elif event_dt.tzinfo is not None and now.tzinfo is None:
                now_compare = EASTERN_TZ.localize(now)
            else:
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

    if free_only:
        events = [e for e in events if e.cost and e.cost.lower() in ['free', '$free', '0', '$0', 'free admission']]

    if family_friendly is not None:
        events = [e for e in events if getattr(e, 'family_friendly', False) == family_friendly]

    # Calculate scores and sort
    event_scores = {}
    if ranked:
        event_scores = score_events(events, use_interactions=True)

        # Refresh cached recommender if stale (>1 hour old)
        if hasattr(app.state, "recommender_trained_at"):
            trained_at = app.state.recommender_trained_at
            if trained_at is None or (time.time() - trained_at) > 3600:
                try:
                    refreshed = _train_recommender_from_db()
                    if refreshed:
                        app.state.recommender = refreshed
                        app.state.recommender_trained_at = time.time()
                        print("[RANKING] LightFM model refreshed")
                except Exception as e:
                    print(f"[RANKING] LightFM refresh failed: {e}")

        # Blend LightFM scores into ranking (works for all users)
        if hasattr(app.state, "recommender") and app.state.recommender is not None:
            try:
                event_ids = [e.id for e in events]

                # Per-user scores if user_id provided, otherwise global community scores
                if user_id:
                    lfm_scores = app.state.recommender.predict_scores(user_id, event_ids)
                    if not lfm_scores:
                        # Unknown user, fall back to global
                        lfm_scores = app.state.recommender.get_global_scores(event_ids)
                else:
                    lfm_scores = app.state.recommender.get_global_scores(event_ids)

                if lfm_scores:
                    # Normalize LightFM scores to [0, 1]
                    vals = list(lfm_scores.values())
                    min_s, max_s = min(vals), max(vals)
                    rng = max_s - min_s if max_s > min_s else 1.0
                    norm_lfm = {eid: (s - min_s) / rng for eid, s in lfm_scores.items()}

                    # Normalize existing scores to [0, 1] for fair blending
                    if event_scores:
                        existing_vals = list(event_scores.values())
                        e_min, e_max = min(existing_vals), max(existing_vals)
                        e_rng = e_max - e_min if e_max > e_min else 1.0
                        norm_existing = {eid: (s - e_min) / e_rng for eid, s in event_scores.items()}
                    else:
                        norm_existing = {}

                    # Blend: 0.8 LightFM + 0.2 existing
                    for eid in event_scores:
                        if eid in norm_lfm:
                            event_scores[eid] = (
                                0.2 * norm_existing.get(eid, 0.0)
                                + 0.8 * norm_lfm[eid]
                            )

                    label = f"user {user_id[:8]}..." if user_id else "global"
                    print(f"[RANKING] LightFM blended ({label}, "
                          f"{len(lfm_scores)} scores, 0.8 LightFM + 0.2 existing)")
            except Exception as e:
                print(f"[RANKING] LightFM blending error: {e}")

        # Apply temporal boost AFTER blending so it's not diluted by LightFM weight
        from src.services.scoring import calculate_temporal_boost
        now = datetime.utcnow()
        for e in events:
            if e.id in event_scores:
                temporal = calculate_temporal_boost(e.start_datetime, now)
                event_scores[e.id] *= temporal

        # Sort by score descending
        events.sort(key=lambda e: event_scores.get(e.id, 0), reverse=True)

        # Log ranking score summary stats
        if event_scores:
            all_scores = sorted(event_scores.values(), reverse=True)
            total_scored = len(all_scores)
            top10_avg = sum(all_scores[:10]) / min(10, total_scored)
            median_score = all_scores[total_scored // 2] if total_scored else 0
            print(f"[RANKING] total_scored={total_scored} top10_avg={top10_avg:.3f} "
                  f"median={median_score:.3f}")
    else:
        # Sort by start date (original behavior)
        def get_sort_key(event):
            dt = event.start_datetime
            if dt.tzinfo is not None:
                return dt.replace(tzinfo=None)
            return dt
        events.sort(key=get_sort_key)

    # Apply pagination
    events = events[offset:offset + limit]

    # Convert to slim format
    featured_list = load_featured()
    slim_events = []
    for e in events:
        slim = EventSlim(
            id=e.id,
            title=e.title,
            start_datetime=e.start_datetime,
            end_datetime=e.end_datetime,
            venue_name=e.venue_name,
            city=e.city,
            latitude=e.latitude,
            longitude=e.longitude,
            category=e.category,
            family_friendly=getattr(e, 'family_friendly', False),
            image_url=e.image_url,
            source_url=e.source_url,
            source_name=e.source_name,
            cost=e.cost,
            featured=_is_featured(e, featured_list)
        )
        # Include score in response if ranked
        if ranked and e.id in event_scores:
            slim.score = round(event_scores[e.id], 3)
        slim_events.append(slim)

    _posthog_capture(http_request, "events_list_viewed", {
        "count": len(slim_events),
        "ranked": ranked,
        "category": category.value if category else None,
        "city": city,
        "source": source,
        "free_only": free_only,
        "family_friendly": family_friendly,
    })

    return slim_events


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

    # Score-weighted relevance: text match bonus + event quality score as tiebreaker
    event_scores = score_events(results, use_interactions=True)

    # Normalize scores to 0-2 range for use as tiebreaker
    max_score = max(event_scores.values()) if event_scores else 1.0
    max_score = max(max_score, 0.001)  # avoid division by zero

    def search_sort_key(event):
        text_bonus = 10 if query in event.title.lower() else 3
        normalized_score = (event_scores.get(event.id, 0) / max_score) * 2
        return text_bonus + normalized_score

    results.sort(key=search_sort_key, reverse=True)

    return results[:limit]


@app.get("/events/{event_id}", response_model=Event)
async def get_event(event_id: str):
    """Get a specific event by ID"""
    events = load_events()

    for event in events:
        if event.id == event_id:
            return event

    raise HTTPException(status_code=404, detail=f"Event {event_id} not found")


def generate_ics(event: Event) -> str:
    """Generate ICS calendar file content for an event"""
    import re

    def format_ics_datetime(dt: datetime) -> str:
        """Format datetime for ICS (YYYYMMDDTHHMMSS)"""
        if dt.tzinfo is not None:
            dt = dt.replace(tzinfo=None)
        return dt.strftime("%Y%m%dT%H%M%S")

    def escape_ics_text(text: str) -> str:
        """Escape special characters for ICS format"""
        if not text:
            return ""
        # Escape backslashes, semicolons, commas, and newlines
        text = text.replace("\\", "\\\\")
        text = text.replace(";", "\\;")
        text = text.replace(",", "\\,")
        text = text.replace("\n", "\\n")
        return text

    # Build location string
    location_parts = []
    if event.venue_name:
        location_parts.append(event.venue_name)
    if event.street_address:
        location_parts.append(event.street_address)
    if event.city:
        location_parts.append(event.city)
    if event.state:
        location_parts.append(event.state)
    location = ", ".join(location_parts)

    # Calculate end time (default to 2 hours after start if not specified)
    start_dt = event.start_datetime
    if event.end_datetime:
        end_dt = event.end_datetime
    else:
        end_dt = start_dt + timedelta(hours=2)

    # Build description with source link
    description = event.description or ""
    if event.source_url:
        description += f"\\n\\nMore info: {event.source_url}"

    # Generate ICS content
    ics_content = f"""BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Cambridge Somerville Events//EN
CALSCALE:GREGORIAN
METHOD:PUBLISH
BEGIN:VEVENT
UID:{event.id}@cambridgesomervilleevents.com
DTSTAMP:{format_ics_datetime(datetime.utcnow())}
DTSTART:{format_ics_datetime(start_dt)}
DTEND:{format_ics_datetime(end_dt)}
SUMMARY:{escape_ics_text(event.title)}
DESCRIPTION:{escape_ics_text(description)}
LOCATION:{escape_ics_text(location)}
URL:{event.source_url or ""}
END:VEVENT
END:VCALENDAR"""

    return ics_content


@app.get("/events/{event_id}/calendar.ics")
async def get_event_ics(event_id: str):
    """
    Download an ICS calendar file for an event.

    Works with Google Calendar, Outlook, Apple Calendar, and any other
    calendar app that supports the ICS format.
    """
    events = load_events()

    for event in events:
        if event.id == event_id:
            ics_content = generate_ics(event)

            # Create a safe filename from the event title
            safe_title = "".join(c if c.isalnum() or c in " -_" else "" for c in event.title)
            safe_title = safe_title[:50].strip().replace(" ", "_")
            filename = f"{safe_title}.ics"

            return Response(
                content=ics_content,
                media_type="text/calendar",
                headers={
                    "Content-Disposition": f'attachment; filename="{filename}"'
                }
            )

    raise HTTPException(status_code=404, detail=f"Event {event_id} not found")


# --- Featured events management ---

@app.get("/featured")
async def get_featured():
    """Get the current featured events list"""
    return load_featured()


@app.post("/events/{event_id}/feature")
async def feature_event(event_id: str):
    """Mark an event as an Editor's Pick by its current ID"""
    global _events_cache
    events = load_events()
    event = next((e for e in events if e.id == event_id), None)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    featured = load_featured()
    # Check if already featured
    for entry in featured:
        if entry["title"] == event.title and entry["source_name"] == event.source_name:
            return {"status": "already_featured", "title": event.title}

    featured.append({"title": event.title, "source_name": event.source_name})
    save_featured(featured)
    # Bust cache so next load picks up the change
    _events_cache["events"] = None
    return {"status": "featured", "title": event.title, "source_name": event.source_name}


@app.delete("/events/{event_id}/feature")
async def unfeature_event(event_id: str):
    """Remove an event from Editor's Picks"""
    global _events_cache
    events = load_events()
    event = next((e for e in events if e.id == event_id), None)
    if not event:
        raise HTTPException(status_code=404, detail=f"Event {event_id} not found")

    featured = load_featured()
    featured = [
        entry for entry in featured
        if not (entry["title"] == event.title and entry["source_name"] == event.source_name)
    ]
    save_featured(featured)
    _events_cache["events"] = None
    return {"status": "unfeatured", "title": event.title}


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


@app.get("/analytics/interactions")
async def get_interaction_analytics(
    api_key: str = Query(..., description="Admin API key for authentication"),
    days: int = Query(7, ge=1, le=90, description="Number of days to analyze")
):
    """
    Get analytics data for user interactions with events.

    Requires ADMIN_API_KEY for authentication.

    Returns:
    - Summary statistics (total interactions, unique events)
    - Breakdown by interaction type
    - Top events by interaction count
    - Daily interaction trends
    - Top sources by engagement
    """
    # Verify API key
    expected_key = os.environ.get("ADMIN_API_KEY", "")
    if not expected_key or api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if SessionLocal is None:
        return {
            "error": "Database not configured",
            "summary": {"total_interactions": 0, "unique_events": 0},
            "by_type": {},
            "top_events": [],
            "daily_trend": [],
            "top_sources": []
        }

    try:
        from sqlalchemy import func, cast, Date

        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=days)

            # Total interactions and unique events
            summary_query = db.query(
                func.count(WebsiteInteraction.id).label('total'),
                func.count(func.distinct(WebsiteInteraction.event_id)).label('unique_events')
            ).filter(WebsiteInteraction.created_at >= cutoff).first()

            total_interactions = summary_query.total or 0
            unique_events = summary_query.unique_events or 0

            # Breakdown by interaction type
            type_breakdown = db.query(
                WebsiteInteraction.interaction_type,
                func.count(WebsiteInteraction.id).label('count')
            ).filter(
                WebsiteInteraction.created_at >= cutoff
            ).group_by(
                WebsiteInteraction.interaction_type
            ).all()

            by_type = {row.interaction_type: row.count for row in type_breakdown}

            # Top events by interaction count (with weighted score)
            from sqlalchemy import case
            weighted_case = case(
                (WebsiteInteraction.interaction_type == 'card_expand', 1),
                (WebsiteInteraction.interaction_type == 'click_external', 3),
                (WebsiteInteraction.interaction_type == 'calendar_add', 5),
                else_=0
            )
            # Use func.max to grab a stored event_title/source_name as fallback
            top_events_query = db.query(
                WebsiteInteraction.event_id,
                func.count(WebsiteInteraction.id).label('total_count'),
                func.sum(weighted_case).label('weighted_score'),
                func.max(WebsiteInteraction.event_title).label('stored_title'),
                func.max(WebsiteInteraction.source_name).label('stored_source'),
            ).filter(
                WebsiteInteraction.created_at >= cutoff
            ).group_by(
                WebsiteInteraction.event_id
            ).order_by(
                func.sum(weighted_case).desc()
            ).limit(20).all()

            # Load events to get titles
            events = load_events()
            event_map = {e.id: e for e in events}

            top_events = []
            for row in top_events_query:
                event = event_map.get(row.event_id)
                top_events.append({
                    "event_id": row.event_id,
                    "title": event.title if event else (row.stored_title or "Unknown Event"),
                    "source_name": event.source_name if event else (row.stored_source or None),
                    "total_interactions": row.total_count,
                    "weighted_score": row.weighted_score or 0
                })

            # Daily interaction trend
            daily_query = db.query(
                cast(WebsiteInteraction.created_at, Date).label('date'),
                func.count(WebsiteInteraction.id).label('count')
            ).filter(
                WebsiteInteraction.created_at >= cutoff
            ).group_by(
                cast(WebsiteInteraction.created_at, Date)
            ).order_by(
                cast(WebsiteInteraction.created_at, Date)
            ).all()

            daily_trend = [
                {"date": row.date.isoformat(), "count": row.count}
                for row in daily_query
            ]

            # Top sources by engagement
            source_engagement = {}
            for row in top_events_query:
                event = event_map.get(row.event_id)
                src_name = event.source_name if event else (row.stored_source or None)
                if src_name:
                    if src_name not in source_engagement:
                        source_engagement[src_name] = {
                            "interactions": 0,
                            "weighted_score": 0,
                            "event_count": 0
                        }
                    source_engagement[src_name]["interactions"] += row.total_count
                    source_engagement[src_name]["weighted_score"] += row.weighted_score or 0
                    source_engagement[src_name]["event_count"] += 1

            top_sources = sorted(
                [
                    {"source": k, **v}
                    for k, v in source_engagement.items()
                ],
                key=lambda x: x["weighted_score"],
                reverse=True
            )[:10]

            # Recent interactions (last 50)
            recent_query = db.query(
                WebsiteInteraction.event_id,
                WebsiteInteraction.interaction_type,
                WebsiteInteraction.created_at,
                WebsiteInteraction.event_title,
                WebsiteInteraction.source_name,
                WebsiteInteraction.position,
                WebsiteInteraction.score,
            ).order_by(
                WebsiteInteraction.created_at.desc()
            ).limit(50).all()

            recent_interactions = []
            for row in recent_query:
                event = event_map.get(row.event_id)
                recent_interactions.append({
                    "event_id": row.event_id,
                    "title": event.title if event else (row.event_title or "Unknown Event"),
                    "interaction_type": row.interaction_type,
                    "timestamp": row.created_at.isoformat(),
                    "position": row.position,
                    "score": row.score,
                })

            # Position-based CTR analysis
            position_analysis = []
            buckets = [(1, 5), (6, 10), (11, 20), (21, None)]
            for low, high in buckets:
                pos_filter = [WebsiteInteraction.position >= low]
                if high is not None:
                    pos_filter.append(WebsiteInteraction.position <= high)

                bucket_query = db.query(
                    WebsiteInteraction.interaction_type,
                    func.count(WebsiteInteraction.id).label('count')
                ).filter(
                    WebsiteInteraction.created_at >= cutoff,
                    WebsiteInteraction.position.isnot(None),
                    *pos_filter
                ).group_by(
                    WebsiteInteraction.interaction_type
                ).all()

                type_counts = {r.interaction_type: r.count for r in bucket_query}
                impressions = type_counts.get('card_expand', 0)
                clicks = type_counts.get('click_external', 0)
                label = f"{low}-{high}" if high else f"{low}+"
                position_analysis.append({
                    "bucket": label,
                    "impressions": impressions,
                    "clicks": clicks,
                    "ctr": round(clicks / impressions, 4) if impressions > 0 else 0,
                    "total": sum(type_counts.values()),
                })

            # Week-over-week trends
            now_utc = datetime.utcnow()
            current_week_start = now_utc - timedelta(days=7)
            previous_week_start = now_utc - timedelta(days=14)

            current_week_q = db.query(
                func.count(WebsiteInteraction.id).label('total'),
                func.count(func.distinct(WebsiteInteraction.event_id)).label('unique_events')
            ).filter(WebsiteInteraction.created_at >= current_week_start).first()

            previous_week_q = db.query(
                func.count(WebsiteInteraction.id).label('total'),
                func.count(func.distinct(WebsiteInteraction.event_id)).label('unique_events')
            ).filter(
                WebsiteInteraction.created_at >= previous_week_start,
                WebsiteInteraction.created_at < current_week_start
            ).first()

            curr_total = current_week_q.total or 0
            prev_total = previous_week_q.total or 0
            curr_unique = current_week_q.unique_events or 0
            prev_unique = previous_week_q.unique_events or 0

            trends = {
                "current_week_interactions": curr_total,
                "previous_week_interactions": prev_total,
                "wow_change_pct": round(((curr_total - prev_total) / prev_total) * 100, 1) if prev_total > 0 else None,
                "current_week_unique_events": curr_unique,
                "previous_week_unique_events": prev_unique,
                "wow_unique_change_pct": round(((curr_unique - prev_unique) / prev_unique) * 100, 1) if prev_unique > 0 else None,
            }

            return {
                "summary": {
                    "total_interactions": total_interactions,
                    "unique_events": unique_events,
                    "period_days": days
                },
                "by_type": by_type,
                "top_events": top_events,
                "daily_trend": daily_trend,
                "top_sources": top_sources,
                "recent_interactions": recent_interactions,
                "position_analysis": position_analysis,
                "trends": trends,
            }

        finally:
            db.close()

    except Exception as e:
        import traceback
        return {
            "error": str(e),
            "details": traceback.format_exc(),
            "summary": {"total_interactions": 0, "unique_events": 0},
            "by_type": {},
            "top_events": [],
            "daily_trend": [],
            "top_sources": [],
            "position_analysis": [],
            "trends": {},
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

    # Score all upcoming events (content + temporal only, skip DB for latency)
    event_scores = score_events(upcoming, use_interactions=False)

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

        # Sort each bucket by score descending (best events first)
        def sort_by_score(bucket):
            return sorted(bucket, key=lambda e: event_scores.get(e.id, 0), reverse=True)

        # Take up to 7 from each time bucket, sorted by score
        day_sample = sort_by_score(morning)[:7] + sort_by_score(afternoon)[:7] + sort_by_score(evening)[:7]
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
        venue_name = e.venue_name or "Unknown Venue"
        venue_short = venue_name[:25] if len(venue_name) > 25 else venue_name
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

CRITICAL FOR TODDLERS: If user mentions toddler/1-3 year old, ONLY suggest events with "story time", "lapsit", "sing-along", "songs", or "baby" in title. If none, say "I don't see toddler story times that day - weekday mornings have more."

OUTPUT FORMAT - ALWAYS USE MARKDOWN LINKS:
[Event Title](https://full-url-here) - Time at Venue

Correct: [Jazz Night](https://passim.org/event/123) - 7pm at Club Passim
Wrong: Jazz Night - 7pm at Club Passim"""


@app.post("/chat")
async def chat_with_events(request: ChatRequest, http_request: Request):
    """
    Chat with an AI assistant about local events

    The assistant has knowledge of all upcoming events and can help
    users find events based on natural language queries like:
    - "What's happening this weekend?"
    - "I'm looking for live music next Saturday"
    - "Find something fun for kids this Sunday"
    """
    try:
        # Check for API key
        api_key = os.environ.get("GROQ_API_KEY")
        if not api_key:
            return JSONResponse(
                status_code=503,
                content={"error": "Chat service not configured. Missing GROQ_API_KEY."}
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
        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messages,
            max_tokens=1024
        )

        _posthog_capture(http_request, "chat_message_sent", {
            "message_length": len(request.message),
        })

        return ChatResponse(
            response=response.choices[0].message.content,
            events=None
        )

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        return JSONResponse(
            status_code=500,
            content={"error": f"AI service error: {str(e)}", "details": error_details}
        )


@app.get("/presentation", include_in_schema=False)
async def get_presentation():
    """Serve the project presentation slide deck"""
    from fastapi.responses import HTMLResponse
    presentation_file = BASE_DIR / "presentation.html"
    if presentation_file.exists():
        with open(presentation_file, 'r') as f:
            return HTMLResponse(content=f.read())
    raise HTTPException(status_code=404, detail="Presentation not found")


@app.get("/signup", include_in_schema=False)
async def get_onboarding_page():
    """Serve the onboarding signup page"""
    from fastapi.responses import HTMLResponse
    onboarding_file = STATIC_DIR / "onboarding" / "index.html"
    if onboarding_file.exists():
        with open(onboarding_file, 'r') as f:
            return HTMLResponse(content=f.read())
    raise HTTPException(status_code=404, detail="Onboarding page not found")


@app.get("/admin", include_in_schema=False)
async def get_admin_page():
    """Serve the archetype audit admin page"""
    from fastapi.responses import HTMLResponse
    admin_file = STATIC_DIR / "admin" / "index.html"
    if admin_file.exists():
        with open(admin_file, 'r') as f:
            return HTMLResponse(content=f.read())
    raise HTTPException(status_code=404, detail="Admin page not found")


@app.get("/admin/featured", include_in_schema=False)
async def get_featured_admin_page():
    """Serve the Editor's Picks admin page"""
    from fastapi.responses import HTMLResponse
    featured_file = STATIC_DIR / "admin" / "featured.html"
    if featured_file.exists():
        with open(featured_file, 'r') as f:
            return HTMLResponse(content=f.read())
    raise HTTPException(status_code=404, detail="Featured admin page not found")


def _run_migrations():
    """Add new columns to existing tables if they don't exist."""
    from src.db.database import engine
    if engine is None:
        return

    from sqlalchemy import inspect, text
    try:
        inspector = inspect(engine)

        # Migrate curated_digests: drop old table (replaced by digest_overrides)
        if "curated_digests" in inspector.get_table_names():
            with engine.begin() as conn:
                conn.execute(text("DROP TABLE curated_digests CASCADE"))
                print("[MIGRATION] Dropped old curated_digests table (replaced by digest_overrides)")

        if "website_interactions" in inspector.get_table_names():
            existing = {c["name"] for c in inspector.get_columns("website_interactions")}
            migrations = {
                "position": "INTEGER",
                "score": "DOUBLE PRECISION",
                "event_title": "VARCHAR(256)",
                "source_name": "VARCHAR(128)",
            }
            with engine.begin() as conn:
                for col_name, col_type in migrations.items():
                    if col_name not in existing:
                        conn.execute(text(
                            f"ALTER TABLE website_interactions ADD COLUMN {col_name} {col_type}"
                        ))
                        print(f"[MIGRATION] Added column website_interactions.{col_name}")

        # Migrate users table: make archetype columns nullable (String instead of Enum)
        if "users" in inspector.get_table_names():
            existing = {c["name"] for c in inspector.get_columns("users")}
            # Add new columns if they don't exist
            # (The ORM will create new tables like user_preferences, onboarding_likes, digest_overrides)

        # Migrate existing archetype users to UserPreferences
        if "users" in inspector.get_table_names() and "user_preferences" in inspector.get_table_names():
            from src.models.user import User, UserPreferences
            from src.services.preferences import get_default_preferences_for_archetype
            from src.db.database import SessionLocal
            if SessionLocal:
                db = SessionLocal()
                try:
                    # Find users with archetypes but no preferences row
                    users_needing_migration = db.query(User).filter(
                        User.primary_archetype.isnot(None),
                    ).all()

                    migrated = 0
                    for user in users_needing_migration:
                        existing_prefs = db.query(UserPreferences).filter(
                            UserPreferences.user_id == user.id
                        ).first()
                        if existing_prefs:
                            continue

                        archetype_val = user.primary_archetype
                        if hasattr(archetype_val, 'value'):
                            archetype_val = archetype_val.value
                        defaults = get_default_preferences_for_archetype(str(archetype_val))
                        prefs = UserPreferences(
                            user_id=user.id,
                            category_weights=defaults["category_weights"],
                            timing_weights=defaults["timing_weights"],
                            venue_weights=defaults["venue_weights"],
                            price_sensitivity=defaults["price_sensitivity"],
                            prefers_family_friendly=defaults["prefers_family_friendly"],
                        )
                        db.add(prefs)
                        migrated += 1

                    if migrated > 0:
                        db.commit()
                        print(f"[MIGRATION] Created UserPreferences for {migrated} existing users from archetypes")
                except Exception as e:
                    db.rollback()
                    print(f"[MIGRATION] User preferences migration error: {e}")
                finally:
                    db.close()

    except Exception as e:
        print(f"[MIGRATION] Error: {e}")


def _train_recommender_from_db():
    """Train LightFM model from DB data. Returns recommender or None."""
    if SessionLocal is None:
        return None
    try:
        from src.jobs.weekly_email import train_lightfm_model, load_events
        db = SessionLocal()
        try:
            events = load_events()
            if not events:
                return None
            return train_lightfm_model(db, events)
        finally:
            db.close()
    except Exception as e:
        print(f"[LightFM] Startup training failed: {e}")
        return None


# Initialize app state for cached recommender
app.state.recommender = None
app.state.recommender_trained_at = None


@app.on_event("startup")
async def startup_migrations():
    from src.db.database import engine, Base
    if engine is not None:
        from src.models.user import UserPreferences, OnboardingLike, DigestOverride  # noqa: F401
        try:
            # Create all tables first (including new ones)
            Base.metadata.create_all(bind=engine)
            # Then run migrations (may need new tables to exist)
            _run_migrations()
            print("[STARTUP] Database tables ready")
        except Exception as e:
            print(f"[STARTUP] Database setup error: {e}")

    # Train LightFM model on startup (non-blocking failure)
    try:
        recommender = _train_recommender_from_db()
        if recommender:
            app.state.recommender = recommender
            app.state.recommender_trained_at = time.time()
            print("[STARTUP] LightFM model trained and cached")
        else:
            print("[STARTUP] LightFM model not available (no data or training failed)")
    except Exception as e:
        print(f"[STARTUP] LightFM training error: {e}")


@app.get("/init-db")
async def initialize_database(api_key: str = Query(None)):
    """
    Initialize database tables. Requires ADMIN_API_KEY.

    Usage: /init-db?api_key=YOUR_ADMIN_KEY
    """
    expected_key = os.environ.get("ADMIN_API_KEY", "")
    if not expected_key or api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    try:
        from src.db.database import engine, Base
        from src.models.user import User, EmailLog, ClickTracking, EventPopularity, UserPreferences, OnboardingLike, DigestOverride  # noqa: F401
        from src.models.interactions import WebsiteInteraction  # noqa: F401

        if engine is None:
            raise HTTPException(status_code=500, detail="DATABASE_URL not configured")

        Base.metadata.create_all(bind=engine)
        _run_migrations()

        # List created tables
        tables = list(Base.metadata.tables.keys())

        return {
            "success": True,
            "message": "Database tables created/migrated successfully",
            "tables": tables
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error creating tables: {str(e)}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
