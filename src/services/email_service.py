"""Email service using Resend for sending weekly digests"""
import os
import uuid
from datetime import datetime
from typing import List, Tuple, Optional
from pathlib import Path
import json

from jinja2 import Environment, FileSystemLoader

# Resend import is optional - only needed when sending
try:
    import resend
    RESEND_AVAILABLE = True
except ImportError:
    RESEND_AVAILABLE = False

from src.models.event import Event
from src.models.user import User, EmailLog, ArchetypeEnum


# Template directory
TEMPLATE_DIR = Path(__file__).parent.parent / "templates" / "email"

# Jinja2 environment
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=True
)


def get_resend_client():
    """Get Resend client with API key"""
    api_key = os.environ.get("RESEND_API_KEY")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY environment variable not set")
    if not RESEND_AVAILABLE:
        raise RuntimeError("resend package not installed")
    resend.api_key = api_key
    return resend


def get_api_base_url() -> str:
    """Get base URL for tracking links"""
    url = os.environ.get("API_BASE_URL", "https://web-production-00281.up.railway.app")
    return url.strip()  # Remove any accidental whitespace


def render_weekly_digest(
    user: User,
    events: List[Tuple[Event, float]],
) -> Tuple[str, str]:
    """
    Render the weekly digest email.

    Args:
        user: The user receiving the email
        events: List of (event, score) tuples

    Returns:
        Tuple of (subject, html_body)
    """
    base_url = get_api_base_url()

    # Prepare events with direct links (Resend handles open/click tracking)
    events_data = []
    for event, score in events:
        # Format date nicely
        event_dt = event.start_datetime
        date_str = event_dt.strftime("%A, %B %d")
        time_str = event_dt.strftime("%I:%M %p").lstrip("0")

        # Handle category as string or enum
        if event.category:
            category = event.category.value if hasattr(event.category, 'value') else str(event.category)
        else:
            category = "Event"

        events_data.append({
            "title": event.title,
            "description": event.description[:200] + "..." if len(event.description) > 200 else event.description,
            "date": date_str,
            "time": time_str,
            "venue": event.venue_name or "TBA",
            "cost": event.cost or "See website",
            "category": category,
            "image_url": event.image_url,
            "link": event.source_url,
            "family_friendly": event.family_friendly,
        })

    # Get archetype name
    from src.services.archetypes import get_archetype_name
    archetype_name = get_archetype_name(user.primary_archetype)

    # Unsubscribe link
    unsubscribe_url = f"{base_url}/onboarding/unsubscribe/{user.unsubscribe_token}"

    # Render template
    template = jinja_env.get_template("weekly_digest.html")
    html_body = template.render(
        user_email=user.email,
        archetype_name=archetype_name,
        events=events_data,
        unsubscribe_url=unsubscribe_url,
        current_year=datetime.now().year,
    )

    # Subject line
    subject = f"Your Week in Cambridge: {len(events)} Events for {archetype_name}s"

    return subject, html_body


def render_welcome_email(user: User) -> Tuple[str, str]:
    """
    Render the welcome email for new subscribers.

    Returns:
        Tuple of (subject, html_body)
    """
    base_url = get_api_base_url()

    from src.services.archetypes import get_archetype_name, get_archetype_description
    archetype_name = get_archetype_name(user.primary_archetype)
    archetype_desc = get_archetype_description(user.primary_archetype)

    secondary_name = None
    if user.secondary_archetype:
        secondary_name = get_archetype_name(user.secondary_archetype)

    unsubscribe_url = f"{base_url}/onboarding/unsubscribe/{user.unsubscribe_token}"

    template = jinja_env.get_template("welcome.html")
    html_body = template.render(
        user_email=user.email,
        archetype_name=archetype_name,
        archetype_description=archetype_desc,
        secondary_archetype_name=secondary_name,
        unsubscribe_url=unsubscribe_url,
        current_year=datetime.now().year,
    )

    subject = f"Welcome, {archetype_name}! Your Cambridge Events Await"

    return subject, html_body


def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    from_email: str = "Cambridge Events <onboarding@resend.dev>"
) -> Optional[str]:
    """
    Send an email via Resend.

    Args:
        to_email: Recipient email address
        subject: Email subject
        html_body: HTML content
        from_email: Sender email (default uses Resend's shared domain)

    Returns:
        Resend message ID if successful, None if failed
    """
    client = get_resend_client()

    try:
        response = resend.Emails.send({
            "from": from_email,
            "to": [to_email],
            "subject": subject,
            "html": html_body,
        })
        return response.get("id")
    except Exception as e:
        print(f"Error sending email to {to_email}: {e}")
        return None


def send_weekly_digest(
    user: User,
    events: List[Tuple[Event, float]],
    db_session
) -> Optional[str]:
    """
    Send weekly digest email to a user.

    Args:
        user: User to send email to
        events: List of (event, score) tuples
        db_session: SQLAlchemy session for logging

    Returns:
        EmailLog ID if successful, None if failed
    """
    if not user.email_opt_in:
        return None

    if not events:
        return None

    # Create email log entry first
    email_log = EmailLog(
        user_id=user.id,
        subject="",  # Will update after rendering
        event_ids=[e.id for e, _ in events],
    )
    db_session.add(email_log)
    db_session.flush()  # Get the ID

    # Render email (Resend handles open/click tracking natively)
    subject, html_body = render_weekly_digest(user, events)

    # Update email log with subject
    email_log.subject = subject

    # Send email
    message_id = send_email(user.email, subject, html_body)

    if message_id:
        email_log.resend_message_id = message_id
        user.last_email_sent = datetime.utcnow()
    else:
        from src.models.user import EmailStatus
        email_log.status = EmailStatus.FAILED

    db_session.commit()

    return str(email_log.id) if message_id else None


def send_welcome_email_to_user(user: User, db_session) -> Optional[str]:
    """
    Send welcome email to a new user.

    Returns:
        EmailLog ID if successful, None if failed
    """
    subject, html_body = render_welcome_email(user)

    # Create email log
    email_log = EmailLog(
        user_id=user.id,
        subject=subject,
        event_ids=[],
    )
    db_session.add(email_log)
    db_session.flush()

    # Send
    message_id = send_email(user.email, subject, html_body)

    if message_id:
        email_log.resend_message_id = message_id
    else:
        from src.models.user import EmailStatus
        email_log.status = EmailStatus.FAILED

    db_session.commit()

    return str(email_log.id) if message_id else None
