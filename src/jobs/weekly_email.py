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
from src.models.user import User, EmailLog
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
    from src.models.user import ClickTracking

    results = db.query(
        ClickTracking.event_id,
        func.count(ClickTracking.id).label('count')
    ).group_by(ClickTracking.event_id).all()

    return {event_id: count for event_id, count in results}


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

        # Get click data for popularity boosting
        click_data = get_click_data(db)
        print(f"Loaded click data for {len(click_data)} events")

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

        for user in users:
            print(f"\nProcessing user: {user.email}")
            print(f"  Primary archetype: {user.primary_archetype.value}")

            # Get personalized events
            recommended = get_weekly_digest_events(
                events,
                user.primary_archetype,
                user.secondary_archetype,
                exclude_event_ids=None,  # Could track sent events to avoid repeats
                click_data=click_data
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
            email_log_id = send_weekly_digest(user, recommended, db)

            if email_log_id:
                print(f"  Sent email successfully (log: {email_log_id})")
                sent_count += 1
            else:
                print(f"  Failed to send email")
                failed_count += 1

        # Print summary
        print(f"\n{'='*50}")
        print(f"Weekly email job complete!")
        print(f"  Sent: {sent_count}")
        print(f"  Failed: {failed_count}")
        print(f"  Skipped: {skipped_count}")
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
