"""Scraper for Harvard Graduate School of Design (GSD) public programs"""
import logging
import re
from html import unescape
from typing import List, Optional

import requests
from bs4 import BeautifulSoup
from dateutil import parser as dateparse

from src.scrapers.base_scraper import BaseScraper
from src.models.event import EASTERN_TZ, EventCategory, EventCreate, to_eastern_naive

logger = logging.getLogger(__name__)

API_URL = "https://www.gsd.harvard.edu/wp-json/gsd/v1/events"

# Events the GSD has told us are not open to the public. These are in Cambridge
# and would otherwise look like ordinary listings, so they can only be excluded
# by name — nothing in the feed marks them private.
#
# Requested by Daniela Morin (GSD Public Programs), 2026-09-02:
#   comeback    GSD Comeback: Alumni & Friends Celebration, Sep 25-26
#   _positions  _Positions, Sep 10 (note: the *named* _positions lectures, e.g.
#               "_positions: Kengo Kuma", are separate events with their own
#               slugs and are public — do not exclude by title prefix)
NOT_PUBLIC_SLUGS = {
    "comeback",
    "_positions",
}

# Kept for the earlier off-site exclusion; superseded by _is_local() below,
# which reads the address instead of maintaining a list.
EXCLUDED_SLUGS = NOT_PUBLIC_SLUGS | {
    "apa-detroit-gsd-alumni-reception-2026",
}

# The GSD runs alumni events all over the world and publishes them on the same
# page. An off-site listing spells out a full address ("SWA Group, 811 W 7th
# Street, Floor 8, Los Angeles, CA, 90017"); a Cambridge one names only a room
# ("Piper Auditorium"). Anything naming a place outside Massachusetts is not
# ours to list — the scraper previously stamped city="Cambridge" on all of it,
# putting a Los Angeles reception and a Toronto park tour on a Cambridge
# calendar.
NON_LOCAL = re.compile(
    r",\s*(?:A[KLRZ]|C[AOT]|D[CE]|FL|GA|HI|I[ADLN]|K[SY]|LA|M[DEINOST]|"
    r"N[CDEHJMVY]|OH|OK|OR|P[AR]|RI|S[CD]|T[NX]|UT|V[AT]|W[AIVY])\b"
    r"|\b(?:canada|united kingdom|london|toronto|vancouver|montreal|"
    r"mexico|china|japan|india|germany|france|italy|spain|netherlands)\b",
    re.I,
)

# Occurrence types that are never a public Cambridge listing. Every "Alumni
# Event" seen in the feed is either a reception elsewhere (Los Angeles,
# Toronto, Miami, Seattle) or alumni-only on campus (GSD Comeback).
OFF_SITE_TYPES = {"alumni event"}

PRIVATE_LOCATION = re.compile(r"\bprivate (?:residence|home|location)\b", re.I)


class HarvardGSDScraper(BaseScraper):
    """Scrape public events from Harvard GSD via their WordPress REST API"""

    def __init__(self):
        super().__init__(
            source_name="Harvard GSD",
            source_url="https://www.gsd.harvard.edu/public-programs/",
            use_selenium=False,
        )

    def scrape_events(self) -> List[EventCreate]:
        response = requests.get(
            API_URL,
            params={"per_page": "100"},
            headers=self.get_browser_headers(),
            timeout=15,
        )
        response.raise_for_status()
        return self.parse_items(response.json())

    def parse_items(self, data: List[dict]) -> List[EventCreate]:
        events = []
        for item in data:
            try:
                events.extend(self._parse_event(item))
            except Exception as e:
                logger.warning(f"Failed to parse GSD event: {e}")

        logger.info(f"Found {len(events)} Harvard GSD events")
        return events

    def _extract_about(self, desc_html: str) -> str:
        """Extract the 'About this Event' text, or fall back to body paragraphs."""
        if not desc_html:
            return ""
        soup = BeautifulSoup(desc_html, "html.parser")

        # Try to find "About this Event" heading
        heading = soup.find(
            lambda tag: tag.name in ("h2", "h3")
            and "about this event" in tag.get_text().lower()
        )
        if heading:
            parts = []
            for sib in heading.find_next_siblings():
                if sib.name and sib.name.startswith("h"):
                    break
                text = sib.get_text(strip=True)
                if text:
                    parts.append(text)
            if parts:
                return " ".join(parts)[:2000]

        # Fallback: collect <p> text after removing the hero banner
        for div in soup.find_all("div", class_=re.compile(r"hero-banner")):
            div.decompose()
        paragraphs = []
        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if len(text) > 20:
                paragraphs.append(text)
        if paragraphs:
            return " ".join(paragraphs)[:2000]
        return ""

    @staticmethod
    def _is_local(location: str) -> bool:
        """Is this address in the calendar's coverage area?

        Massachusetts addresses and bare room names both count; anything naming
        another state or country does not.
        """
        if not location:
            return True                     # a bare room name is on campus
        if re.search(r",\s*MA\b|\bmassachusetts\b", location, re.I):
            return True
        return not NON_LOCAL.search(location)

    @staticmethod
    def _off_eastern_clock(time_start: str) -> bool:
        """True if the feed states this start in another timezone.

        The GSD writes each occurrence with the venue's own UTC offset, so an
        event in Seattle carries -07:00. A Cambridge event is always -04:00 or
        -05:00, whichever Eastern time is in force on that date.
        """
        try:
            stamped = dateparse.parse(time_start)
        except (ValueError, TypeError, OverflowError):
            return False
        if stamped.tzinfo is None:
            return False
        eastern = EASTERN_TZ.localize(stamped.replace(tzinfo=None)).utcoffset()
        return stamped.utcoffset() != eastern

    def off_site_reason(self, occ: dict) -> Optional[str]:
        """Why this occurrence is not a Cambridge event, or None if it is.

        Any one signal is enough. The address check alone missed two alumni
        events on 2026-10-05: "Private Residence" (Miami) names no place, and
        "Surf Incubator, …, Seattle" names a city but no state.
        """
        location = " ".join((occ.get("location") or "").split())
        kind = (occ.get("type") or "").strip()
        if kind.lower() in OFF_SITE_TYPES:
            return f"{kind} at {location or 'no stated location'}"
        if PRIVATE_LOCATION.search(location):
            return f"private location ({location})"
        if not self._is_local(location):
            return f"off-site ({location})"
        if self._off_eastern_clock(occ.get("time_start") or ""):
            return f"scheduled in another timezone ({occ.get('time_start')}, {location})"
        return None

    def _occurrence_times(self, occ: dict):
        """(start, end, all_day) for one occurrence, or None if undatable."""
        all_day = bool(occ.get("is_all_day"))
        try:
            if all_day:
                day = dateparse.parse(occ.get("date") or occ.get("time_start") or "")
                start = to_eastern_naive(day).replace(hour=0, minute=0, second=0, microsecond=0)
                return start, None, True
            start = to_eastern_naive(dateparse.parse(occ.get("time_start") or ""))
        except (ValueError, TypeError, OverflowError):
            return None
        end = None
        if occ.get("time_end"):
            try:
                end = to_eastern_naive(dateparse.parse(occ["time_end"]))
            except (ValueError, TypeError, OverflowError):
                end = None
        return start, end, False

    def _parse_event(self, item: dict) -> List[EventCreate]:
        """Every public, on-campus occurrence of one GSD event.

        A multi-day event lists one occurrence per day — the Nov 12 lecture and
        the Nov 13 symposium of "Infrastructure in a Time of Flux" — and only
        the first used to be read.
        """
        slug = item.get("slug", "")
        if slug in EXCLUDED_SLUGS:
            logger.info(f"Skipping '{slug}' - not open to the public")
            return []

        # Extract title, stripping HTML tags and decoding entities
        raw_title = item.get("title", {}).get("rendered", "")
        title = unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
        if not title:
            return []

        # Prefix with series name if available
        series = item.get("series", "")
        if series and series.lower() not in title.lower():
            title = f"{series}: {title}"

        events = []
        for occ in item.get("occurrences") or []:
            reason = self.off_site_reason(occ)
            if reason:
                logger.info(f"Skipping '{slug}' - {reason}")
                continue
            times = self._occurrence_times(occ)
            if times is None:
                logger.warning(f"Skipping an occurrence of '{slug}' - no parseable start ({occ.get('time_start')!r})")
                continue
            event = self._build(item, occ, title, *times)
            if event:
                events.append(event)
        return events

    def _build(self, item: dict, occ: dict, title: str, start_datetime, end_datetime, all_day):
        location = " ".join((occ.get("location") or "").split())
        venue = f"Harvard GSD - {location}" if location else "Harvard GSD"

        # Build description from "About this Event" section
        description = self._extract_about(item.get("description", {}).get("rendered", ""))
        if not description:
            description = title

        # Event URL
        event_url = item.get("link", self.source_url)

        # Image
        image_url = None
        card_html = item.get("card_html", "")
        if card_html:
            img_match = re.search(r'<img[^>]+src="([^"]+)"', card_html)
            if img_match:
                image_url = img_match.group(1)

        # Categorize based on event type
        event_type = occ.get("type") or ""
        category = EventCategory.LECTURES
        type_lower = event_type.lower()
        if any(w in type_lower for w in ("exhibition", "gallery", "art")):
            category = EventCategory.ARTS_CULTURE
        elif "performance" in type_lower:
            category = EventCategory.ARTS_CULTURE
        elif "community" in type_lower:
            category = EventCategory.COMMUNITY

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start_datetime,
            end_datetime=end_datetime,
            all_day=all_day,
            source_url=event_url,
            source_name=self.source_name,
            venue_name=venue[:150],
            city="Cambridge",
            state="MA",
            category=category,
            family_friendly=False,
            image_url=image_url,
        )
