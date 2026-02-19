"""Weekly email job for sending personalized event digests"""
import os
import sys
import json
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path for imports
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from sqlalchemy.orm import Session
from src.db.database import SessionLocal, engine, Base
from src.models.user import User, UserPreferences, DigestOverride, EmailLog, ClickTracking
from src.models.event import Event
from src.services.recommendation import get_weekly_digest_events
from src.services.email_service import send_weekly_digest


def load_events() -> list:
    """Load events from JSON file"""
    events_file = project_root / "data" / "events.json"
    if not events_file.exists():
        print(f"Events file not found: {events_file}")
        return []

    with open(events_file, 'r') as f:
        data = json.load(f)
        return [Event(**event) for event in data]


def get_users_to_email(db: Session, batch_size: int = 100) -> list:
    """
    Get users who should receive emails.

    Returns users who:
    - Have email_opt_in = True
    - Haven't received an email in the last 6 days
    """
    six_days_ago = datetime.utcnow() - timedelta(days=6)

    users = db.query(User).filter(
        User.email_opt_in == True,
        (User.last_email_sent == None) | (User.last_email_sent < six_days_ago)
    ).limit(batch_size).all()

    return users


def get_click_data(db: Session) -> dict:
    """Get click counts per event for popularity boosting"""
    from sqlalchemy import func

    results = db.query(
        ClickTracking.event_id,
        func.count(ClickTracking.id).label('count')
    ).group_by(ClickTracking.event_id).all()

    return {event_id: count for event_id, count in results}


def get_user_prefs_dict(user: User, db: Session) -> dict:
    """Get user preferences as a plain dict for scoring."""
    prefs = db.query(UserPreferences).filter(UserPreferences.user_id == user.id).first()
    if prefs:
        return {
            "category_weights": prefs.category_weights or {},
            "timing_weights": prefs.timing_weights or {},
            "venue_weights": prefs.venue_weights or {},
            "price_sensitivity": prefs.price_sensitivity if prefs.price_sensitivity is not None else 0.5,
            "prefers_family_friendly": prefs.prefers_family_friendly or False,
        }
    # Default neutral preferences
    return {
        "category_weights": {},
        "timing_weights": {},
        "venue_weights": {},
        "price_sensitivity": 0.5,
        "prefers_family_friendly": False,
    }


def update_preferences_from_recent_clicks(user: User, events_map: dict, db: Session):
    """Update user preferences based on recent click engagement."""
    from src.services.preferences import update_preferences_from_engagement

    # Get clicks from last 14 days
    two_weeks_ago = datetime.utcnow() - timedelta(days=14)
    recent_clicks = db.query(ClickTracking).filter(
        ClickTracking.user_id == user.id,
        ClickTracking.clicked_at >= two_weeks_ago,
    ).all()

    if not recent_clicks:
        return

    # Resolve clicked events
    clicked_events = []
    for click in recent_clicks:
        event = events_map.get(click.event_id)
        if event:
            clicked_events.append(event)

    if not clicked_events:
        return

    # Get current preferences
    prefs_row = db.query(UserPreferences).filter(UserPreferences.user_id == user.id).first()
    if not prefs_row:
        return

    current = {
        "category_weights": prefs_row.category_weights or {},
        "timing_weights": prefs_row.timing_weights or {},
        "venue_weights": prefs_row.venue_weights or {},
        "price_sensitivity": prefs_row.price_sensitivity if prefs_row.price_sensitivity is not None else 0.5,
        "prefers_family_friendly": prefs_row.prefers_family_friendly or False,
    }

    updated = update_preferences_from_engagement(current, clicked_events)

    # Save back
    prefs_row.category_weights = updated["category_weights"]
    prefs_row.timing_weights = updated["timing_weights"]
    prefs_row.venue_weights = updated["venue_weights"]
    prefs_row.price_sensitivity = updated["price_sensitivity"]
    prefs_row.prefers_family_friendly = updated["prefers_family_friendly"]
    prefs_row.updated_at = datetime.utcnow()
    db.flush()


def train_lightfm_model(db: Session, events: list):
    """
    Train LightFM model from all OnboardingLikes and ClickTracking data.

    Returns:
        LightFMRecommender instance if training succeeded, None otherwise
    """
    try:
        from src.services.lightfm_recommender import LightFMRecommender
        from src.models.user import OnboardingLike, ClickTracking

        # Gather likes: (user_uuid_str, event_id)
        likes_rows = db.query(OnboardingLike).all()
        likes = [(str(row.user_id), row.event_id) for row in likes_rows]

        # Gather clicks: (user_uuid_str, event_id, position)
        clicks_rows = db.query(ClickTracking).all()
        clicks = [
            (str(row.user_id), row.event_id, row.event_position)
            for row in clicks_rows
        ]

        if not likes and not clicks:
            print("[LightFM] No interaction data, skipping training")
            return None

        recommender = LightFMRecommender()
        success = recommender.train(events, likes, clicks)

        if success:
            return recommender
        return None

    except Exception as e:
        print(f"[LightFM] Training failed: {e}")
        import traceback
        traceback.print_exc()
        return None


def run_weekly_email_job(dry_run: bool = False, max_users: int = None):
    """
    Run the weekly email job.

    Args:
        dry_run: If True, don't actually send emails
        max_users: Maximum number of users to process (for testing)
    """
    print(f"Starting weekly email job at {datetime.utcnow().isoformat()}")

    # Check database connection
    if SessionLocal is None:
        print("ERROR: Database not configured. Set DATABASE_URL environment variable.")
        return

    db = SessionLocal()

    try:
        # Load events
        events = load_events()
        if not events:
            print("No events loaded. Exiting.")
            return

        print(f"Loaded {len(events)} events")
        events_map = {e.id: e for e in events}

        # Get click data for popularity boosting
        click_data = get_click_data(db)
        print(f"Loaded click data for {len(click_data)} events")

        # Train LightFM model (once per batch)
        recommender = train_lightfm_model(db, events)
        if recommender:
            print("LightFM model trained successfully")
        else:
            print("LightFM training skipped/failed, using multiplier fallback")

        # Get users to email
        batch_size = max_users or 100
        users = get_users_to_email(db, batch_size)
        print(f"Found {len(users)} users to email")

        if not users:
            print("No users need emails. Exiting.")
            return

        # Track stats
        sent_count = 0
        failed_count = 0
        skipped_count = 0
        used_override = 0

        for user in users:
            print(f"\nProcessing user: {user.email}")

            # Step 1: Update preferences from recent clicks
            try:
                update_preferences_from_recent_clicks(user, events_map, db)
            except Exception as e:
                print(f"  Warning: Failed to update preferences: {e}")

            # Step 2: Check for digest override
            override = db.query(DigestOverride).filter(
                DigestOverride.user_id == user.id
            ).first()

            recommended = []
            if override and override.event_ids:
                # Use override event IDs
                for eid in override.event_ids:
                    event = events_map.get(eid)
                    if event:
                        recommended.append((event, 1.0))
                if recommended:
                    used_override += 1
                    print(f"  Using admin override ({len(recommended)} events)")
                # Clear override after use (one-time)
                db.delete(override)
                db.flush()

            if not recommended:
                # Step 3: Get preference-based recommendations
                prefs = get_user_prefs_dict(user, db)

                # Get liked event IDs from onboarding
                from src.models.user import OnboardingLike
                liked_rows = db.query(OnboardingLike).filter(
                    OnboardingLike.user_id == user.id
                ).all()
                liked_event_ids = [row.event_id for row in liked_rows]

                recommended = get_weekly_digest_events(
                    events,
                    prefs,
                    exclude_event_ids=None,
                    click_data=click_data,
                    liked_event_ids=liked_event_ids,
                    user_uuid=str(user.id),
                    recommender=recommender,
                )

            if not recommended:
                print(f"  No events found for user. Skipping.")
                skipped_count += 1
                continue

            print(f"  Found {len(recommended)} recommended events")

            if dry_run:
                print(f"  [DRY RUN] Would send email with events:")
                for event, score in recommended[:5]:
                    print(f"    - {event.title} (score: {score:.3f})")
                continue

            # Send email
            try:
                email_log_id = send_weekly_digest(user, recommended, db)
                if email_log_id:
                    print(f"  Sent email successfully (log: {email_log_id})")
                    sent_count += 1
                else:
                    print(f"  Failed to send email")
                    failed_count += 1
            except Exception as e:
                print(f"  Failed to send to {user.email}: {e}")
                failed_count += 1

        # Print summary
        print(f"\n{'='*50}")
        print(f"Weekly email job complete!")
        print(f"  Sent: {sent_count}")
        print(f"  Failed: {failed_count}")
        print(f"  Skipped: {skipped_count}")
        print(f"  Used override: {used_override}")
        print(f"{'='*50}")

    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()

    finally:
        db.close()


def main():
    """CLI entry point"""
    import argparse

    parser = argparse.ArgumentParser(description="Send weekly event digest emails")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually send emails, just show what would be sent"
    )
    parser.add_argument(
        "--max-users",
        type=int,
        default=None,
        help="Maximum number of users to process (for testing)"
    )

    args = parser.parse_args()

    run_weekly_email_job(
        dry_run=args.dry_run,
        max_users=args.max_users
    )


if __name__ == "__main__":
    main()
