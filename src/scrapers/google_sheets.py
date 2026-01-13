"""Scraper for user-submitted events from Google Sheets"""
import json
import logging
import os
import re
from datetime import datetime
from typing import List, Optional, Tuple

from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory

logger = logging.getLogger(__name__)

# Category mapping from form values to EventCategory
CATEGORY_MAP = {
    'music': EventCategory.MUSIC,
    'arts and culture': EventCategory.ARTS_CULTURE,
    'arts & culture': EventCategory.ARTS_CULTURE,
    'food and drink': EventCategory.FOOD_DRINK,
    'food & drink': EventCategory.FOOD_DRINK,
    'theater': EventCategory.THEATER,
    'theatre': EventCategory.THEATER,
    'lectures': EventCategory.LECTURES,
    'sports': EventCategory.SPORTS,
    'community': EventCategory.COMMUNITY,
    'other': EventCategory.OTHER,
}

# Default sheet ID from the user's Google Sheet URL
DEFAULT_SHEET_ID = "1LZEWBBuj1SKCrdvzcZ1w37EY6f7ZZ_dpk60XuHllK_Q"


class GoogleSheetsScraper(BaseScraper):
    """
    Scraper for user-submitted events from Google Sheets.

    Requires environment variables:
    - GOOGLE_SERVICE_ACCOUNT_JSON: Service account credentials JSON string
    - GOOGLE_SHEET_ID: Spreadsheet ID (optional, defaults to known sheet)
    """

    def __init__(self, sheet_id: str = None, credentials_json: str = None):
        super().__init__(
            source_name="User Submitted",
            source_url=f"https://docs.google.com/spreadsheets/d/{sheet_id or DEFAULT_SHEET_ID}",
            use_selenium=False
        )
        self.sheet_id = sheet_id or os.environ.get('GOOGLE_SHEET_ID', DEFAULT_SHEET_ID)
        self.credentials_json = credentials_json
        self._service = None
        self._sheet_name = None  # Cached sheet name
        self._processed_rows = []  # Track rows for marking as uploaded

    def _get_sheets_service(self):
        """Initialize Google Sheets API service"""
        if self._service:
            return self._service

        # Import Google API libraries (only when needed)
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        # Get credentials from environment or constructor
        creds_json = self.credentials_json or os.environ.get('GOOGLE_SERVICE_ACCOUNT_JSON')

        if not creds_json:
            raise ValueError(
                "GOOGLE_SERVICE_ACCOUNT_JSON environment variable not set. "
                "Please configure service account credentials."
            )

        # Parse credentials JSON
        try:
            creds_dict = json.loads(creds_json)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in GOOGLE_SERVICE_ACCOUNT_JSON: {e}")

        credentials = service_account.Credentials.from_service_account_info(
            creds_dict,
            scopes=['https://www.googleapis.com/auth/spreadsheets']
        )

        self._service = build('sheets', 'v4', credentials=credentials)
        logger.info("Google Sheets API service initialized")
        return self._service

    def _get_first_sheet_name(self) -> str:
        """Get the name of the first sheet in the spreadsheet (cached)"""
        if self._sheet_name:
            return self._sheet_name

        service = self._get_sheets_service()

        try:
            spreadsheet = service.spreadsheets().get(
                spreadsheetId=self.sheet_id,
                fields='sheets.properties.title'
            ).execute()

            sheets = spreadsheet.get('sheets', [])
            if sheets:
                self._sheet_name = sheets[0]['properties']['title']
                logger.info(f"Using sheet: {self._sheet_name}")
                return self._sheet_name
            else:
                raise ValueError("No sheets found in spreadsheet")
        except Exception as e:
            logger.error(f"Failed to get sheet name: {e}")
            raise

    def fetch_approved_events(self) -> List[dict]:
        """Fetch all approved events that haven't been uploaded yet"""
        service = self._get_sheets_service()
        sheet_name = self._get_first_sheet_name()

        # Read all data from the sheet
        # Columns: A=Timestamp, B=Event Name, C=Date, D=Time, E=Address,
        #          F=Description, G=Category, H=Cost, I=Family Friendly,
        #          J=Event URL, K=Image URL, L=Contact Email, M=Approved, N=Uploaded
        range_name = f"'{sheet_name}'!A2:N"

        try:
            result = service.spreadsheets().values().get(
                spreadsheetId=self.sheet_id,
                range=range_name
            ).execute()
        except Exception as e:
            logger.error(f"Failed to fetch data from Google Sheets: {e}")
            raise

        rows = result.get('values', [])
        approved_events = []

        logger.info(f"Found {len(rows)} total rows in sheet")

        for row_idx, row in enumerate(rows):
            # Pad row with empty strings to ensure we have all columns
            while len(row) < 14:
                row.append('')

            # Extract values (0-indexed)
            timestamp = row[0]
            event_name = row[1]
            date = row[2]
            time = row[3]
            address = row[4]
            description = row[5]
            category = row[6]
            cost = row[7]
            family_friendly = row[8]
            event_url = row[9]
            image_url = row[10]
            contact_email = row[11]
            approved = row[12]
            uploaded = row[13]

            # Only process approved and not-yet-uploaded events
            is_approved = approved.strip().lower() == 'yes'
            is_uploaded = uploaded.strip().lower().startswith('yes')

            if is_approved and not is_uploaded:
                approved_events.append({
                    'row_index': row_idx + 2,  # 1-indexed, +1 for header row
                    'timestamp': timestamp,
                    'event_name': event_name,
                    'date': date,
                    'time': time,
                    'address': address,
                    'description': description,
                    'category': category,
                    'cost': cost,
                    'family_friendly': family_friendly,
                    'event_url': event_url,
                    'image_url': image_url,
                    'contact_email': contact_email,
                })
                logger.debug(f"Found approved event: {event_name}")
            elif is_approved and is_uploaded:
                logger.debug(f"Skipping already uploaded event: {event_name}")

        logger.info(f"Found {len(approved_events)} approved events pending upload")
        return approved_events

    def mark_as_uploaded(self, row_indices: List[int] = None):
        """Mark events as uploaded in the sheet"""
        indices_to_mark = row_indices or self._processed_rows

        if not indices_to_mark:
            logger.info("No rows to mark as uploaded")
            return

        service = self._get_sheets_service()
        sheet_name = self._get_first_sheet_name()
        now = datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')

        for row_idx in indices_to_mark:
            try:
                # Update Uploaded column (N)
                range_name = f"'{sheet_name}'!N{row_idx}"
                body = {'values': [[f'Yes - {now}']]}

                service.spreadsheets().values().update(
                    spreadsheetId=self.sheet_id,
                    range=range_name,
                    valueInputOption='RAW',
                    body=body
                ).execute()

                logger.debug(f"Marked row {row_idx} as uploaded")
            except Exception as e:
                logger.error(f"Failed to mark row {row_idx} as uploaded: {e}")

        logger.info(f"Marked {len(indices_to_mark)} events as uploaded")

    def parse_datetime(self, date_str: str, time_str: str) -> Optional[datetime]:
        """Parse date and time strings into datetime"""
        if not date_str:
            return None

        try:
            # Combine date and time
            combined = f"{date_str} {time_str}".strip()
            return date_parser.parse(combined, fuzzy=True)
        except Exception as e:
            logger.warning(f"Failed to parse datetime '{date_str} {time_str}': {e}")
            return None

    def parse_category(self, category_str: str) -> Optional[EventCategory]:
        """Parse category string to EventCategory enum"""
        if not category_str:
            return EventCategory.OTHER

        category_lower = category_str.lower().strip()
        return CATEGORY_MAP.get(category_lower, EventCategory.OTHER)

    def parse_family_friendly(self, value: str) -> bool:
        """Parse family friendly value to boolean"""
        if not value:
            return False
        return value.lower().strip() in ('yes', 'true', '1', 'y')

    def parse_address(self, address: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Parse address field to extract venue name and street address.

        Examples:
        - "Brattle Theatre, 40 Brattle Street, Cambridge, MA 02138"
          → venue="Brattle Theatre", street="40 Brattle Street, Cambridge, MA 02138"
        - "101 Rogers St Cambridge, MA 02142"
          → venue=None, street="101 Rogers St Cambridge, MA 02142"
        - "Fletcher-Maynard Academy (225 Windsor St, Cambridge, MA 02139)"
          → venue="Fletcher-Maynard Academy", street="225 Windsor St, Cambridge, MA 02139"
        - "Central Square (various locations)"
          → venue="Central Square", street="various locations"

        Returns:
            Tuple of (venue_name, street_address)
        """
        if not address or not address.strip():
            return None, None

        address = address.strip()

        # Pattern 1: Venue name in parentheses at end - extract what's in parens
        # e.g., "Fletcher-Maynard Academy (225 Windsor St, Cambridge, MA 02139)"
        paren_match = re.match(r'^(.+?)\s*\(([^)]+)\)\s*$', address)
        if paren_match:
            venue = paren_match.group(1).strip()
            street = paren_match.group(2).strip()
            return venue, street

        # Pattern 2: Comma-separated with venue first
        # e.g., "Brattle Theatre, 40 Brattle Street, Cambridge, MA 02138"
        parts = [p.strip() for p in address.split(',')]

        if len(parts) >= 2:
            first_part = parts[0]
            # Check if first part looks like a venue (doesn't start with a number)
            if first_part and not re.match(r'^\d', first_part):
                venue = first_part
                street = ', '.join(parts[1:])
                return venue, street

        # Pattern 3: Just a street address starting with a number
        # e.g., "101 Rogers St Cambridge, MA 02142"
        if re.match(r'^\d', address):
            return None, address

        # Pattern 4: Just a venue name with no clear street address
        # e.g., "Central Square" or "Cambridge Common"
        return address, None

    def scrape_events(self) -> List[EventCreate]:
        """Scrape approved events from Google Sheets"""
        events = []
        self._processed_rows = []

        try:
            approved_rows = self.fetch_approved_events()
        except Exception as e:
            logger.error(f"Failed to fetch events from Google Sheets: {e}")
            return []

        for row_data in approved_rows:
            try:
                # Parse datetime
                start_datetime = self.parse_datetime(
                    row_data['date'],
                    row_data['time']
                )

                if not start_datetime:
                    logger.warning(
                        f"Skipping event '{row_data['event_name']}': "
                        f"invalid datetime (date={row_data['date']}, time={row_data['time']})"
                    )
                    continue

                # Get title and description
                title = row_data['event_name'].strip()[:200]
                description = row_data['description'].strip()[:2000]

                if not title:
                    logger.warning("Skipping event with empty title")
                    continue

                # Use title as description if description is empty
                if not description:
                    description = title

                # Get event URL - use as source_url if provided, otherwise use sheet URL
                event_url = row_data['event_url'].strip()
                source_url = event_url if event_url else self.source_url

                # Parse contact email (validate format)
                contact_email = row_data['contact_email'].strip() or None
                if contact_email and '@' not in contact_email:
                    contact_email = None  # Invalid email format

                # Parse address to extract venue name and street address
                venue_name, street_address = self.parse_address(row_data['address'])

                # Build event
                event = EventCreate(
                    title=title,
                    description=description,
                    start_datetime=start_datetime,
                    source_url=source_url,
                    source_name=self.source_name,
                    venue_name=venue_name,
                    street_address=street_address,
                    city="Cambridge",
                    state="MA",
                    category=self.parse_category(row_data['category']),
                    cost=row_data['cost'].strip() or None,
                    family_friendly=self.parse_family_friendly(row_data['family_friendly']),
                    image_url=row_data['image_url'].strip() or None,
                    website_url=event_url or None,
                    contact_email=contact_email,
                )

                events.append(event)
                self._processed_rows.append(row_data['row_index'])
                logger.info(f"Parsed event: {title}")

            except Exception as e:
                logger.error(
                    f"Failed to parse event '{row_data.get('event_name', 'unknown')}': {e}"
                )
                continue

        logger.info(f"Successfully parsed {len(events)} events from Google Sheets")
        return events

    def get_processed_row_indices(self) -> List[int]:
        """Get list of row indices that were successfully processed"""
        return self._processed_rows.copy()
