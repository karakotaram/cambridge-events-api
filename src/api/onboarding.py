"""Onboarding API endpoints for user signup and email tracking"""
import os
import secrets
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy import func

from src.db.database import get_db
from src.models.user import (
    User,
    EmailLog,
    ClickTracking,
    OnboardingSubmit,
    OnboardingResponse,
    UnsubscribeRequest,
    QuestionsResponse,
    AdminStats,
    ArchetypeEnum,
    EmailStatus,
)
from src.services.onboarding import (
    get_questionnaire,
    calculate_archetype,
    get_archetype_result,
)
from src.services.archetypes import get_archetype_description


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


@router.get("/questions", response_model=QuestionsResponse)
async def get_questions():
    """
    Get the onboarding questionnaire questions.

    Returns the 4 questions with their options for the signup flow.
    """
    questions = get_questionnaire()
    return QuestionsResponse(questions=questions)


@router.post("/submit", response_model=OnboardingResponse)
async def submit_onboarding(
    data: OnboardingSubmit,
    db: Session = Depends(get_db)
):
    """
    Submit onboarding questionnaire and create user.

    Calculates the user's archetype based on responses and creates
    their account for weekly email recommendations.
    """
    # Check if user already exists
    existing = db.query(User).filter(User.email == data.email).first()
    if existing:
        # Return existing user's archetype info
        result = get_archetype_result(
            existing.primary_archetype,
            existing.secondary_archetype
        )
        return OnboardingResponse(
            success=True,
            user_id=str(existing.id),
            primary_archetype=result["primary_archetype"],
            secondary_archetype=result.get("secondary_archetype"),
            archetype_description=result["description"],
            message="Welcome back! You're already signed up."
        )

    # Calculate archetype
    primary, secondary = calculate_archetype(data.responses)
    result = get_archetype_result(primary, secondary)

    # Create user
    user = User(
        email=data.email,
        primary_archetype=primary,
        secondary_archetype=secondary,
        questionnaire_responses=data.responses.model_dump(),
        email_opt_in=True,
        unsubscribe_token=secrets.token_urlsafe(32)
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return OnboardingResponse(
        success=True,
        user_id=str(user.id),
        primary_archetype=result["primary_archetype"],
        secondary_archetype=result.get("secondary_archetype"),
        archetype_description=result["description"],
        message=f"Welcome! You've been identified as a {result['primary_name']}."
    )


@router.post("/unsubscribe")
async def unsubscribe(
    data: UnsubscribeRequest,
    db: Session = Depends(get_db)
):
    """
    Unsubscribe a user using their unique token.
    """
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
    """
    Unsubscribe via GET request (for email links).
    """
    user = db.query(User).filter(User.unsubscribe_token == token).first()
    if not user:
        raise HTTPException(status_code=404, detail="Invalid unsubscribe token")

    user.email_opt_in = False
    db.commit()

    # Return simple HTML confirmation
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


@router.get("/track/open/{email_log_id}")
async def track_open(
    email_log_id: str,
    db: Session = Depends(get_db)
):
    """
    Track email opens via 1x1 transparent pixel.

    This endpoint is called when the email is opened and the
    tracking pixel image loads.
    """
    try:
        email_log = db.query(EmailLog).filter(EmailLog.id == email_log_id).first()
        if email_log:
            if email_log.opened_at is None:
                email_log.opened_at = datetime.utcnow()
            email_log.open_count += 1
            db.commit()
    except Exception:
        pass  # Silently fail - don't break email viewing

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
    """
    Track link clicks and redirect to destination.

    All event links in emails are wrapped with this tracker.
    """
    import urllib.parse

    try:
        # Convert string to UUID for comparison
        import uuid
        click_uuid = uuid.UUID(click_id)
        click = db.query(ClickTracking).filter(ClickTracking.id == click_uuid).first()
        if click:
            click.clicked_at = datetime.utcnow()
            db.commit()
    except Exception as e:
        print(f"Click tracking error: {e}")  # Log but don't break redirect

    # Decode the redirect URL if it's encoded
    decoded_redirect = urllib.parse.unquote(redirect)
    return RedirectResponse(url=decoded_redirect, status_code=302)


@router.get("/admin/stats", response_model=AdminStats)
async def get_admin_stats(
    verified: bool = Depends(verify_admin_key),
    db: Session = Depends(get_db)
):
    """
    Get email analytics for admin dashboard.

    Requires API key authentication.
    """
    # Total users
    total_users = db.query(func.count(User.id)).scalar() or 0

    # Users by archetype
    archetype_counts = db.query(
        User.primary_archetype,
        func.count(User.id)
    ).group_by(User.primary_archetype).all()

    users_by_archetype = {
        str(arch.value if hasattr(arch, 'value') else arch): count
        for arch, count in archetype_counts
    }

    # Emails sent in last 7 days
    week_ago = datetime.utcnow() - timedelta(days=7)
    emails_last_7_days = db.query(func.count(EmailLog.id)).filter(
        EmailLog.sent_at >= week_ago
    ).scalar() or 0

    # Total opens
    total_opens = db.query(func.sum(EmailLog.open_count)).scalar() or 0

    # Total clicks
    total_clicks = db.query(func.count(ClickTracking.id)).scalar() or 0

    # Calculate rates
    total_emails = db.query(func.count(EmailLog.id)).scalar() or 0
    open_rate = (total_opens / total_emails * 100) if total_emails > 0 else 0.0
    click_rate = (total_clicks / total_emails * 100) if total_emails > 0 else 0.0

    return AdminStats(
        total_users=total_users,
        users_by_archetype=users_by_archetype,
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
    """
    Get list of users for admin dashboard.
    """
    users = db.query(User).order_by(User.created_at.desc()).offset(offset).limit(limit).all()

    return {
        "users": [
            {
                "id": str(u.id),
                "email": u.email,
                "primary_archetype": u.primary_archetype.value,
                "secondary_archetype": u.secondary_archetype.value if u.secondary_archetype else None,
                "email_opt_in": u.email_opt_in,
                "created_at": u.created_at.isoformat(),
                "last_email_sent": u.last_email_sent.isoformat() if u.last_email_sent else None,
            }
            for u in users
        ],
        "total": db.query(func.count(User.id)).scalar() or 0
    }


@router.post("/admin/send-test-email")
async def send_test_email(
    email: str = Query(...),
    verified: bool = Depends(verify_admin_key),
    db: Session = Depends(get_db)
):
    """
    Send a test email to a specific user.

    Requires API key authentication.
    """
    import json
    import os
    import traceback
    from pathlib import Path

    try:
        # Check if RESEND_API_KEY is set
        if not os.environ.get("RESEND_API_KEY"):
            raise HTTPException(status_code=500, detail="RESEND_API_KEY not configured in environment variables")

        # Find user
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"User not found. Make sure {email} has signed up first at /signup")

        # Load events
        project_root = Path(__file__).parent.parent.parent
        events_file = project_root / "data" / "events.json"

        if not events_file.exists():
            raise HTTPException(status_code=500, detail=f"Events file not found at {events_file}")

        from src.models.event import Event
        with open(events_file, 'r') as f:
            data = json.load(f)
            events = [Event(**event) for event in data]

        # Get recommendations
        from src.services.recommendation import get_weekly_digest_events
        recommended = get_weekly_digest_events(
            events,
            user.primary_archetype,
            user.secondary_archetype
        )

        if not recommended:
            raise HTTPException(status_code=400, detail="No events to recommend for this user's archetype")

        # Send email
        from src.services.email_service import send_weekly_digest
        email_log_id = send_weekly_digest(user, recommended, db)

        if email_log_id:
            return {"success": True, "email_log_id": email_log_id, "events_sent": len(recommended)}
        else:
            raise HTTPException(status_code=500, detail="Failed to send email - check RESEND_API_KEY is valid")

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
    """
    Trigger the weekly email job.

    This endpoint is called by the GitHub Action cron job.
    """
    import json
    from pathlib import Path
    from datetime import timedelta

    # Load events
    project_root = Path(__file__).parent.parent.parent
    events_file = project_root / "data" / "events.json"

    if not events_file.exists():
        raise HTTPException(status_code=500, detail="Events file not found")

    from src.models.event import Event
    with open(events_file, 'r') as f:
        data = json.load(f)
        events = [Event(**event) for event in data]

    # Get users who need emails (haven't received in 6+ days)
    six_days_ago = datetime.utcnow() - timedelta(days=6)
    users = db.query(User).filter(
        User.email_opt_in == True,
        (User.last_email_sent == None) | (User.last_email_sent < six_days_ago)
    ).limit(max_users).all()

    if not users:
        return {"success": True, "message": "No users need emails", "sent": 0, "failed": 0}

    # Send emails
    from src.services.recommendation import get_weekly_digest_events
    from src.services.email_service import send_weekly_digest

    sent = 0
    failed = 0

    for user in users:
        recommended = get_weekly_digest_events(
            events,
            user.primary_archetype,
            user.secondary_archetype
        )

        if not recommended:
            continue

        email_log_id = send_weekly_digest(user, recommended, db)
        if email_log_id:
            sent += 1
        else:
            failed += 1

    return {
        "success": True,
        "message": f"Weekly email job complete",
        "users_processed": len(users),
        "sent": sent,
        "failed": failed
    }
