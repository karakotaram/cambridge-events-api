"""Dave Bryant's Third Thursdays — the GigPress feed on dbryantmusic.com.

The Third Thursdays page describes the series in prose ("a new monthly ...
series", "the third Thursday each month") and prints the next date in a
paragraph. Neither is read for dates: a recurrence described in prose is not
a list of dates (CLAUDE.md, rule 1). The site's gig list is GigPress, whose RSS
feed (`?feed=gigpress`) carries each upcoming show with labelled fields:

    Date: Thursday, November 19th 2026    Time: 8:00pm
    City: Cambridge, MA                   Venue: Harvard-Epworth Church

**Times.** The feed's `pubDate` ("Thu, 19 Nov 2026 20:00:00 +0000") and its
Google Calendar link stamp the local 8 PM as UTC, which would put the show at
3 PM. Only the printed Date and Time fields are read, as Eastern wall clock,
and the printed weekday must agree with the date.

The feed lists all of Dave Bryant's gigs; base_feed keeps the ones in
Cambridge and Somerville.
"""
import logging
import re
from typing import Dict, List, Optional
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper
from src.scrapers.base_printed_date import at, clock, dated, month_number, weekday_number

logger = logging.getLogger(__name__)

SITE = "https://dbryantmusic.com"
FEED_URL = f"{SITE}/?feed=gigpress"

DATE = re.compile(r"^\s*([a-z]+),?\s+([a-z]+)\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\s*$", re.I)
ZIP = re.compile(r"\b(\d{5})\b")


class DaveBryantScraper(BaseFeedScraper):
    HOME = None
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name="Dave Bryant Third Thursdays", source_url=f"{SITE}/third-thursdays/")

    def fetch_feed(self) -> str:
        response = requests.get(FEED_URL, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        root = ElementTree.fromstring(self.fetch_feed().encode("utf-8"))
        channel = root.find("channel")
        if channel is None:
            raise ValueError(f"{self.source_name}: {FEED_URL} is not an RSS feed")
        events = []
        for item in channel.findall("item"):
            event = self.parse_item(item)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    @staticmethod
    def fields(description_html: str) -> Dict[str, str]:
        """GigPress's '<li><strong>Label:</strong> value</li>' list as {label: value}."""
        soup = BeautifulSoup(description_html or "", "html.parser")
        found = {}
        for li in soup.find_all("li"):
            label = li.find("strong")
            if not label:
                continue
            key = label.get_text(strip=True).rstrip(":").lower()
            label.extract()
            found[key] = " ".join(li.get_text(" ").split())
            link = li.find("a", class_="gigpress-address")
            if link and link.get("href"):
                found["maps"] = link["href"]
        return found

    def parse_item(self, item) -> Optional[EventCreate]:
        info = self.fields(item.findtext("description") or "")
        artist = info.get("artist") or "Dave Bryant"
        tour = info.get("tour")
        venue = info.get("venue")
        title = f"{tour}: {artist}" if tour else f"{artist} at {venue or 'TBA'}"

        match = DATE.match(info.get("date", ""))
        day = None
        if match:
            weekday, month, dom, year = match.groups()
            month_no = month_number(month)
            day = dated(int(year), month_no, int(dom), weekday_number(weekday)) if month_no else None
        if day is None:
            logger.warning(f"Skipping '{title}' - no parseable date in {info.get('date')!r} ({FEED_URL})")
            return None
        time = clock(info.get("time", ""))

        city = info.get("city")
        zip_match = ZIP.search(" ".join(parse_qs(urlparse(info.get("maps", "")).query).get("q", [])))
        where = self.place(title, venue, info.get("address"), city, zip_match.group(1) if zip_match else None)
        if where is None:
            return None

        # The series page for the series; GigPress's gig list for anything else.
        url = self.source_url if (tour or "").lower() == "third thursdays" else (item.findtext("link") or self.source_url)
        notes = info.get("notes", "")
        description = notes if len(notes) >= 20 else self.fallback_description(title, where)
        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=at(day, time),
            all_day=time is None,
            venue_name=where.venue[:150],
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.CATEGORY,
            cost=info.get("admission") or None,
            age_restrictions=info.get("age restrictions") or None,
            source_name=self.source_name,
            source_url=url,
        )
