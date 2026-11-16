"""Harvard Gazette events calendar — Harvard's university-wide listings.

news.harvard.edu/gazette/harvard-events/events-calendar/ embeds the Trumba
calendar "gazette"; this reads its JSON (see base_trumba). It is an aggregator:
museums, libraries, schools, centres, the Memorial Church and outside presenters
at Harvard venues (Revels at Sanders) all post to it, so it is registered as
`aggregator` and a venue's own source wins deduplication.

On 2026-11-16 the 60-day feed held 818 items, most of them not events:

  - **Exhibitions**, one item per day the gallery is open: "Rubies" 9–5 every
    day, "Alfonso Ossorio" all day every day. Skipped when the item is classified
    Exhibition(s) and its title names no occurrence (a tour, talk, reception…),
    and by base_trumba's run rule when a title fills four or more days all day
    or for four hours or more ("Pop Quiz: 1776" is unclassified; A.R.T.'s
    "Mexodus" is listed all day on each of its 33 days, while the A.R.T. source
    has its timed performances).
  - **Not public**: "Harvard Key Required: Yes", or a description that limits
    entry ("This event is open to HUID holders only").
  - **Elsewhere**: Harvard Business School, the Longwood medical campus and
    Allston are in Boston; CSWR holds talks in Concord. Those are dropped by
    base_feed's place rules. Online items are kept, as Online.

Category is the title's (base_feed), falling back to the item's own Gazette
Classification — per-event metadata, not venue boilerplate.
"""
import re

from src.models.event import EventCategory
from src.scrapers.base_trumba import BaseTrumbaScraper

CLASSIFICATION = "Gazette Classification"

# A title that names an occurrence, even under an exhibition: "Curator-Led
# Tour: Cooking Up Change", "Curatorial Chat: 50 Shades of Modern".
OCCURRENCE = re.compile(
    r"\b(tours?|talks?|lectures?|reception|opening|performances?|conversation|panel|workshops?|"
    r"celebration|screenings?|readings?|concerts?|symposium|discussion|chat|presentation|"
    r"demonstration|family day|festival|book launch|signing)\b", re.I)
EXHIBITION_CLASS = re.compile(r"\bexhibitions?\b", re.I)

# Statements about who may attend. "Non-Harvard ID Holders: please enter
# through the lobby" welcomes the public, so ID wording must say "only"/"required".
RESTRICTED = re.compile(
    r"\bopen\s+(?:only\s+)?to\s+(?:current\s+)?(?:HUID|Harvard\s+(?:ID|Key))\s+holders"
    r"|\b(?:HUID|Harvard\s+(?:ID|Key))\s+(?:holders\s+only|(?:is\s+)?required)"
    r"|\bopen\s+(?:only\s+)?to\s+(?:the\s+)?Harvard\s+(?:community|affiliates|students|faculty|staff)"
    r"(?:\s+members)?\s+only"
    r"|\bHarvard\s+affiliates\s+only"
    r"|(?:not|n't)\s+open\s+to\s+the\s+(?:general\s+)?public"
    r"|\bclosed\s+to\s+the\s+public"
    r"|\binvite[\s-]+only|\bby\s+invitation\s+only", re.I)

# Gazette Classification values, most specific first.
CATEGORY_BY_CLASS = (
    ("Film", EventCategory.FILM),
    ("Theater", EventCategory.THEATER),
    ("Concerts", EventCategory.MUSIC),
    ("Dance", EventCategory.THEATER),
    ("Lecture", EventCategory.LECTURES),
    ("Conferences", EventCategory.LECTURES),
    ("Poetry/Prose", EventCategory.LECTURES),
    ("Classes/Workshops", EventCategory.LECTURES),
    ("Religion", EventCategory.COMMUNITY),
    ("Music", EventCategory.MUSIC),
    ("Art/Design", EventCategory.ARTS_CULTURE),
    ("Exhibitions", EventCategory.ARTS_CULTURE),
)


class HarvardGazetteScraper(BaseTrumbaScraper):
    HOME = None
    CATEGORY = EventCategory.COMMUNITY

    def __init__(self):
        super().__init__(source_name="Harvard Gazette",
                         web_name="gazette",
                         page="https://news.harvard.edu/gazette/harvard-events/events-calendar/")

    def classifications(self, item: dict) -> list:
        return [c.strip() for c in self.field(item, CLASSIFICATION).split(",") if c.strip()]

    def is_run(self, item: dict) -> bool:
        if super().is_run(item):
            return True
        exhibition = any(EXHIBITION_CLASS.fullmatch(c) for c in self.classifications(item))
        return exhibition and not OCCURRENCE.search(self.text(item.get("title")))

    def is_public(self, item: dict) -> bool:
        if self.field(item, "Harvard Key Required").lower() == "yes":
            return False
        return not RESTRICTED.search(f"{self.text(item.get('title'))}\n{self.text(item.get('description'))}")

    def categorize_item(self, item: dict, title: str):
        category = self.categorize(title)
        if category != self.CATEGORY:
            return category
        classes = set(self.classifications(item))
        for name, mapped in CATEGORY_BY_CLASS:
            if name in classes:
                return mapped
        return self.CATEGORY
