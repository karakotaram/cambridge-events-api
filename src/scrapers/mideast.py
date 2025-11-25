"""Custom scraper for The Middle East Restaurant & Nightclub"""
import re
from datetime import datetime
from typing import List
from dateutil import parser as date_parser

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EventCreate, EventCategory


class MideastClubScraper(BaseScraper):
    """Custom scraper for Middle East Club events"""

    def __init__(self):
        super().__init__(
            source_name="The Middle East",
            source_url="https://mideastclub.com/",
            use_selenium=True  # JavaScript-rendered content
        )

    def scrape_events(self) -> List[EventCreate]:
        """Scrape events from The Middle East"""
        html = self.fetch_html(self.source_url)

        # Add extra wait for JavaScript to load
        if self.driver:
            import time
            time.sleep(5)  # Give JS time to render events
            html = self.driver.page_source

        soup = self.parse_html(html)

        events = []

        # Find the event list container
        event_list = soup.find('div', class_='tw-plugin-upcoming-event-list')

        if not event_list:
            return events

        # Find all event sections
        event_sections = event_list.find_all('div', class_='tw-section')

        # Limit to reasonable number
        event_sections = event_sections[:30]

        for element in event_sections:
            try:
                # Extract title - look for link with aria-label containing "Event Name"
                title = None
                links = element.find_all('a')
                for link in links:
                    aria_label = link.get('aria-label', '')
                    if 'Event Name' in aria_label:
                        title = self.clean_text(link.get_text())
                        break

                if not title or len(title) < 3:
                    continue

                # Skip private events
                full_text = self.clean_text(element.get_text()).lower()
                private_keywords = ['private party', 'private event', 'closed to public',
                                   'invite only', 'members only', 'by invitation']
                if any(keyword in full_text for keyword in private_keywords):
                    continue

                # Extract date
                date_elem = element.find('span', class_='tw-event-date')
                dow_elem = element.find('span', class_='tw-day-of-week')

                if not date_elem:
                    continue

                # Build date string (format: "Tue 11.18" -> "Tuesday November 18, 2025")
                dow = dow_elem.get_text(strip=True) if dow_elem else ""
                date_text = date_elem.get_text(strip=True)  # e.g., "11.18"

                # Convert format from "11.18" to "November 18"
                month_day_match = re.match(r'(\d{1,2})\.(\d{1,2})', date_text)
                if month_day_match:
                    month = int(month_day_match.group(1))
                    day = int(month_day_match.group(2))

                    # Assume current year or next year
                    current_year = datetime.now().year
                    current_month = datetime.now().month

                    # If the month is before current month, assume next year
                    if month < current_month:
                        year = current_year + 1
                    else:
                        year = current_year

                    date_str = f"{dow} {month}/{day}/{year}"
                else:
                    continue

                # Extract time if available
                # Time is in <span class="tw-event-time"> inside <div class="tw-date-time">
                time_elem = element.find('span', class_='tw-event-time')
                if time_elem:
                    time_text = self.clean_text(time_elem.get_text())
                    # Remove "Show:" prefix if present
                    time_text = time_text.replace('Show:', '').strip()
                    date_str += f" {time_text}"

                # Parse the date
                try:
                    start_datetime = date_parser.parse(date_str, fuzzy=True)
                except:
                    # If parsing fails, skip this event
                    continue

                # Extract venue/location
                venue_name = "The Middle East"
                location_elem = element.find('div', class_='tw-event-location')
                if location_elem:
                    location_text = self.clean_text(location_elem.get_text())
                    # Location might specify "Middle East Upstairs", "Middle East Corner", etc.
                    if location_text and location_text != venue_name:
                        venue_name = location_text

                # Get event URL (TicketWeb link) - use the link from earlier
                event_url = self.source_url
                for link in links:
                    href = link.get('href', '')
                    if 'ticketweb' in href:
                        event_url = href
                        break

                # Extract price if available
                cost = None
                price_elem = element.find('div', class_='tw-event-price')
                if price_elem:
                    price_text = self.clean_text(price_elem.get_text())
                    cost_match = re.search(r'\$\d+(?:\.\d{2})?', price_text)
                    if cost_match:
                        cost = cost_match.group()

                # Create description (since we can't fetch from TicketWeb)
                description = f"{title} at {venue_name} in Cambridge, MA"
                if location_elem:
                    description += f". {self.clean_text(location_elem.get_text())}"

                # The Middle East is located in Central Square, Cambridge
                street_address = "472-480 Massachusetts Ave"
                city = "Cambridge"
                state = "MA"
                zip_code = "02139"

                # All Middle East events are music events
                category = EventCategory.MUSIC

                event = EventCreate(
                    title=title[:200],
                    description=description[:2000],
                    start_datetime=start_datetime,
                    source_url=event_url,
                    source_name=self.source_name,
                    venue_name=venue_name[:200],
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
