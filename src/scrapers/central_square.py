"""Scraper for Central Square Theater.

The calendar is EventON, which loads each month's events by POSTing the
calendar's settings to `/?evo-ajax=eventon_get_events` and receives the month as
JSON (`html`, plus the settings for the month it now shows, `SC`). Calling that
endpoint directly reads any month with plain requests, the same way the page's
"next month" arrow does. The old Selenium scraper clicked that arrow, but a
Mailmunch popup iframe intercepted the click on every run, so only two of the
four months it meant to read were ever read.

Each listing carries its start as a Unix timestamp. What it does not carry is a
description: EventON's schema description is just the title in quotes
("'Eleanor'"), which EventValidator rejects as too short. The synopsis lives on
the show's own page (/shows/<slug>/), which the calendar page links to, so each
show page is read once and matched to its performances by title.
"""
import html
import json
import logging
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

SITE = "https://www.centralsquaretheater.org"

# The season is announced about a year out; months past it come back empty.
MONTHS = 12

# Variants of a show that share its page: "The Getaway Driver (captioned)",
# "Scholar Social for The Getaway Driver", "Artists & Audiences for Arcadia".
SHOW_PREFIX = re.compile(r"^(scholar social|artists\s*&\s*audiences|talkback|post-show [a-z ]+)\s+(for|of|with)\s+", re.I)
SHOW_SUFFIX = re.compile(r"\s*\((captioned|asl[^)]*|open caption[^)]*|audio[- ]described[^)]*|relaxed[^)]*)\)\s*$", re.I)

# Paragraphs on a show page after the synopsis.
SYNOPSIS_END = ("directed by", "season tickets", "buy tickets", "student matinees",
                "interested in bringing", "central conversations", "join us")


def _key(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", html.unescape(title or "").lower()).strip()


def show_key(title: str) -> str:
    """The show a calendar title belongs to, as a comparable key."""
    return _key(SHOW_SUFFIX.sub("", SHOW_PREFIX.sub("", html.unescape(title or "").strip())))


class CentralSquareTheaterScraper(BaseScraper):
    """Central Square Theater's EventON calendar, read through its own AJAX endpoint."""

    def __init__(self):
        super().__init__(
            source_name="Central Square Theater",
            source_url=f"{SITE}/calendar/",
            use_selenium=False,
        )
        self.session = requests.Session()

    # --- fetching (the two seams tests replace) -----------------------------

    def fetch_page(self, url: str) -> str:
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.text

    def fetch_month(self, params: dict, sc: dict, direction: str) -> dict:
        """One month from EventON, as the page's own month arrows request it."""
        data = {"direction": direction, "ajaxtype": "switchmonth",
                "nonce": params.get("n", ""), "nonceX": params.get("nonce", "")}
        data.update({f"shortcode[{k}]": v for k, v in sc.items()})
        response = self.session.post(f"{SITE}/?evo-ajax=eventon_get_events", data=data,
                                     headers={"X-WP-Nonce": params.get("nonce", "")}, timeout=30)
        response.raise_for_status()
        return response.json()

    # --- the run ---------------------------------------------------------------

    def scrape_events(self) -> List[EventCreate]:
        page = self.fetch_page(self.source_url)
        params, sc = self.calendar_state(page)
        shows = self.read_shows(self.show_urls(page))

        events: List[EventCreate] = []
        seen = set()
        direction = "none"                  # the current month first, then forward
        for _ in range(MONTHS):
            payload = self.fetch_month(params, sc, direction)
            if payload.get("status") != "GOOD" or "SC" not in payload:
                raise ValueError(f"{self.source_name}: EventON returned {str(payload)[:200]}")
            sc = payload["SC"]
            for event in self.parse_month(payload.get("html", ""), shows):
                key = (event.title, event.start_datetime)
                if key not in seen:
                    seen.add(key)
                    events.append(event)
            direction = "next"
        return events

    @staticmethod
    def calendar_state(page: str) -> tuple:
        """EventON's nonces and the calendar's settings, from the calendar page."""
        params = re.search(r"evo_general_params\s*=\s*(\{.*?\});", page)
        sc = re.search(r"class=['\"]evo_cal_data['\"]\s+data-sc=\"([^\"]+)\"", page)
        if not (params and sc):
            raise ValueError("Central Square Theater: calendar page no longer carries EventON's settings")
        return json.loads(params.group(1)), json.loads(html.unescape(sc.group(1)))

    @staticmethod
    def show_urls(page: str) -> List[str]:
        found = re.findall(rf"{re.escape(SITE)}/shows/[a-z0-9-]+/", page)
        return list(dict.fromkeys(found))

    def read_shows(self, urls: List[str]) -> Dict[str, dict]:
        """{show key: {url, synopsis, image}} for each show page that loads."""
        shows = {}
        for url in urls:
            try:
                info = self.show_info(self.fetch_page(url), url)
            except Exception as e:
                logger.warning(f"{self.source_name}: could not read show page {url}: {e}")
                continue
            if info:
                shows[show_key(info["title"])] = info
        return shows

    def show_info(self, page: str, url: str) -> Optional[dict]:
        soup = BeautifulSoup(page, "html.parser")
        h1 = soup.find("h1", class_="header large") or soup.find("h1")
        og_title = soup.find("meta", property="og:title")
        title = self.clean_text(h1.get_text()) if h1 else (
            og_title["content"].split(" - ")[0] if og_title and og_title.get("content") else "")
        if not title:
            return None

        synopsis = []
        content = soup.find("div", class_="main-content") or soup.find("div", class_="content-area")
        for p in content.find_all("p") if content else []:
            text = self.clean_text(p.get_text(" "))
            if any(marker in text.lower() for marker in SYNOPSIS_END):
                break
            if text:
                synopsis.append(text)
        text = " ".join(synopsis)
        if len(text) < 20:
            og = soup.find("meta", property="og:description")
            text = self.clean_text(og["content"]) if og and og.get("content") else ""

        image = soup.find("meta", property="og:image")
        return {"title": title, "url": url, "synopsis": text,
                "image": image.get("content") if image else None}

    # --- one month ---------------------------------------------------------------

    def parse_month(self, month_html: str, shows: Dict[str, dict]) -> List[EventCreate]:
        events = []
        soup = BeautifulSoup(month_html or "", "html.parser")
        for item in soup.select("div.eventon_list_event"):
            if "no_events" in (item.get("class") or []):
                continue
            try:
                event = self.parse_item(item, shows)
            except Exception as e:
                logger.warning(f"{self.source_name}: failed to parse a listing: {e}")
                continue
            if event:
                events.append(event)
        return events

    @staticmethod
    def _schema(item) -> dict:
        script = item.find("script", type="application/ld+json")
        if not script or not script.string:
            return {}
        try:
            return json.loads(script.string, strict=False)
        except ValueError:
            return {}

    def parse_item(self, item, shows: Dict[str, dict]) -> Optional[EventCreate]:
        title_el = item.find("span", class_="evcal_event_title")
        title = self.clean_text(html.unescape(title_el.get_text())) if title_el else ""
        if len(title) < 3:
            return None

        schema = self._schema(item)
        classes = item.get("class") or []
        if "cancelled" in classes or str(schema.get("eventStatus", "")).endswith("EventCancelled"):
            logger.info(f"{self.source_name}: skipping cancelled '{title}'")
            return None

        start = self.start_of(item)
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable date ({self.source_url})")
            return None
        listed = self.schema_wall_clock(schema.get("startDate"))
        if listed is not None and listed != start:
            logger.warning(f"Skipping '{title}' - timestamp says {start}, listing says {listed}")
            return None

        show = shows.get(show_key(title))
        description = self.description(title, schema.get("description"), show)

        link = item.find("a", class_="evcal_list_a")
        href = (link.get("href") or "").strip() if link else ""
        if href.startswith("http"):
            url = href                       # the performance's ticket page
        elif show:
            url = show["url"]
        elif str(schema.get("url", "")).startswith("http"):
            url = schema["url"]              # the event's own page on the theater's site
        else:
            url = self.source_url

        image = schema.get("image") or (show or {}).get("image") or None

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            venue_name="Central Square Theater",
            street_address="450 Massachusetts Avenue",
            city="Cambridge",
            state="MA",
            zip_code="02139",
            category=EventCategory.THEATER,
            image_url=image if isinstance(image, str) and image.startswith("http") else None,
            source_name=self.source_name,
            source_url=url,
        )

    @staticmethod
    def start_of(item) -> Optional[datetime]:
        """data-time is "start-end" in Unix seconds (UTC)."""
        raw = (item.get("data-time") or "").split("-")[0]
        if not raw.isdigit():
            return None
        instant = datetime.fromtimestamp(int(raw), tz=timezone.utc)
        return to_eastern_naive(instant).replace(second=0, microsecond=0)

    @staticmethod
    def schema_wall_clock(value) -> Optional[datetime]:
        """The wall clock in EventON's schema startDate, e.g. "2027-2-4T19:30-4:00".

        Only the wall clock is read: EventON writes -4:00 in winter too, so the
        offset is wrong half the year. The timestamp and this must agree.
        """
        m = re.match(r"(\d{4})-(\d{1,2})-(\d{1,2})T(\d{1,2}):(\d{2})", str(value or ""))
        if not m:
            return None
        try:
            return datetime(*map(int, m.groups()))
        except ValueError:
            return None

    def description(self, title: str, schema_description: Optional[str], show: Optional[dict]) -> str:
        """The schema's own text if it says more than the title; else the show's synopsis."""
        own = self.clean_text(html.unescape(BeautifulSoup(schema_description or "", "html.parser").get_text(" ")))
        if _key(own) != _key(title) and len(own) >= 20:
            return own

        synopsis = (show or {}).get("synopsis") or ""
        if synopsis:
            if SHOW_PREFIX.match(title):     # a talk attached to the show
                return f"{title}, at Central Square Theater. About the show: {synopsis}"
            variant = SHOW_SUFFIX.search(title)
            if variant:                      # "(captioned)"
                return f"{variant.group(1).capitalize()} performance. {synopsis}"
            return synopsis
        return f"{title} at Central Square Theater, Cambridge."
