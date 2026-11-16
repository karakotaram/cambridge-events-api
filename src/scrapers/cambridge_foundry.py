"""The Foundry, 101 Rogers Street — two Softr lists embedded in its events page.

cambridgefoundry.org/events is a Webflow page carrying two iframes from a Softr
app: "Foundry-led" (its own maker-space classes, gallery shows, café pop-ups)
and "Community-led" (groups that book its rooms: yoga, fencing, Korean club,
improv, board meetings). Each embed reads a Softr table through the JSON
endpoints it calls itself:

  GET  .../blocks/<block>/datasources/<datasource>/metadata   field ids and names
  POST .../blocks/<block>/datasources/<datasource>/records    the rows, paged

The block's own filter runs on the server, so the rows are the ones the page
shows. Fields are found by name in the metadata rather than by their opaque
ids. The block and datasource ids are copied from the embeds; if the Foundry
rebuilds a block they change, the endpoints answer 400, and the source fails
loudly rather than quietly listing nothing.

**Times.** Start and End are UTC instants ("2026-11-16T15:00:00.000Z"). The
embed displays them converted to America/New_York — that row reads
"11/16/2026, 10:00 AM" — and they are converted the same way here.

**Runs are not sessions.** A row lasting more than a day is a gallery show, a
café pop-up, or a festival week. None of them says when to turn up, so they
are skipped. "Closed for Thanksgiving" and "Closed for Holiday Break" rows go
through base_feed's closure filter.

Images are signed S3 links that expire after two hours, so none are stored.
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional

import requests

from src.models.event import EventCategory, EventCreate, to_eastern_naive
from src.scrapers.base_feed import BaseFeedScraper, Place

logger = logging.getLogger(__name__)

SOFTR = ("https://waldo49575.softr.app/v1/datasource/applications/1044a234-47a2-400c-967a-065f4ee27989"
         "/pages/c5235fc0-eefc-442c-8e31-faab3702dace")

# (who runs it, block id, datasource id) — from the two iframes on the events page
LISTS = (
    ("Foundry-led", "78ff2b72-9f6c-49e2-9a38-bc62914b412a", "c199b253-107f-4a6b-a7aa-380cf21599e5"),
    ("Community-led", "cab4a9c4-c4ed-4300-bf8e-9f3d5fe7b116", "62f1e110-f6ab-4a28-9c73-5c5cb24e880f"),
)

PAGE_SIZE = 100
MAX_PAGES = 10
LONGEST_SESSION = timedelta(hours=24)

# The Foundry's own labels, for titles that name no kind of event.
# "Category" on community-led rows, "Foundry-led category" on its own.
LABEL_CATEGORIES = {
    "movement": EventCategory.SPORTS,
    "performing arts": EventCategory.ARTS_CULTURE,
    "visual arts": EventCategory.ARTS_CULTURE,
    "making": EventCategory.ARTS_CULTURE,
    "writing": EventCategory.ARTS_CULTURE,
    "maker space classes": EventCategory.ARTS_CULTURE,
    "galleries": EventCategory.ARTS_CULTURE,
    "culinary": EventCategory.FOOD_DRINK,
    "cafe pop ups": EventCategory.FOOD_DRINK,
}


class CambridgeFoundryScraper(BaseFeedScraper):
    HOME = Place("The Foundry", "101 Rogers Street", "Cambridge", "02142")
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Cambridge Foundry", source_url="https://www.cambridgefoundry.org/events")

    # -- fetching ---------------------------------------------------------- #

    def _headers(self) -> dict:
        headers = self.get_browser_headers()
        headers["Accept"] = "application/json"
        # The embed sends the viewer's zone; the list's "upcoming" filter uses it.
        headers["X-User-Timezone"] = "America/New_York"
        return headers

    def fetch_fields(self, endpoint: str) -> Dict[str, str]:
        """Field name -> field id."""
        response = requests.get(f"{endpoint}/metadata", headers=self._headers(), timeout=30)
        response.raise_for_status()
        return {f["name"]: f["id"] for f in response.json().get("fields") or []}

    def fetch_rows(self, endpoint: str, sort_field: Optional[str]) -> List[dict]:
        rows: List[dict] = []
        offset = None
        for _ in range(MAX_PAGES):
            body = {
                "options": {"timeZone": "America/New_York", "userLocale": "en-US"},
                "pageContext": None,
                "filterCriteria": {},
                "pagingOption": {"offset": offset, "count": PAGE_SIZE},
                "sortingOption": [{"sortingField": sort_field, "sortType": "ASC"}] if sort_field else [],
            }
            response = requests.post(f"{endpoint}/records", json=body, headers=self._headers(), timeout=30)
            response.raise_for_status()
            data = response.json()
            items = data.get("items") or []
            rows.extend(items)
            following = data.get("offset")
            if not items or following is None or int(following) >= int(data.get("total") or 0):
                return rows
            offset = following
        logger.warning(f"{self.source_name}: stopped after {MAX_PAGES} pages; the list has more")
        return rows

    def scrape_events(self) -> List[EventCreate]:
        events: List[EventCreate] = []
        for host, block, datasource in LISTS:
            endpoint = f"{SOFTR}/blocks/{block}/datasources/{datasource}"
            fields = self.fetch_fields(endpoint)
            missing = {"Program title", "Start"} - set(fields)
            if missing:
                raise ValueError(f"{self.source_name}: the {host} list has no field(s) {sorted(missing)}; "
                                 f"it has {sorted(fields)}")
            for row in self.fetch_rows(endpoint, fields["Start"]):
                event = self.parse_row(row.get("fields") or {}, fields, host)
                if event:
                    events.append(event)
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    # -- one row ------------------------------------------------------------ #

    @staticmethod
    def _instant(value) -> Optional[datetime]:
        """'2026-11-16T15:00:00.000Z' -> naive Eastern wall clock."""
        if not value or not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None  # an instant without a zone is a guess
        return to_eastern_naive(parsed)

    @staticmethod
    def _label(value) -> Optional[str]:
        if isinstance(value, dict):
            return value.get("label") or None
        return None

    def category_for(self, title: str, label: Optional[str]) -> EventCategory:
        by_title = self.categorize(title)
        if by_title != self.CATEGORY:
            return by_title
        return LABEL_CATEGORIES.get((label or "").lower(), self.CATEGORY)

    def parse_row(self, row: dict, fields: Dict[str, str], host: str) -> Optional[EventCreate]:
        def value(name: str):
            return row.get(fields[name]) if name in fields else None

        title = self.text(value("Program title"))
        if not self.is_event(title):
            return None

        start = self._instant(value("Start"))
        if start is None:
            logger.warning(f"Skipping '{title}' - no parseable start ({value('Start')!r})")
            return None
        end = self._instant(value("End"))
        if end is not None and end < start:
            end = None
        if end is not None and end - start > LONGEST_SESSION:
            logger.info(f"{self.source_name}: skipping '{title}' - runs {start:%b %d} to {end:%b %d}, "
                        f"a run rather than a session")
            return None

        room = self._label(value("Room"))
        label = self._label(value("Category")) or self._label(value("Foundry-led category"))
        age = self._label(value("Age"))
        free = (self._label(value("Free")) or "").lower().startswith("free")

        where = self.HOME
        venue = f"{where.venue} ({room})" if room else where.venue
        kind = f"{host} {label.lower()}" if label else host.lower()
        description = f"{title} — {kind} at {venue}, {where.street}, Cambridge."
        extras = [x for x in (age, "Free to attend" if free else None) if x]
        if extras:
            description += " " + ". ".join(extras) + "."

        return EventCreate(
            title=title[:200],
            description=description[:2000],
            start_datetime=start,
            end_datetime=end,
            venue_name=where.venue,
            street_address=where.street,
            city=where.city,
            state="MA",
            zip_code=where.zip_code,
            category=self.category_for(title, label),
            cost="Free" if free else None,
            source_name=self.source_name,
            source_url=self.source_url,
        )
