"""Custom scraper for Boston Swing Central"""
import re
from datetime import datetime
from typing import List
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class BostonSwingCentralScraper(BaseScraper):
    """Custom scraper for Boston Swing Central dance events"""

    def __init__(self):
        super().__init__(
            source_name="Boston Swing Central",
            source_url="https://www.bostonswingcentral.org/",
            use_selenium=False  # Static HTML
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from Boston Swing Central"""
        html = self.fetch_html(self.source_url)
        soup = self.parse_html(html)

        events = []

        # Find date spans (e.g., "Nov, 28" or "Dec, 5")
        date_spans = soup.find_all('span', string=re.compile(r'[A-Z][a-z]{2},\s+\d{1,2}'))

        # Get current year for dates without year
        from datetime import datetime as dt
        current_year = dt.now().year
        current_month = dt.now().month

        for date_span in date_spans[:10]:  # Limit to next 10 events
            try:
                # Extract date from span (e.g., "Nov, 28")
                date_text = self.clean_text(date_span.get_text())

                # Add year - if month is before current month, assume next year
                try:
                    # Parse without year first
                    temp_date = date_parser.parse(f"{date_text} {current_year}", fuzzy=False)
                    # If the parsed month is before current month, use next year
                    if temp_date.month < current_month:
                        event_date = date_parser.parse(f"{date_text} {current_year + 1}", fuzzy=False)
                    else:
                        event_date = temp_date
                except:
                    continue

                # Find the next h3 element (contains event title)
                title_elem = date_span.find_next('h3')
                if not title_elem:
                    continue

                title = self.clean_text(title_elem.get_text())

                # Skip non-event entries (closures, announcements, etc.)
                if any(word in title.lower() for word in ['closed', 'remodeling', 'holiday hours', 'announcement']):
                    continue

                # Get link if available
                event_url = self.source_url
                link = title_elem.find('a', href=True)
                if link and link.get('href'):
                    href = link.get('href')
                    if href.startswith('http'):
                        event_url = href

                # Special case: Boot Camp uses Wufoo registration
                if 'boot camp' in title.lower():
                    event_url = "https://bostonswingcentral.wufoo.com/forms/bsc-swing-boot-camp-2025/"

                # Find the content after the title
                current = title_elem.find_next_sibling()

                description_parts = []
                venue_info = None
                time_str = None
                cost = None

                # Gather content until we hit another date span or h3 or run out
                while current and current.name not in ['h3']:
                    # Also stop if we find another date span
                    if current.find('span', string=re.compile(r'[A-Z][a-z]{2},\s+\d{1,2}')):
                        break
                    text = self.clean_text(current.get_text())

                    # Look for time information - check for noon first
                    if 'noon' in text.lower() and not time_str:
                        time_str = '12:00 PM'
                    # Check for standard time patterns in schedule text
                    elif ('🗓' in text or 'EVENING SCHEDULE' in text.upper() or 'SCHEDULE' in text.upper()) and not time_str:
                        time_match = re.search(r'(\d{1,2}:\d{2}\s*[ap]m)', text, re.IGNORECASE)
                        if time_match:
                            time_str = time_match.group(1)

                    # Look for venue/address information
                    if '26 New St' in text or 'New Street' in text:
                        venue_info = text

                    # Look for admission/cost (🎟 ADMISSION INFORMATION)
                    if '🎟' in text or 'ADMISSION' in text.upper() or '$' in text:
                        cost_match = re.search(r'\$\d+(?:\.\d{2})?', text)
                        if cost_match:
                            cost = cost_match.group()

                    # Collect description parts (but not emoji headers)
                    if text and len(text) > 20 and not text.startswith('🎵') and not text.startswith('🗓') and not text.startswith('🎟'):
                        if text not in description_parts:
                            description_parts.append(text)

                    current = current.find_next_sibling()

                # Build start datetime
                if time_str:
                    try:
                        # Combine date and time
                        datetime_str = f"{event_date.strftime('%Y-%m-%d')} {time_str}"
                        start_datetime = date_parser.parse(datetime_str, fuzzy=False)
                    except:
                        start_datetime = event_date
                else:
                    start_datetime = event_date

                # Build description
                description = ' '.join(description_parts[:3])[:2000] if description_parts else title

                # Venue information
                venue_name = "Boston Swing Central"
                street_address = "26 New St, Suite 3"
                city = "Cambridge"
                state = "MA"
                zip_code = "02138"

                # All Boston Swing Central events are dance/sports
                category = EventCategory.SPORTS

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    source_url=event_url,
                    source_name=self.source_name,
                    venue_name=venue_name,
                    street_address=street_address,
                    city=city,
                    state=state,
                    zip_code=zip_code,
                    category=category,
                    cost=cost
                )
                events.append(event)

            except Exception as e:
                # Log error but continue processing other events
                continue

        return events
