"""Onboarding API endpoints for user signup and email tracking"""
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from src.db.database import get_db
from src.models.user import (
    User,
    UserPreferences,
    OnboardingLike,
    DigestOverride,
    EmailLog,
    ClickTracking,
    OnboardingSubmit,
    OnboardingResponse,
    UnsubscribeRequest,
    AdminStats,
    EmailStatus,
)


router = APIRouter(prefix="/onboarding", tags=["onboarding"])

# 1x1 transparent GIF for email open tracking
TRACKING_PIXEL = bytes([
    0x47, 0x49, 0x46, 0x38, 0x39, 0x61,  # GIF89a
    0x01, 0x00, 0x01, 0x00,              # 1x1
    0x80, 0x00, 0x00,                     # Global color table
    0xff, 0xff, 0xff,                     # White
    0x00, 0x00, 0x00,                     # Black
    0x21, 0xf9, 0x04,                     # Graphic control
    0x01, 0x00, 0x00, 0x00, 0x00,         # Transparent
    0x2c, 0x00, 0x00, 0x00, 0x00,         # Image descriptor
    0x01, 0x00, 0x01, 0x00, 0x00,         # 1x1 image
    0x02, 0x02, 0x44, 0x01, 0x00,         # Image data
    0x3b                                   # Trailer
])


def get_admin_api_key() -> str:
    """Get admin API key from environment"""
    return os.environ.get("ADMIN_API_KEY", "")


def verify_admin_key(api_key: str = Query(...)) -> bool:
    """Verify admin API key"""
    expected_key = get_admin_api_key()
    if not expected_key or api_key != expected_key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


def _load_events():
    """Helper to load events from JSON file."""
    import json
    from pathlib import Path
    from src.models.event import Event

    project_root = Path(__file__).parent.parent.parent
    events_file = project_root / "data" / "events.json"

    if not events_file.exists():
        return []

    with open(events_file, "r") as f:
        data = json.load(f)
        return [Event(**e) for e in data]


# --- Public Endpoints ---


@router.get("/sample-events")
async def get_sample_events():
    """
    Get 10 diverse events for the onboarding thumbs-up screen.

    Returns events with: id, title, description (truncated), start_datetime,
    venue_name, category, cost, image_url, family_friendly.
    """
    events = _load_events()
    if not events:
        return {"events": []}

    from src.services.preferences import select_diverse_events
    selected = select_diverse_events(events, count=10)

    result = []
    for ev in selected:
        cat = ev.category.value if hasattr(ev.category, "value") else str(ev.category) if ev.category else "other"
        result.append({
            "id": ev.id,
            "title": ev.title,
            "description": ev.description,
            "start_datetime": ev.start_datetime.isoformat(),
            "venue_name": ev.venue_name or ev.source_name,
            "category": cat,
            "cost": ev.cost,
            "image_url": ev.image_url,
            "family_friendly": ev.family_friendly,
        })

    return {"events": result}


@router.post("/submit", response_model=OnboardingResponse)
async def submit_onboarding(
    data: OnboardingSubmit,
    db: Session = Depends(get_db)
):
    """
    Submit onboarding: create user from liked events.

    Creates the user, stores OnboardingLike rows, computes initial
    UserPreferences from liked events, and optionally sends welcome email.
    """
    # Check if user already exists
    existing = db.query(User).filter(User.email == data.email).first()
    is_returning = existing is not None

    if existing:
        user = existing
        # Re-opt in if they previously unsubscribed
        user.email_opt_in = True
        # Clear old likes and replace with new ones
        db.query(OnboardingLike).filter(OnboardingLike.user_id == user.id).delete()
    else:
        # Create new user
        user = User(
            email=data.email,
            email_opt_in=True,
            unsubscribe_token=secrets.token_urlsafe(32),
        )
        db.add(user)
        db.flush()  # Get user.id

    # Store OnboardingLike rows
    for event_id in data.liked_event_ids:
        db.add(OnboardingLike(user_id=user.id, event_id=event_id))

    # Compute preferences from liked events
    liked_events = []
    if data.liked_event_ids:
        events = _load_events()
        events_map = {e.id: e for e in events}
        liked_events = [events_map[eid] for eid in data.liked_event_ids if eid in events_map]

    from src.services.preferences import compute_preferences_from_likes
    pref_data = compute_preferences_from_likes(liked_events)

    # Upsert preferences
    prefs_row = db.query(UserPreferences).filter(UserPreferences.user_id == user.id).first()
    if prefs_row:
        prefs_row.category_weights = pref_data["category_weights"]
        prefs_row.timing_weights = pref_data["timing_weights"]
        prefs_row.venue_weights = pref_data["venue_weights"]
        prefs_row.price_sensitivity = pref_data["price_sensitivity"]
        prefs_row.prefers_family_friendly = pref_data["prefers_family_friendly"]
        prefs_row.updated_at = datetime.utcnow()
    else:
        db.add(UserPreferences(
            user_id=user.id,
            category_weights=pref_data["category_weights"],
            timing_weights=pref_data["timing_weights"],
            venue_weights=pref_data["venue_weights"],
            price_sensitivity=pref_data["price_sensitivity"],
            prefers_family_friendly=pref_data["prefers_family_friendly"],
        ))

    db.commit()
    db.refresh(user)

    # Send welcome email for new users only (non-blocking)
    if not is_returning:
        try:
            from src.services.email_service import send_welcome_email_to_user
            send_welcome_email_to_user(user, db, liked_count=len(data.liked_event_ids))
        except Exception as e:
            print(f"Welcome email failed for {user.email}: {e}")

    message = "Welcome back! We've updated your preferences." if is_returning else "You're all set! We'll send you personalized weekly recommendations."

    return OnboardingResponse(
        success=True,
        user_id=str(user.id),
        message=message,
        liked_count=len(data.liked_event_ids),
    )


# --- Unsubscribe Endpoints ---


@router.post("/unsubscribe")
async def unsubscribe(
    data: UnsubscribeRequest,
    db: Session = Depends(get_db)
):
    """Unsubscribe a user using their unique token."""
    user = db.query(User).filter(User.unsubscribe_token == data.token).first()
    if not user:
        raise HTTPException(status_code=404, detail="Invalid unsubscribe token")

    user.email_opt_in = False
    db.commit()

    return {"success": True, "message": "You've been unsubscribed from our emails."}


@router.get("/unsubscribe/{token}")
async def unsubscribe_get(
    token: str,
    db: Session = Depends(get_db)
):
    """Unsubscribe via GET request (for email links)."""
    user = db.query(User).filter(User.unsubscribe_token == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="Invalid unsubscribe token")

    user.email_opt_in = False
    db.commit()

    html = """
    <!DOCTYPE html>
    <html>
    <head><title>Unsubscribed</title></head>
    <body style="font-family: sans-serif; text-align: center; padding: 50px;">
        <h1>You've been unsubscribed</h1>
        <p>You will no longer receive weekly event emails from us.</p>
        <p>Changed your mind? <a href="/">Sign up again</a></p>
    </body>
    </html>
    """
    return Response(content=html, media_type="text/html")


# --- Tracking Endpoints ---


@router.get("/track/open/{email_log_id}")
async def track_open(
    email_log_id: str,
    db: Session = Depends(get_db)
):
    """Track email opens via 1x1 transparent pixel."""
    try:
        email_log = db.query(EmailLog).filter(EmailLog.id == email_log_id).first()
        if email_log:
            if email_log.opened_at is None:
                email_log.opened_at = datetime.utcnow()
            email_log.open_count += 1
            db.commit()
    except Exception:
        pass

    return Response(
        content=TRACKING_PIXEL,
        media_type="image/gif",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )


@router.get("/track/click/{click_id}")
async def track_click(
    click_id: str,
    redirect: str = Query(...),
    db: Session = Depends(get_db)
):
    """Track link clicks and redirect to destination."""
    import urllib.parse

    try:
        import uuid
        click_uuid = uuid.UUID(click_id)
        click = db.query(ClickTracking).filter(ClickTracking.id == click_uuid).first()
        if click:
            click.clicked_at = datetime.utcnow()
            db.commit()
    except Exception as e:
        print(f"Click tracking error: {e}")

    decoded_redirect = urllib.parse.unquote(redirect)
    return RedirectResponse(url=decoded_redirect, status_code=302)


# --- Admin Endpoints ---


@router.get("/admin/stats", response_model=AdminStats)
async def get_admin_stats(
    verified: bool = Depends(verify_admin_key),
    db: Session = Depends(get_db)
):
    """Get email analytics for admin dashboard."""
    total_users = db.query(func.count(User.id)).scalar() or 0

    week_ago = datetime.utcnow() - timedelta(days=7)
    emails_last_7_days = db.query(func.count(EmailLog.id)).filter(
        EmailLog.sent_at >= week_ago
    ).scalar() or 0

    total_opens = db.query(func.sum(EmailLog.open_count)).scalar() or 0
    total_clicks = db.query(func.count(ClickTracking.id)).scalar() or 0

    total_emails = db.query(func.count(EmailLog.id)).scalar() or 0
    open_rate = (total_opens / total_emails * 100) if total_emails > 0 else 0.0
    click_rate = (total_clicks / total_emails * 100) if total_emails > 0 else 0.0

    return AdminStats(
        total_users=total_users,
        emails_sent_last_7_days=emails_last_7_days,
        total_opens=total_opens,
        total_clicks=total_clicks,
        open_rate=round(open_rate, 2),
        click_rate=round(click_rate, 2)
    )


@router.get("/admin/users")
async def get_admin_users(
    verified: bool = Depends(verify_admin_key),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db)
):
    """Get list of users for admin dashboard."""
    users = db.query(User).order_by(User.created_at.desc()).offset(offset).limit(limit).all()

    result = []
    for u in users:
        # Get liked count
        liked_count = db.query(func.count(OnboardingLike.id)).filter(
            OnboardingLike.user_id == u.id
        ).scalar() or 0

        result.append({
            "id": str(u.id),
            "email": u.email,
            "email_opt_in": u.email_opt_in,
            "created_at": u.created_at.isoformat(),
            "last_email_sent": u.last_email_sent.isoformat() if u.last_email_sent else None,
            "liked_count": liked_count,
        })

    return {
        "users": result,
        "total": db.query(func.count(User.id)).scalar() or 0
    }


@router.get("/admin/user/{user_id}/preview-digest")
async def preview_user_digest(
    user_id: str,
    verified: bool = Depends(verify_admin_key),
    db: Session = Depends(get_db)
):
    """
    Preview the ~7 events that would be recommended for this specific user.
    Also returns any saved override if it exists.
    """
    import uuid as uuid_mod
    import traceback

    try:
        user_uuid = uuid_mod.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    user = db.query(User).filter(User.id == user_uuid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    try:
        events = _load_events()
        if not events:
            return {"events": [], "override": None, "user_email": user.email}

        # Get user preferences
        prefs_row = db.query(UserPreferences).filter(UserPreferences.user_id == user_uuid).first()
        prefs = {}
        if prefs_row:
            prefs = {
                "category_weights": prefs_row.category_weights or {},
                "timing_weights": prefs_row.timing_weights or {},
                "venue_weights": prefs_row.venue_weights or {},
                "price_sensitivity": prefs_row.price_sensitivity if prefs_row.price_sensitivity is not None else 0.5,
                "prefers_family_friendly": prefs_row.prefers_family_friendly or False,
            }

        # Get liked event IDs from onboarding
        liked_rows = db.query(OnboardingLike).filter(OnboardingLike.user_id == user_uuid).all()
        liked_event_ids = [row.event_id for row in liked_rows]

        # Train LightFM model on-demand for preview
        recommender = None
        try:
            from src.jobs.weekly_email import train_lightfm_model
            recommender = train_lightfm_model(db, events)
        except Exception as e:
            print(f"[Preview] LightFM training skipped: {e}")

        from src.services.recommendation import get_weekly_digest_events
        recommended = get_weekly_digest_events(
            events, prefs,
            liked_event_ids=liked_event_ids,
            user_uuid=user_id,
            recommender=recommender,
        )

        # Check for override first
        override = db.query(DigestOverride).filter(DigestOverride.user_id == user_uuid).first()
        override_data = None

        if override and override.event_ids:
            override_data = {
                "event_ids": override.event_ids,
                "created_at": override.created_at.isoformat() if override.created_at else None,
            }
            # Return override events as the main event list
            events_map = {e.id: e for e in events}
            events_out = []
            for eid in override.event_ids:
                ev = events_map.get(eid)
                if ev:
                    cat = ev.category.value if hasattr(ev.category, "value") else str(ev.category) if ev.category else "other"
                    events_out.append({
                        "id": ev.id,
                        "title": ev.title,
                        "start_datetime": ev.start_datetime.isoformat(),
                        "venue_name": ev.venue_name or ev.source_name,
                        "category": cat,
                        "cost": ev.cost,
                        "score": 1.0,
                        "image_url": ev.image_url,
                    })
        else:
            # No override — show algorithm recommendations
            events_out = []
            for ev, score in recommended:
                cat = ev.category.value if hasattr(ev.category, "value") else str(ev.category) if ev.category else "other"
                events_out.append({
                    "id": ev.id,
                    "title": ev.title,
                    "start_datetime": ev.start_datetime.isoformat(),
                    "venue_name": ev.venue_name or ev.source_name,
                    "category": cat,
                    "cost": ev.cost,
                    "score": round(score, 4),
                    "image_url": ev.image_url,
                })

        return {
            "user_email": user.email,
            "preferences": prefs,
            "events": events_out,
            "override": override_data,
        }

    except HTTPException:
        raise
    except Exception as e:
        error_details = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}\n\n{error_details}")


class OverrideDigestRequest(BaseModel):
    event_ids: List[str]


@router.post("/admin/user/{user_id}/override-digest")
async def override_user_digest(
    user_id: str,
    body: OverrideDigestRequest,
    verified: bool = Depends(verify_admin_key),
    db: Session = Depends(get_db)
):
    """Save a manually curated event list for this user's next email."""
    import uuid as uuid_mod

    try:
        user_uuid = uuid_mod.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    user = db.query(User).filter(User.id == user_uuid).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Upsert override
    existing = db.query(DigestOverride).filter(DigestOverride.user_id == user_uuid).first()
    if existing:
        existing.event_ids = body.event_ids
        existing.created_at = datetime.utcnow()
    else:
        db.add(DigestOverride(
            user_id=user_uuid,
            event_ids=body.event_ids,
            created_by="admin",
        ))
    db.commit()

    return {"success": True, "event_count": len(body.event_ids)}


@router.delete("/admin/user/{user_id}/override-digest")
async def clear_user_digest_override(
    user_id: str,
    verified: bool = Depends(verify_admin_key),
    db: Session = Depends(get_db)
):
    """Clear any saved override, revert to algorithmic picks."""
    import uuid as uuid_mod

    try:
        user_uuid = uuid_mod.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    existing = db.query(DigestOverride).filter(DigestOverride.user_id == user_uuid).first()
    if existing:
        db.delete(existing)
        db.commit()

    return {"success": True}


@router.get("/admin/user/{user_id}/history")
async def get_user_email_history(
    user_id: str,
    limit: int = Query(5, ge=1, le=50),
    verified: bool = Depends(verify_admin_key),
    db: Session = Depends(get_db)
):
    """Return last N emails sent to this user with open/click data."""
    import uuid as uuid_mod

    try:
        user_uuid = uuid_mod.UUID(user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    logs = db.query(EmailLog).filter(
        EmailLog.user_id == user_uuid
    ).order_by(EmailLog.sent_at.desc()).limit(limit).all()

    result = []
    for log in logs:
        click_count = db.query(func.count(ClickTracking.id)).filter(
            ClickTracking.email_log_id == log.id
        ).scalar() or 0

        result.append({
            "id": str(log.id),
            "subject": log.subject,
            "sent_at": log.sent_at.isoformat() if log.sent_at else None,
            "opened": log.opened_at is not None,
            "open_count": log.open_count,
            "click_count": click_count,
            "event_ids": log.event_ids or [],
        })

    return {"history": result}


@router.post("/admin/send-test-email")
async def send_test_email(
    email: str = Query(...),
    verified: bool = Depends(verify_admin_key),
    db: Session = Depends(get_db)
):
    """Send a test email to a specific user. Uses override events if one exists."""
    import traceback

    try:
        if not os.environ.get("RESEND_API_KEY"):
            raise HTTPException(status_code=500, detail="RESEND_API_KEY not configured")

        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found: {email}")

        events = _load_events()
        events_map = {e.id: e for e in events}

        # Check for override first
        override = db.query(DigestOverride).filter(DigestOverride.user_id == user.id).first()
        recommended = []
        used_override = False

        if override and override.event_ids:
            for eid in override.event_ids:
                event = events_map.get(eid)
                if event:
                    recommended.append((event, 1.0))
            if recommended:
                used_override = True

        # Fall back to algorithm if no override
        if not recommended:
            prefs_row = db.query(UserPreferences).filter(UserPreferences.user_id == user.id).first()
            prefs = {}
            if prefs_row:
                prefs = {
                    "category_weights": prefs_row.category_weights or {},
                    "timing_weights": prefs_row.timing_weights or {},
                    "venue_weights": prefs_row.venue_weights or {},
                    "price_sensitivity": prefs_row.price_sensitivity if prefs_row.price_sensitivity is not None else 0.5,
                    "prefers_family_friendly": prefs_row.prefers_family_friendly or False,
                }

            recommender = None
            try:
                from src.jobs.weekly_email import train_lightfm_model
                recommender = train_lightfm_model(db, events)
            except Exception as e:
                print(f"[TestEmail] LightFM training skipped: {e}")

            from src.services.recommendation import get_weekly_digest_events
            recommended = get_weekly_digest_events(
                events, prefs,
                user_uuid=str(user.id),
                recommender=recommender,
            )

        if not recommended:
            raise HTTPException(status_code=400, detail="No events to recommend")

        from src.services.email_service import send_weekly_digest
        email_log_id = send_weekly_digest(user, recommended, db)

        if email_log_id:
            return {
                "success": True,
                "email_log_id": email_log_id,
                "events_sent": len(recommended),
                "used_override": used_override,
            }
        else:
            raise HTTPException(status_code=500, detail="Failed to send email")

    except HTTPException:
        raise
    except Exception as e:
        error_details = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}\n\n{error_details}")


class CuratedEventItem(BaseModel):
    event_id: str
    score: float


class CuratedDigestRequest(BaseModel):
    email: str
    events: List[CuratedEventItem]


@router.post("/admin/send-curated-email")
async def send_curated_email(
    body: CuratedDigestRequest,
    verified: bool = Depends(verify_admin_key),
    db: Session = Depends(get_db),
):
    """Send a curated digest email with hand-picked events."""
    import traceback

    try:
        if not os.environ.get("RESEND_API_KEY"):
            raise HTTPException(status_code=500, detail="RESEND_API_KEY not configured")

        user = db.query(User).filter(User.email == body.email).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found: {body.email}")

        events = _load_events()
        events_map = {e.id: e for e in events}

        curated_events = []
        missing_ids = []
        for item in body.events:
            event = events_map.get(item.event_id)
            if event:
                curated_events.append((event, item.score))
            else:
                missing_ids.append(item.event_id)

        if missing_ids:
            raise HTTPException(status_code=400, detail=f"Event IDs not found: {missing_ids[:10]}")

        if not curated_events:
            raise HTTPException(status_code=400, detail="No events provided")

        from src.services.email_service import send_weekly_digest
        email_log_id = send_weekly_digest(user, curated_events, db)

        if email_log_id:
            return {"success": True, "email_log_id": email_log_id, "events_sent": len(curated_events)}
        else:
            raise HTTPException(status_code=500, detail="Failed to send email")

    except HTTPException:
        raise
    except Exception as e:
        error_details = traceback.format_exc()
        raise HTTPException(status_code=500, detail=f"Error: {str(e)}\n\n{error_details}")


@router.post("/admin/trigger-weekly-email")
async def trigger_weekly_email(
    max_users: int = Query(100, ge=1, le=500),
    verified: bool = Depends(verify_admin_key),
    db: Session = Depends(get_db)
):
    """Trigger the weekly email job."""
    events = _load_events()
    if not events:
        raise HTTPException(status_code=500, detail="Events file not found")

    events_map = {e.id: e for e in events}

    # Get users who need emails
    six_days_ago = datetime.utcnow() - timedelta(days=6)
    users = db.query(User).filter(
        User.email_opt_in == True,
        (User.last_email_sent == None) | (User.last_email_sent < six_days_ago)
    ).limit(max_users).all()

    if not users:
        return {"success": True, "message": "No users need emails", "sent": 0, "failed": 0}

    from src.services.recommendation import get_weekly_digest_events
    from src.services.email_service import send_weekly_digest

    # Train LightFM model once for the batch
    recommender = None
    try:
        from src.jobs.weekly_email import train_lightfm_model
        recommender = train_lightfm_model(db, events)
    except Exception as e:
        print(f"[TriggerEmail] LightFM training skipped: {e}")

    sent = 0
    failed = 0
    used_override = 0

    for user in users:
        # Check for digest override
        override = db.query(DigestOverride).filter(DigestOverride.user_id == user.id).first()
        recommended = []

        if override and override.event_ids:
            for eid in override.event_ids:
                event = events_map.get(eid)
                if event:
                    recommended.append((event, 1.0))
            if recommended:
                used_override += 1
            db.delete(override)
            db.flush()

        if not recommended:
            # Get preference-based recommendations
            prefs_row = db.query(UserPreferences).filter(UserPreferences.user_id == user.id).first()
            prefs = {}
            if prefs_row:
                prefs = {
                    "category_weights": prefs_row.category_weights or {},
                    "timing_weights": prefs_row.timing_weights or {},
                    "venue_weights": prefs_row.venue_weights or {},
                    "price_sensitivity": prefs_row.price_sensitivity if prefs_row.price_sensitivity is not None else 0.5,
                    "prefers_family_friendly": prefs_row.prefers_family_friendly or False,
                }

            # Get liked event IDs from onboarding
            liked_rows = db.query(OnboardingLike).filter(
                OnboardingLike.user_id == user.id
            ).all()
            liked_event_ids = [row.event_id for row in liked_rows]

            recommended = get_weekly_digest_events(
                events, prefs,
                liked_event_ids=liked_event_ids,
                user_uuid=str(user.id),
                recommender=recommender,
            )

        if not recommended:
            continue

        try:
            email_log_id = send_weekly_digest(user, recommended, db)
            sent += 1
        except Exception as e:
            print(f"Failed to send to {user.email}: {e}")
            failed += 1

    return {
        "success": True,
        "message": "Weekly email job complete",
        "users_processed": len(users),
        "sent": sent,
        "failed": failed,
        "used_override": used_override,
    }
