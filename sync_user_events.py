#!/usr/bin/env python3
"""
Sync user-submitted events from Google Sheets.

This script:
1. Fetches approved events from Google Sheets
2. Validates and processes them
3. Adds them to the events database
4. Generates an audit HTML file
5. Marks events as uploaded in the sheet

Run weekly via GitHub Actions or manually.
"""
import json
import logging
import os
import uuid
from datetime import datetime
from typing import List

from src.scrapers.google_sheets import GoogleSheetsScraper
from src.utils.validator import EventValidator
from src.utils.deduplicator import EventDeduplicator
from src.models.event import Event, EventCreate

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('logs/user_events_sync.log', mode='a')
    ]
)
logger = logging.getLogger(__name__)

SOURCE_NAME = "User Submitted"
AUDIT_FILE = "user_submitted_audit.html"
EVENTS_FILE = "data/events.json"
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")


def send_admin_notification(new_events: List[Event]):
    """Send email notification to admin about newly synced events"""
    if not ADMIN_EMAIL:
        logger.info("ADMIN_EMAIL not set, skipping notification")
        return

    try:
        from src.services.email_service import send_email
    except Exception as e:
        logger.warning(f"Could not import email service: {e}")
        return

    lines = []
    for event in new_events:
        dt = event.start_datetime.strftime('%a, %b %d at %I:%M %p')
        venue = event.venue_name or 'TBA'
        lines.append(
            f"<li><strong>{event.title}</strong><br>"
            f"{dt} — {venue}<br>"
            f"<em>{event.description[:150]}</em></li>"
        )

    html_body = f"""
    <h2>{len(new_events)} New User-Submitted Event{'s' if len(new_events) != 1 else ''} Synced</h2>
    <p>The following approved events were synced from Google Sheets to the calendar:</p>
    <ul>{''.join(lines)}</ul>
    <p>View them in the <a href="https://docs.google.com/spreadsheets/d/{os.environ.get('GOOGLE_SHEET_ID', '')}">Google Sheet</a>
    or remove any that look like spam.</p>
    """

    subject = f"[Cambridge Events] {len(new_events)} new submitted event{'s' if len(new_events) != 1 else ''} auto-approved"

    try:
        send_email(ADMIN_EMAIL, subject, html_body)
        logger.info(f"Sent admin notification to {ADMIN_EMAIL}")
    except Exception as e:
        logger.warning(f"Failed to send admin notification: {e}")


def generate_audit_html(events: List[Event], output_path: str = AUDIT_FILE):
    """Generate audit HTML for user-submitted events"""

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>User Submitted Events - Audit</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        h1 {{ color: #333; border-bottom: 3px solid #27ae60; padding-bottom: 10px; }}
        .summary {{ background: #fff; padding: 15px; border-radius: 8px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
        .event-card {{ background: #fff; border-radius: 8px; overflow: hidden; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); display: grid; grid-template-columns: 300px 1fr; }}
        .event-image {{ width: 300px; height: 225px; object-fit: cover; background: #ddd; }}
        .no-image {{ width: 300px; height: 225px; background: #ecf0f1; display: flex; align-items: center; justify-content: center; color: #95a5a6; font-size: 0.9em; }}
        .event-content {{ padding: 20px; }}
        .event-title {{ margin: 0 0 10px 0; color: #2c3e50; font-size: 1.4em; }}
        .event-meta {{ display: flex; flex-wrap: wrap; gap: 15px; margin-bottom: 15px; font-size: 0.9em; }}
        .meta-item {{ background: #ecf0f1; padding: 5px 10px; border-radius: 4px; }}
        .meta-item.category {{ background: #3498db; color: white; }}
        .meta-item.family {{ background: #27ae60; color: white; }}
        .meta-item.cost {{ background: #9b59b6; color: white; }}
        .event-description {{ color: #555; line-height: 1.6; margin-bottom: 15px; }}
        .venue-info {{ font-size: 0.9em; color: #666; margin-bottom: 10px; }}
        .event-links a {{ display: inline-block; margin-right: 15px; color: #27ae60; text-decoration: none; }}
        .event-links a:hover {{ text-decoration: underline; }}
        .field-label {{ font-weight: bold; color: #666; font-size: 0.85em; }}
        @media (max-width: 768px) {{ .event-card {{ grid-template-columns: 1fr; }} .event-image, .no-image {{ width: 100%; height: 200px; }} }}
    </style>
</head>
<body>
    <h1>User Submitted Events</h1>
    <div class="summary">
        <strong>Total Events:</strong> {len(events)} |
        <strong>Source:</strong> Community Submissions via Google Forms |
        <strong>Generated:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M')}
    </div>
"""

    for i, event in enumerate(events, 1):
        # Format datetime
        dt = event.start_datetime
        date_str = dt.strftime('%a, %b %d, %Y at %I:%M %p')

        # Build location string
        location_parts = []
        if event.venue_name:
            location_parts.append(event.venue_name)
        if event.street_address:
            location_parts.append(event.street_address)
        if event.city:
            city_state = event.city
            if event.state:
                city_state += f", {event.state}"
            location_parts.append(city_state)
        location = ' - '.join(location_parts) if location_parts else 'Location TBA'

        # Category badge
        category = event.category if event.category else 'other'

        # Image or placeholder
        if event.image_url:
            image_html = f'<img class="event-image" src="{event.image_url}" alt="{event.title}" onerror="this.outerHTML=\'<div class=no-image>Image not available</div>\'">'
        else:
            image_html = '<div class="no-image">No image provided</div>'

        # Family friendly badge
        family_badge = '<span class="meta-item family">Family Friendly</span>' if event.family_friendly else ''

        # Cost badge
        cost_html = f'<span class="meta-item cost">{event.cost}</span>' if event.cost else ''

        # Full description (no truncation)
        desc = event.description

        html += f"""
    <div class="event-card">
        {image_html}
        <div class="event-content">
            <h2 class="event-title">{i}. {event.title}</h2>
            <div class="event-meta">
                <span class="meta-item">{date_str}</span>
                <span class="meta-item category">{category}</span>
                {family_badge}
                {cost_html}
            </div>
            <div class="venue-info">{location}</div>
            <p class="event-description">{desc}</p>
        </div>
    </div>
"""

    html += """
</body>
</html>
"""

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(html)

    logger.info(f"Generated audit HTML: {output_path}")


def load_existing_events() -> List[dict]:
    """Load existing events from JSON file"""
    try:
        with open(EVENTS_FILE, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f"{EVENTS_FILE} not found, starting with empty list")
        return []
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse {EVENTS_FILE}: {e}")
        return []


def save_events(events: List[dict]):
    """Save events to JSON file"""
    os.makedirs('data', exist_ok=True)
    with open(EVENTS_FILE, 'w') as f:
        json.dump(events, f, indent=2, default=str)
    logger.info(f"Saved {len(events)} events to {EVENTS_FILE}")


def finalize_events(events: List[EventCreate]) -> List[Event]:
    """Convert EventCreate objects to Event objects with IDs"""
    finalized = []
    now = datetime.utcnow()

    for event_create in events:
        event = Event(
            id=str(uuid.uuid4()),
            scraped_at=now,
            last_updated=now,
            **event_create.model_dump()
        )
        finalized.append(event)

    return finalized


def main():
    """Main execution function"""
    logger.info("=" * 60)
    logger.info("User Submitted Events Sync")
    logger.info("=" * 60)

    # Ensure logs directory exists
    os.makedirs('logs', exist_ok=True)

    # Initialize components
    scraper = GoogleSheetsScraper()
    validator = EventValidator()
    deduplicator = EventDeduplicator()

    try:
        # Fetch approved events from Google Sheets
        logger.info("Fetching events from Google Sheets...")
        events = scraper.scrape_events()
        logger.info(f"Fetched {len(events)} new events from Google Sheets")

        if not events:
            logger.info("No new events to process")
            print("No new events to sync.")
            return

        # Validate events
        logger.info("Validating events...")
        validated_events = []
        for event in events:
            event = validator.clean_and_enhance(event)
            is_valid, error = validator.validate_event(event)
            if is_valid:
                validated_events.append(event)
            else:
                logger.warning(f"Rejected event '{event.title}': {error}")

        logger.info(f"Events after validation: {len(validated_events)}")

        if not validated_events:
            logger.info("No events passed validation")
            print("No events passed validation.")
            return

        # Deduplicate among new events
        logger.info("Deduplicating events...")
        deduplicated_events = deduplicator.deduplicate_events(validated_events)
        logger.info(f"Events after deduplication: {len(deduplicated_events)}")

        # Convert to Event objects with IDs
        new_events = finalize_events(deduplicated_events)

        # Load existing events
        existing_events = load_existing_events()
        logger.info(f"Loaded {len(existing_events)} existing events")

        # Keep all existing events (including previous user-submitted ones)
        # and add the new events
        new_events_dict = [event.model_dump(mode='json') for event in new_events]

        # Check for duplicates by title + date to avoid re-adding the same event
        existing_user_events = [
            e for e in existing_events
            if e.get('source_name') == SOURCE_NAME
        ]
        logger.info(f"Found {len(existing_user_events)} existing user-submitted events")

        # Create a set of existing (title, date) pairs to check for duplicates
        existing_keys = set()
        for e in existing_user_events:
            title = e.get('title', '')
            dt = e.get('start_datetime', '')
            if isinstance(dt, str):
                dt = dt[:10]  # Just the date part
            existing_keys.add((title.lower(), dt))

        # Filter out duplicates from new events
        unique_new_events = []
        for e in new_events_dict:
            title = e.get('title', '')
            dt = e.get('start_datetime', '')
            if isinstance(dt, str):
                dt = dt[:10]
            key = (title.lower(), dt)
            if key not in existing_keys:
                unique_new_events.append(e)
            else:
                logger.info(f"Skipping duplicate: {title}")

        logger.info(f"Adding {len(unique_new_events)} new user-submitted events")
        final_events = existing_events + unique_new_events

        # Save updated events
        save_events(final_events)

        # Generate audit HTML with ALL user-submitted events
        all_user_events = [
            e for e in final_events
            if e.get('source_name') == SOURCE_NAME
        ]
        # Convert dicts back to Event objects for audit HTML
        from dateutil import parser as date_parser
        audit_events = []
        for e in all_user_events:
            dt = e.get('start_datetime')
            if isinstance(dt, str):
                dt = date_parser.parse(dt)
            audit_events.append(Event(
                id=e.get('id', str(uuid.uuid4())),
                title=e.get('title', ''),
                description=e.get('description', ''),
                start_datetime=dt,
                source_url=e.get('source_url', ''),
                source_name=e.get('source_name', ''),
                venue_name=e.get('venue_name'),
                street_address=e.get('street_address'),
                city=e.get('city'),
                state=e.get('state'),
                category=e.get('category'),
                cost=e.get('cost'),
                family_friendly=e.get('family_friendly', False),
                image_url=e.get('image_url'),
            ))
        generate_audit_html(audit_events, AUDIT_FILE)

        # Mark events as uploaded in Google Sheets
        row_indices = scraper.get_processed_row_indices()
        if row_indices:
            logger.info(f"Marking {len(row_indices)} events as uploaded in Google Sheets...")
            scraper.mark_as_uploaded(row_indices)

        # Send admin notification about new events
        if new_events:
            send_admin_notification(new_events)

        # Summary
        logger.info("=" * 60)
        logger.info("SYNC COMPLETE")
        logger.info(f"  New user-submitted events: {len(new_events)}")
        logger.info(f"  Total events in database: {len(final_events)}")
        logger.info(f"  Audit file: {AUDIT_FILE}")
        logger.info("=" * 60)

        print(f"\nSynced {len(new_events)} user-submitted events")
        print(f"Total events in database: {len(final_events)}")
        print(f"Audit file: {AUDIT_FILE}")

    except Exception as e:
        logger.error(f"Sync failed: {e}", exc_info=True)
        print(f"Error: {e}")
        raise


if __name__ == "__main__":
    main()
