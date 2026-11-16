"""Global Arts Live — the presenter's Cambridge and Somerville shows.

Global Arts Live (formerly World Music/CRASHarts) presents at its own venue,
The Platform (315 Kendall St, Cambridge: the Takeda Theater and the Tempo
Stage), and at halls around Boston: the Somerville Theatre and its Crystal
Ballroom, Sanders Theatre, the Berklee Performance Center, the Cutler
Majestic. Only shows in Cambridge or Somerville are kept.

The site is Drupal 7. Its homepage carries the season as one list, a row per
show:

    <li class="views-row">
      ... <a class="event-links" href="/content/event_page/10407/">John Scofield's Electrospective</a>
      <span class="date-display-single" property="dc:date" content="2026-11-19T19:30:00-05:00">
      <a href="/content/venue/somerville-theatre">Somerville Theatre</a>
      ...

**When.** A row with one `dc:date` is one performance, at that instant. A row
with two is a run, printed "first - last": Cirque Kalabanté's row shows Jan 29
and Jan 31, and its page lists Jan 29, Jan 30 and Jan 31. So a run's own page
is read for its performances, each a `dc:date` like the row's. A run whose page
cannot be read is skipped; publishing only its first and last nights would
look complete and not be.

**Where.** The row names a venue page, whose address field reads "315 Kendall
St, Cambridge, MA 02142" or "55 Davis Square, Somerville". Each venue's page
is read once per run, and its city decides whether the show is kept. A run at
a venue outside the area is dropped before its page is fetched. The Platform's
stages ("Takeda Theater at The Platform") are listed at The Platform, the name
both stage pages give the building, with the stage in the description: one
venue for readers, and the same venue name BostonShows.org uses, so its copies
merge into these.
"""
import logging
import re
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place, city_of

logger = logging.getLogger(__name__)

SITE = "https://www.globalartslive.org"
ZIP = re.compile(r"\b(\d{5})\b")


class GlobalArtsLiveScraper(BaseFeedScraper):
    HOME = None
    CATEGORY = EventCategory.MUSIC
    # Seconds between page reads after the first: ~20 pages a run
    PAUSE = 1.0

    def __init__(self):
        super().__init__(source_name="Global Arts Live", source_url=f"{SITE}/")

    def fetch_html(self, url: str, retries: int = 3) -> str:
        if url != self.source_url and self.PAUSE:
            time.sleep(self.PAUSE)
        return super().fetch_html(url, retries)

    def scrape_events(self) -> List[EventCreate]:
        venues: Dict[str, Optional[Tuple[Place, Optional[str]]]] = {}
        events: List[EventCreate] = []
        seen = set()
        for show in self.parse_listing(self.fetch_html(self.source_url)):
            if not self.is_event(show["title"]):
                continue
            href = show["venue_href"]
            if href not in venues:
                venues[href] = self.venue(href, show["venue"])
            if venues[href] is None:
                continue
            where, stage = venues[href]
            if len(show["starts"]) > 1:
                show = {**show, "starts": self.performances(show)}
            for start in show["starts"]:
                key = (show["title"].lower(), start)
                if key in seen:
                    continue
                seen.add(key)
                events.append(self.make_event(show, start, where, stage))
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- the season list -------------------------------------------------- #

    def parse_listing(self, html: str) -> List[dict]:
        soup = BeautifulSoup(html, "html.parser")
        rows = soup.select(".view-performancelist li.views-row")
        if not rows:
            raise ValueError(f"{self.source_name}: no performance list on {self.source_url}")
        shows = []
        for row in rows:
            link = row.select_one(".views-field-title a.event-links[href]")
            venue = row.select_one(".views-field-field-venue a[href]")
            title = self.clean_text(link.get_text(" ")) if link else ""
            if not (link and title):
                continue
            url = urljoin(SITE, link["href"])
            if venue is None:
                logger.warning(f"Skipping '{title}' - no venue on its listing row ({url})")
                continue
            starts = self.instants(row)
            if not starts:
                logger.warning(f"Skipping '{title}' - no readable date ({url})")
                continue
            teaser = row.select_one(".views-field-body")
            if teaser:
                for more in teaser.select("a.views-more-link"):
                    more.decompose()
            image = row.select_one(".views-field-field-smallimage1 img[src]")
            ticket = row.select_one(".views-field-php")
            shows.append({
                "title": title,
                "url": url,
                "venue": self.clean_text(venue.get_text(" ")),
                "venue_href": urljoin(SITE, venue["href"]),
                "starts": starts,
                "teaser": self.clean_text(teaser.get_text(" ")) if teaser else "",
                "image": image["src"] if image else None,
                "free": bool(ticket and self.clean_text(ticket.get_text(" ")).upper() == "FREE"),
            })
        return shows

    @staticmethod
    def instants(node) -> List[datetime]:
        """Every dc:date under a node, as aware datetimes (the model makes them naive Eastern)."""
        out = []
        for span in node.select('[property="dc:date"][content]'):
            try:
                when = datetime.fromisoformat(span["content"])
            except ValueError:
                continue
            if when.tzinfo is None or when.second or when.microsecond:
                continue  # the site always sends an offset and whole minutes
            out.append(when)
        return out

    def performances(self, show: dict) -> List[datetime]:
        """A run's performances, from its own page. [] if unreadable (the run is skipped)."""
        try:
            html = self.fetch_html(show["url"])
        except Exception as e:
            logger.warning(f"Skipping '{show['title']}' - could not read its performances: {e} ({show['url']})")
            return []
        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one("#content") or soup
        starts = sorted(set(self.instants(content)))
        listed = show["starts"]
        if not starts or starts[0] != min(listed) or starts[-1] != max(listed):
            logger.warning(f"Skipping '{show['title']}' - its page lists {starts}, its row {listed} ({show['url']})")
            return []
        return starts

    # -- venues ----------------------------------------------------------- #

    def venue(self, href: str, name: str) -> Optional[Tuple[Place, Optional[str]]]:
        """(place, stage) from a venue page; None (skip its shows) if outside the area or unreadable.

        The Platform's two stages link to their own pages, both of which name
        the venue "The Platform". The building is the venue, so that readers
        and deduplication see one place; the stage goes into the description.
        """
        try:
            html = self.fetch_html(href)
        except Exception as e:
            logger.warning(f"{self.source_name}: could not read venue page {href}: {e}")
            return None
        building, street, city, zip_code = self.parse_venue_page(html)
        if city is None:
            logger.warning(f"{self.source_name}: no city on venue page {href}; skipping shows at {name}")
            return None
        stage = None
        if building and name.lower().endswith(f" at {building.lower()}"):
            stage, name = name, building
        where = self.place(name, name, street, city, zip_code)
        return (where, stage) if where else None

    @staticmethod
    def parse_venue_page(html: str) -> Tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
        """-> (venue name, street, city, zip) from '315 Kendall St, Cambridge, MA 02142'."""
        soup = BeautifulSoup(html, "html.parser")
        name = soup.select_one(".field-name-field-venuename .field-item")
        field = soup.select_one(".field-name-field-venue-address .field-item")
        building = " ".join(name.get_text(" ").split()) if name else None
        text = " ".join(field.get_text(" ").split()) if field else ""
        parts = [p.strip() for p in text.split(",") if p.strip()]
        if len(parts) < 2:
            return building, None, None, None
        zip_match = ZIP.search(text)
        return building, parts[0], city_of(parts[1]), zip_match.group(1) if zip_match else None

    # -- one performance -------------------------------------------------- #

    def make_event(self, show: dict, start: datetime, where: Place, stage: Optional[str] = None) -> EventCreate:
        title = show["title"]
        description = show["teaser"]
        if len(description) < 20:
            description = self.fallback_description(title, where)
        if stage:
            description = f"{description} ({stage})"
        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.categorize(title),
            cost="Free" if show["free"] else None,
            source_name=self.source_name,
            source_url=show["url"],
            image_url=show["image"],
        )
