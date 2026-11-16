"""Shared ground for scrapers that read a venue's own machine-readable feed.

Two website platforms cover most of the small venues on the owner's source list:
WordPress running The Events Calendar, which serves a REST API, and Squarespace,
whose events pages answer `?format=json`. Several scrapers in this repo already
read one or the other by hand (rockwell, mount_auburn, portico, ...).
`base_tribe` and `base_squarespace` are shared versions for new sources, and
this module is what the two have in common.

**Where an event is.** A feed names a place per event, or names none:

  - a place in Cambridge or Somerville is used as given;
  - a place anywhere else is skipped — the same line bostonshows.py draws.
    Choirs and orchestras on the list tour (Blue Heron sings in New York and
    Northampton; the Cambridge Symphony plays Jordan Hall);
  - no place at all means the source's HOME, if it has exactly one. A source
    that performs in several places has HOME = None, and an unplaced event from
    it is skipped rather than guessed. A wrong venue sends a reader to the
    wrong door, which is worse than a missing listing (CLAUDE.md, rule 1).

**What is not an event.** Feeds carry placeholders ("No Event Tonight") and
closures ("SATELLITE: Closed for a Private Event"). Those are matched on the
title only — a description saying the room is "available for private events"
must not drop a public show.

**Category** comes from the title alone, falling back to the source's
CATEGORY. Descriptions are not read for it: venue boilerplate ("live music
every week!") would classify every event the same way.
"""
import html
import logging
import re
from dataclasses import dataclass
from typing import Optional, Pattern, Tuple

from bs4 import BeautifulSoup

from src.models.event import EventCategory
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

# The calendar's area. bostonshows.py filters to the same two cities.
LOCAL_CITIES = ("Cambridge", "Somerville")

# Timezone settings under which a feed's wall clock is Eastern. Anything else
# on a Massachusetts venue's site is a misconfiguration; see base_tribe.
EASTERN_ZONES = {"America/New_York", "US/Eastern", "EST5EDT", "America/Detroit", "Canada/Eastern",
                 "America/Toronto"}

# Titles that announce the absence of an event, or one the public cannot attend.
# "Closed" only as a notice ("Closed for Thanksgiving", "SATELLITE: Closed ...")
# so that a show called "Behind Closed Doors" survives.
NOT_AN_EVENT = re.compile(
    r"\bno events? tonight\b"
    r"|(^|:\s*)closed\b|\bclosed for\b"
    r"|\bprivate (event|party|function|rental)\b"
    r"|\b(invite|invitation|members)[- ]only\b",
    re.I,
)

ONLINE = re.compile(r"^\W*(virtual|online|zoom|livestream(ed)?)\b", re.I)

# First match wins, so the specific forms come before the general ones.
TITLE_CATEGORIES: Tuple[Tuple[EventCategory, Pattern], ...] = (
    (EventCategory.THEATER, re.compile(
        r"\b(comedy|comedian|improv|stand-?up|theat(er|re)|musical|play reading|drag|burlesque|cabaret)\b", re.I)),
    (EventCategory.FILM, re.compile(r"\b(film|films|screening|movie|cinema)\b", re.I)),
    (EventCategory.MUSIC, re.compile(
        r"\b(concert|live music|band|jazz|bluegrass|folk|songwriters?|dj|choir|chorus|orchestra|"
        r"symphony|pops|recital|singers?|open mic|karaoke)\b", re.I)),
    (EventCategory.LECTURES, re.compile(
        r"\b(lecture|panel|reading|author|poetry|book (talk|launch|signing)|curator tour|"
        r"discussion)\b|(?<!small )\btalk\b", re.I)),
    (EventCategory.ARTS_CULTURE, re.compile(
        r"\b(exhibit|exhibition|gallery|opening reception|reception|artists?|museum|collage|"
        r"drawing|painting|zine|craft|printmaking)\b", re.I)),
    (EventCategory.SPORTS, re.compile(r"\b(yoga|fitness|workout|bootcamp|run club|5k)\b", re.I)),
    (EventCategory.FOOD_DRINK, re.compile(r"\b(tasting|beer garden|brunch|dinner|food|market)\b", re.I)),
)

ZIP = re.compile(r"\b(\d{5})\b")


@dataclass(frozen=True)
class Place:
    venue: str
    street: Optional[str]
    city: Optional[str]
    zip_code: Optional[str]


def city_of(text: Optional[str]) -> Optional[str]:
    """'Cambridge, MA, 02139' / 'Somerville' / 'Boston, MA 02115' -> the city name."""
    if not text:
        return None
    head = text.split(",")[0].strip()
    head = ZIP.sub("", head).strip()
    return head.title() or None


class BaseFeedScraper(BaseScraper):
    """A venue feed: one home (or none), one default category."""

    # The venue an unplaced event is at. None for sources that perform in
    # several places — their unplaced events are skipped, not guessed.
    HOME: Optional[Place] = None

    # Category for titles that name no kind of event ("Legends of Mary").
    CATEGORY: EventCategory = EventCategory.COMMUNITY

    def __init__(self, source_name: str, source_url: str):
        super().__init__(source_name=source_name, source_url=source_url, use_selenium=False)

    # -- text ------------------------------------------------------------- #

    def text(self, value) -> str:
        """Plain text from a feed's HTML-and-entities field."""
        if not value:
            return ""
        if "<" not in str(value):
            return self.clean_text(html.unescape(str(value)))
        soup = BeautifulSoup(str(value), "html.parser")
        for tag in soup(["script", "style"]):
            tag.decompose()
        return self.clean_text(html.unescape(soup.get_text(" ")))

    # -- what is and is not an event ------------------------------------- #

    def is_event(self, title: str) -> bool:
        if len(title) < 3:
            return False
        if NOT_AN_EVENT.search(title):
            logger.info(f"{self.source_name}: skipping non-public listing '{title}'")
            return False
        return True

    def categorize(self, title: str) -> EventCategory:
        for category, pattern in TITLE_CATEGORIES:
            if pattern.search(title):
                return category
        return self.CATEGORY

    # -- where ----------------------------------------------------------- #

    def place(self, title: str, venue: Optional[str], street: Optional[str],
              city: Optional[str], zip_code: Optional[str] = None) -> Optional[Place]:
        """Resolve a feed's venue fields to a local Place, or None to skip.

        See the module docstring for the rules and why each exists.
        """
        if ONLINE.search(title):
            return Place("Online", None, self.HOME.city if self.HOME else None, None)

        venue = self.text(venue)
        street = self.text(street) or None
        city_text = self.text(city)
        zip_match = ZIP.search(zip_code or "") or ZIP.search(city_text)
        zip_code = zip_match.group(1) if zip_match else None

        if not (venue or street or city_text):
            if self.HOME is None:
                logger.info(f"{self.source_name}: skipping '{title}' - no venue given and no single home venue")
            return self.HOME

        # The source's own room under its own name: trust our address, not the
        # feed's (Gallery 263's own venue record spells its city "Cambride").
        if self.HOME and venue.lower() == self.HOME.venue.lower():
            return self.HOME

        name = city_of(city_text)
        if name is None:
            if self.HOME is None:
                logger.info(f"{self.source_name}: skipping '{title}' - venue '{venue}' has no city")
                return None
            name = self.HOME.city
        if name not in LOCAL_CITIES:
            logger.info(f"{self.source_name}: skipping '{title}' - in {name}, outside the calendar's area")
            return None

        label = venue or (self.HOME.venue if self.HOME else street) or name
        return Place(label[:150], street, name, zip_code)

    def fallback_description(self, title: str, where: Place) -> str:
        if where.venue == "Online":
            return f"{title}, online, from {self.source_name}."
        return f"{title} at {where.venue}, {where.city}."
