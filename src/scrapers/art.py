"""Scraper for the American Repertory Theater (A.R.T.).

/shows-events/ links each current show; each show page lists every performance
as a `div.c-booking-instance`, server-rendered, so plain requests read it.

A performance prints its weekday and date ("Tuesday" / "May 18") and a time,
never a year. The year is the one whose calendar puts that date on that
weekday; a performance whose weekday matches no nearby year is skipped. Taking
the year from the clock instead published a whole run on 2026 dates that
belonged in 2027.

Shows play in different buildings, named in the show's masthead
(`p.c-masthead__venue`): Loeb Drama Center and Farkas Hall in Harvard Square,
and the Goel Center in Allston, which is in Boston.
"""
import logging
import re
from datetime import date, datetime
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

BASE_URL = "https://americanrepertorytheater.org"

WEEKDAYS = {name: i for i, name in enumerate(
    ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"))}
MONTHS = {name: i for i, name in enumerate(
    ("january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"), start=1)}

# Addresses as A.R.T. publishes them: Loeb in its site footer, Farkas Hall in
# 1972's venue card, the Goel Center on /a-new-home-in-allston/ ("175 N.
# Harvard Street in Boston").
VENUES = {
    "loeb drama center": {"name": "Loeb Drama Center", "address": "64 Brattle Street",
                          "city": "Cambridge", "zip": "02138"},
    "farkas hall": {"name": "Farkas Hall", "address": "12 Holyoke Street",
                    "city": "Cambridge", "zip": "02138"},
    "goel center": {"name": "Goel Center for Creativity & Performance", "address": "175 N. Harvard Street",
                    "city": "Boston", "zip": "02134"},
}


def year_for_weekday(month: int, day: int, weekday: int, reference: date) -> Optional[int]:
    """The year near `reference` in which month/day falls on `weekday`, or None.

    Adjacent years put a date on different weekdays, so at most one of the
    three candidates matches.
    """
    for year in (reference.year, reference.year + 1, reference.year - 1):
        try:
            if date(year, month, day).weekday() == weekday:
                return year
        except ValueError:          # Feb 29 in a common year
            continue
    return None


class AmericanRepertoryTheaterScraper(BaseScraper):
    """A.R.T.'s show pages, one performance per booking instance."""

    def __init__(self, today: Optional[date] = None):
        super().__init__(
            source_name="American Repertory Theater",
            source_url=f"{BASE_URL}/shows-events/",
            use_selenium=False,
        )
        self.base_url = BASE_URL
        # Only the year is inferred from it, and only through the weekday match.
        self.today = today

    def fetch_page(self, url: str) -> str:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        return response.text

    def scrape_events(self) -> List[EventCreate]:
        reference = self.today or date.today()
        show_urls = self._get_show_urls(self.parse_html(self.fetch_page(self.source_url)))
        logger.info(f"Found {len(show_urls)} show pages to scrape")
        if not show_urls:
            raise ValueError(f"{self.source_name}: no show links on {self.source_url}")

        events = []
        for url in show_urls:
            try:
                events.extend(self.parse_show(self.parse_html(self.fetch_page(url)), url, reference))
            except Exception as e:
                logger.warning(f"{self.source_name}: error scraping {url}: {e}")
        return events

    def _get_show_urls(self, soup: BeautifulSoup) -> List[str]:
        """Every show page linked from the listing, in page order."""
        urls = []
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if href.startswith("/"):
                href = f"{self.base_url}{href}"
            if (href.startswith(f"{self.base_url}/shows-events/") and href.rstrip("/") != self.source_url.rstrip("/")
                    and not any(skip in href for skip in ("#", "?", "category", "page"))
                    and href not in urls):
                urls.append(href)
        return urls

    # --- one show ------------------------------------------------------------

    def parse_show(self, soup: BeautifulSoup, url: str, reference: date) -> List[EventCreate]:
        title = self._get_title(soup)
        if not title:
            logger.warning(f"{self.source_name}: no title on {url}")
            return []
        description = self._get_description(soup) or f"{title} at the American Repertory Theater."
        image_url = self._get_image(soup)
        venue = self._get_venue(soup, url)
        category = self._categorize(title, description)

        instances = soup.find_all("div", class_="c-booking-instance")
        if not instances:
            logger.warning(f"{self.source_name}: no performances listed for '{title}' ({url})")

        events = []
        for instance in instances:
            start = self.performance_start(instance, reference)
            if start is None:
                day = " ".join(instance.get_text(" ").split())[:60]
                logger.warning(f"Skipping a performance of '{title}' - no readable date ({day!r}, {url})")
                continue
            events.append(EventCreate(
                title=title[:200],
                description=description[:2000],
                start_datetime=start,
                source_url=self._booking_url(instance) or url,
                source_name=self.source_name,
                venue_name=venue["name"],
                street_address=venue.get("address"),
                city=venue.get("city"),
                state="MA",
                zip_code=venue.get("zip"),
                category=category,
                tags=self._access(instance),
                cost=self._cost(instance),
                image_url=image_url,
            ))
        logger.info(f"Found {len(events)} performances for {title}")
        return events

    @staticmethod
    def performance_start(instance, reference: date) -> Optional[datetime]:
        """ "Tuesday" + "May 18" + "7:30PM ET" -> the year whose May 18 is a Tuesday."""
        day = instance.find(class_="c-booking-instance__day")
        month_day = instance.find(class_="c-booking-instance__month")
        times = instance.find(class_="c-booking-instance__times")
        if not (day and month_day and times):
            return None

        weekday = WEEKDAYS.get(day.get_text(strip=True).lower())
        md = re.fullmatch(r"([A-Za-z]+)\s+(\d{1,2})", " ".join(month_day.get_text(" ").split()))
        tm = re.search(r"(\d{1,2})(?::(\d{2}))?\s*([AP]M)", times.get_text(" "), re.I)
        if weekday is None or not md or not tm or md.group(1).lower() not in MONTHS:
            return None

        month, dom = MONTHS[md.group(1).lower()], int(md.group(2))
        year = year_for_weekday(month, dom, weekday, reference)
        if year is None:
            return None
        hour = int(tm.group(1)) % 12 + (12 if tm.group(3).upper() == "PM" else 0)
        return datetime(year, month, dom, hour, int(tm.group(2) or 0))

    def _booking_url(self, instance) -> Optional[str]:
        link = instance.find("a", href=True)
        if not link:
            return None
        href = link["href"]
        return f"{self.base_url}{href}" if href.startswith("/") else href if href.startswith("http") else None

    def _cost(self, instance) -> Optional[str]:
        button = instance.find("div", class_="c-booking-instance__button")
        if button and "sold out" in button.get_text(" ").lower():
            return "Sold Out"
        price = instance.find("div", class_="c-booking-instance__price")
        return self.clean_text(price.get_text(" ")) if price else None

    def _access(self, instance) -> List[str]:
        """"ASL Interpreted", "Audio Described", "Open Captioned" - each before its "[?]"."""
        access = instance.find(class_="c-booking-instance__access")
        text = self.clean_text(access.get_text(" ")) if access else ""
        return [m.strip() for m in re.findall(r"([A-Z][A-Za-z ]{3,40}?)\s*\[\?\]", text)]

    def _get_title(self, soup: BeautifulSoup) -> Optional[str]:
        """"1972" + subtitle "A Rock Opera" -> "1972: A Rock Opera"."""
        title = soup.find(class_="c-pg-titles__title")
        if title and self.clean_text(title.get_text(" ")):
            text = self.clean_text(title.get_text(" "))
            subtitle = soup.find(class_="c-pg-titles__subtitle")
            sub = self.clean_text(subtitle.get_text(" ")) if subtitle else ""
            return f"{text}: {sub}" if sub else text

        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return self.clean_text(re.sub(r"\s+at A\.R\.T\.?$", "", og_title["content"].split(" | ")[0]))
        h1 = soup.find("h1")
        return self.clean_text(h1.get_text()) if h1 else None

    def _get_description(self, soup: BeautifulSoup) -> str:
        for attrs in ({"property": "og:description"}, {"name": "description"}):
            meta = soup.find("meta", attrs=attrs)
            if meta and meta.get("content"):
                return self.clean_text(meta["content"])
        return ""

    def _get_image(self, soup: BeautifulSoup) -> Optional[str]:
        og_image = soup.find("meta", property="og:image")
        return og_image["content"] if og_image and og_image.get("content") else None

    def _get_venue(self, soup: BeautifulSoup, url: str) -> Dict:
        """The building named in the masthead, with its address."""
        named = soup.find(class_="c-masthead__venue")
        name = self.clean_text(named.get_text(" ")) if named else ""
        key = name.lower()
        for prefix, venue in VENUES.items():
            if key.startswith(prefix):
                return venue
        if not name:
            logger.warning(f"{self.source_name}: no venue in the masthead of {url}; using Loeb Drama Center")
            return VENUES["loeb drama center"]

        # A building we have not seen: take the address from the page's venue card.
        card = soup.find(id="venue")
        lines = [self.clean_text(t) for t in card.find("p").stripped_strings] if card and card.find("p") else []
        where = re.match(r"(.+),\s*([A-Z]{2})\s+(\d{5})", lines[1]) if len(lines) > 1 else None
        if where:
            return {"name": name, "address": lines[0], "city": where.group(1), "zip": where.group(3)}
        logger.warning(f"{self.source_name}: unknown venue '{name}' with no address on {url}")
        return {"name": name}

    def _categorize(self, title: str, description: str) -> EventCategory:
        text = f"{title} {description}".lower()
        if any(word in text for word in ['workshop', 'class', 'conversation', 'panel', 'discussion']):
            return EventCategory.LECTURES
        elif any(word in text for word in ['screening', 'film', 'movie']):
            return EventCategory.ARTS_CULTURE
        elif any(word in text for word in ['gala', 'fundraiser', 'benefit']):
            return EventCategory.COMMUNITY
        elif any(word in text for word in ['concert', 'orchestra', 'symphony']):
            return EventCategory.MUSIC
        else:
            return EventCategory.THEATER
