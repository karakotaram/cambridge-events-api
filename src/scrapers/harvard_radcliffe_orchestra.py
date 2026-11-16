"""Harvard-Radcliffe Orchestra — the season's concerts.

harvardradcliffeorchestra.org/concerts is a Webflow page listing the season's
four concerts, upcoming and past, each as a block:

    CONCERT 3:
    March 6, 2027 / 8:00pm / Sanders Theatre
    Pricing: $25 General Admission, $50 VIP / ...

followed by the programme (composer, work, conductor, soloists), which becomes
the description. The date carries its year; a block without a readable date
and time is skipped.

Concerts are at Sanders Theatre, but tickets are sold through the Harvard Box
Office, and the "Sanders Theatre" source has not carried them: they reached the
calendar only through BostonShows.org and Harvard Square. The title names the
season and concert number ("Harvard-Radcliffe Orchestra: Season 219 Concert 3"),
which is how those listings name them too, so deduplication can match them.

A concert anywhere but Sanders Theatre is kept only if its block names a
Cambridge or Somerville address; the orchestra also tours.
"""
import logging
import re
from datetime import datetime
from typing import List, Optional

from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import LOCAL_CITIES, BaseFeedScraper, Place

logger = logging.getLogger(__name__)

PAGE = "https://www.harvardradcliffeorchestra.org/concerts"
NAME = "Harvard-Radcliffe Orchestra"

DATE = re.compile(r"^([A-Z][a-z]+ \d{1,2}, \d{4})$")
TIME = re.compile(r"^(\d{1,2}):(\d{2})\s*([ap])\.?m\.?$", re.I)
CONCERT = re.compile(r"^CONCERT\s+(\d+)\s*:?$", re.I)
SEASON = re.compile(r"\bSeason\s+(\d+)\b", re.I)


class HarvardRadcliffeOrchestraScraper(BaseFeedScraper):
    HOME = Place("Sanders Theatre", "45 Quincy Street", "Cambridge", "02138")
    CATEGORY = EventCategory.MUSIC

    def __init__(self):
        super().__init__(source_name=NAME, source_url=PAGE)

    def scrape_events(self) -> List[EventCreate]:
        soup = BeautifulSoup(self.fetch_html(self.source_url), "html.parser")
        headers = soup.select("div.div-block-87")
        if not headers:
            raise ValueError(f"{self.source_name}: no concert blocks on {self.source_url} — has the page changed?")
        season_heading = soup.find(string=SEASON)
        season = SEASON.search(season_heading).group(1) if season_heading else None
        events = []
        for header in headers:
            event = self.parse_concert(header, season)
            if event:
                events.append(event)
        logger.info(f"Scraped {len(events)} concerts from {self.source_name}")
        return events

    def parse_concert(self, header, season: Optional[str]) -> Optional[EventCreate]:
        heading = header.find(["h1", "h2", "h3"])
        number = CONCERT.match(self.clean_text(heading.get_text())) if heading else None
        details = header.select_one(".text-block-25")
        if number is None or details is None:
            return None
        lines = [self.clean_text(s) for s in details.stripped_strings]
        name = f"{NAME}: Season {season} Concert {number.group(1)}" if season else f"{NAME}: Concert {number.group(1)}"

        date = DATE.match(lines[0]) if lines else None
        time = TIME.match(lines[1]) if len(lines) > 1 else None
        if date is None or time is None:
            logger.warning(f"Skipping '{name}' - unreadable date/time {lines[:2]!r} ({self.source_url})")
            return None
        hour = int(time.group(1)) % 12 + (12 if time.group(3).lower() == "p" else 0)
        start = datetime.strptime(date.group(1), "%B %d, %Y").replace(hour=hour, minute=int(time.group(2)))

        where = self.where(name, lines[2:])
        if where is None:
            return None

        program = self.program(header)
        pricing = header.select_one(".text-block-26")
        price_lines = [self.clean_text(s) for s in pricing.stripped_strings] if pricing else []
        cost = "; ".join(ln.replace("Pricing:", "").strip() for ln in price_lines if ln) or None
        description = " ".join([f"The {NAME} at {where.venue}."] + program + price_lines[2:3])

        return EventCreate(
            title=name[:200],
            description=description[:2000],
            start_datetime=start,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.CATEGORY,
            cost=cost[:200] if cost else None,
            source_name=self.source_name,
            source_url=f"{PAGE}#concert-{number.group(1)}",
        )

    def where(self, name: str, lines: List[str]) -> Optional[Place]:
        venue = lines[0] if lines else ""
        if not venue or venue.lower() == self.HOME.venue.lower():
            return self.HOME
        if not any(city in " ".join(lines) for city in LOCAL_CITIES):
            logger.info(f"{self.source_name}: skipping '{name}' at {venue!r} - not Sanders and no local address")
            return None
        return self.place(name, venue, None, next(c for c in LOCAL_CITIES if c in " ".join(lines)))

    def program(self, header) -> List[str]:
        """The works listed after a concert's block, up to the next concert."""
        out = []
        for sibling in header.find_next_siblings("div"):
            if "div-block-87" in (sibling.get("class") or []):
                break
            if "div-block-90" not in (sibling.get("class") or []):
                continue
            composer = sibling.select_one(".text-block-27")
            works = [self.clean_text(p.get_text(" ")) for p in sibling.select(".rich-text-block p")]
            people = [self.clean_text(p.get_text(" ")) for p in sibling.select(".rich-text-block-3 p")]
            piece = ", ".join(w for w in works if w)
            if composer:
                piece = f"{self.clean_text(composer.get_text())}: {piece}" if piece else self.clean_text(composer.get_text())
            if people:
                piece += f" ({'; '.join(people)})"
            if piece:
                out.append(piece + ".")
        return out
