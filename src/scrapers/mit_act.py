"""MIT Art, Culture and Technology (ACT) — its public lecture series.

A WordPress / The Events Calendar site, read through the Tribe REST API and
limited to the "lecture-series" category, the page this source was asked for.
The rest of ACT's feed is student exhibitions listed as weeks-long all-day
spans and graduate-admissions open houses.

**Where.** ACT records no Tribe venue for any event. The room is typed into
the event page instead, under the date:

    <div class="event-meta">
      <p><span class="tribe-event-date-start">November 16, 2026, 6:00 pm</span></p>
      <p>MIT E15-070<br />Wiesner Building<br />20 Ames Street<br />Cambridge, MA 02142</p>
    </div>

so each upcoming lecture's page is read for it (a handful a semester). Some
pages stop before the city ("MIT Building 12 / Room 0168"); a place that names
an MIT building is on the Cambridge campus. A page with no place, or a
city-less place that is not visibly MIT's, skips its lecture: ACT uses several
rooms (E15-070, the ACT Cube, Bartos Theater, W41), so there is no single home
to fall back on.

**TLS.** act.mit.edu serves its certificate without the intermediate.
Browsers and macOS curl fetch the missing link themselves; requests does not,
and fails with CERTIFICATE_VERIFY_FAILED. Every request here verifies against
`mit_act_chain.pem` — that intermediate and its root — so the check is still
made, against a narrower set of authorities than usual. If ACT changes
certificate authority, this source fails loudly until the file is updated
(see its header for where each certificate came from).
"""
import logging
import re
from pathlib import Path
from typing import Optional

import requests
from bs4 import BeautifulSoup

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_tribe import PER_PAGE, BaseTribeScraper

logger = logging.getLogger(__name__)

CA_CHAIN = str(Path(__file__).with_name("mit_act_chain.pem"))

# "Cambridge, MA 02142" / "Cambridge, Massachusetts"
CITY_LINE = re.compile(r"^(?P<city>[A-Za-z .'-]+?),\s*(?:MA|Massachusetts)\b\.?\s*(?P<zip>\d{5})?", re.I)
STREET = re.compile(r"^\d+[A-Za-z]?\s+\S")
# "MIT Building 12", "W41- 1216", "E15-070": a room on MIT's campus
CAMPUS = re.compile(r"\bMIT\b|\b[ENW]?\d{1,2}[A-Z]?-\s?\d{2,4}\b")


class MITACTScraper(BaseTribeScraper):
    HOME = None
    CATEGORY = EventCategory.LECTURES
    CATEGORY_SLUG = "lecture-series"

    def __init__(self):
        super().__init__(source_name="MIT Art, Culture and Technology", site="https://act.mit.edu",
                         page="/events/category/lecture-series/")

    def get(self, url: str, params: Optional[dict] = None) -> requests.Response:
        return requests.get(url, params=params, headers=self.get_browser_headers(), timeout=30, verify=CA_CHAIN)

    def fetch_page(self, page: int) -> Optional[dict]:
        response = self.get(self.api_url, params={"categories": self.CATEGORY_SLUG, "per_page": PER_PAGE,
                                                  "start_date": "now", "status": "publish", "page": page})
        if page > 1 and response.status_code in (400, 404):
            return None
        response.raise_for_status()
        return response.json()

    def parse_item(self, item: dict) -> Optional[EventCreate]:
        venue = self._as_dict(item.get("venue"))
        if not (venue.get("venue") or venue.get("city")) and item.get("url"):
            item = {**item, "venue": self.venue_from_page(item["url"])}
        return super().parse_item(item)

    def venue_from_page(self, url: str) -> dict:
        """The room typed under the date on the event page, as a Tribe venue dict."""
        try:
            response = self.get(url)
            response.raise_for_status()
        except Exception as e:
            logger.warning(f"{self.source_name}: could not read {url} for its location: {e}")
            return {}
        venue = self.parse_location(response.text)
        if not venue.get("city"):
            logger.warning(f"{self.source_name}: no city in the location on {url} ({venue or 'none given'})")
        return venue

    @staticmethod
    def parse_location(html: str) -> dict:
        """{venue, address, city, zip} from the event page; {} if it names no place.

        Some pages stop before the city ("MIT Building 12 / Room 0168"). A
        place that names an MIT building is on the Cambridge campus; any other
        city-less place is returned without a city, and base_feed skips it.
        """
        soup = BeautifulSoup(html, "html.parser")
        for p in soup.select(".event-meta p"):
            if p.select_one(".tribe-event-date-start"):
                continue
            lines = [line.strip() for line in p.get_text("\n").split("\n") if line.strip()]
            if not lines:
                continue
            city = zip_code = None
            for i, line in enumerate(lines):
                match = CITY_LINE.match(line)
                if match:
                    city, zip_code = match.group("city").strip(), match.group("zip")
                    lines = lines[:i]
                    break
            else:
                if CAMPUS.search(" ".join(lines)):
                    city = "Cambridge"
            streets = [line for line in lines if STREET.match(line)]
            street = streets[-1] if streets else None
            return {
                "venue": ", ".join(line for line in lines if line != street) or None,
                "address": street,
                "city": city,
                "zip": zip_code,
            }
        return {}
