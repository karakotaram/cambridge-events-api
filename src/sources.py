"""The source registry — layer 0 of the system.

This is the only place a source is described. `scrape.py` runs what is listed
here, the CI monitor watches what is listed here, and the `cal` CLI enumerates
what is listed here. Adding a scraper means adding one row.

It exists because the same fact used to live in two hand-maintained lists — the
`register_scraper()` calls in `scrape.py` and `REGISTERED_SOURCES` in
`src/agents/ci_monitor.py` — and they drifted. Four scrapers ran daily with no
monitoring at all, and two monitored entries pointed at nothing. Nobody was
careless; two copies of one fact drift because that is what they do.

Scraper classes are imported lazily so that listing sources stays fast and does
not pull in Selenium or Playwright.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Iterator, Literal, Optional

# How a source is fetched. Also the run order: plain HTTP first so that the
# expensive browser-driven scrapers start after the cheap ones have finished,
# and aggregators last so original sources win deduplication.
Kind = Literal["requests", "selenium", "playwright", "aggregator", "manual"]

KIND_ORDER: dict[str, int] = {
    "requests": 0,
    "selenium": 1,
    "playwright": 2,
    "aggregator": 3,
    "manual": 4,
}


@dataclass(frozen=True)
class Source:
    """One event source.

    name        canonical, must equal the `source_name` the scraper emits
    module      dotted path, e.g. "src.scrapers.brattle"
    cls         scraper class name; None for sources not produced by a scraper
    kind        how it is fetched; also determines run order
    runs_in_ci  False if the venue blocks GitHub's IP ranges
    notes       why anything above is unusual
    """

    name: str
    module: Optional[str]
    cls: Optional[str]
    kind: Kind
    runs_in_ci: bool = True
    notes: str = ""

    @property
    def is_scraped(self) -> bool:
        """False for sources fed by something other than the scrape pipeline."""
        return self.cls is not None

    def load(self):
        """Import and instantiate the scraper. Raises for non-scraped sources."""
        if not self.is_scraped:
            raise ValueError(f"{self.name} has no scraper (kind={self.kind}): {self.notes}")
        return getattr(importlib.import_module(self.module), self.cls)()


SOURCES: tuple[Source, ...] = (
    # ---- plain HTTP (requests / cloudscraper) --------------------------------
    Source("Lamplighter Brewing", "src.scrapers.lamplighter", "LamplighterScraper", "requests"),
    Source("Boston Swing Central", "src.scrapers.boston_swing", "BostonSwingCentralScraper", "requests",
           runs_in_ci=False, notes="blocks GitHub cloud IPs; run scrape_local.py"),
    Source("The Comedy Studio", "src.scrapers.comedy_studio", "ComedyStudioScraper", "requests"),
    Source("The Dance Complex", "src.scrapers.dance_complex", "DanceComplexScraper", "requests"),
    Source("BostonShows.org", "src.scrapers.bostonshows", "BostonShowsScraper", "requests"),
    Source("Theatre at First", "src.scrapers.theatre_at_first", "TheatreAtFirstScraper", "requests"),
    Source("First Parish in Cambridge", "src.scrapers.first_parish", "FirstParishScraper", "requests"),
    Source("Harvard Art Museums", "src.scrapers.harvard_art_museums", "HarvardArtMuseumsScraper", "requests"),
    Source("Brattle Theatre", "src.scrapers.brattle", "BrattleTheaterScraper", "requests"),
    Source("Grolier Poetry Book Shop", "src.scrapers.grolier", "GrolierPoetryBookshopScraper", "requests"),
    Source("Multicultural Arts Center", "src.scrapers.multicultural_arts", "MulticulturalArtsCenterScraper", "requests"),
    Source("The Rockwell", "src.scrapers.rockwell", "RockwellScraper", "requests"),
    Source("The Mad Monkfish", "src.scrapers.mad_monkfish", "MadMonkfishScraper", "requests"),
    Source("Mount Auburn Cemetery", "src.scrapers.mount_auburn", "MountAuburnScraper", "requests"),
    Source("Harvard Athletics", "src.scrapers.harvard_athletics", "HarvardAthleticsScraper", "requests"),
    Source("Harvard GSD", "src.scrapers.harvard_gsd", "HarvardGSDScraper", "requests"),
    Source("The Sinclair", "src.scrapers.the_sinclair", "TheSinclairScraper", "requests"),
    Source("Harvard-Radcliffe Dramatic Club", "src.scrapers.hrdc", "HRDCScraper", "requests",
           notes="month-grid calendar; plain HTTP is enough, was needlessly on Selenium"),
    Source("City of Cambridge", "src.scrapers.cambridge_gov", "CambridgeGovScraper", "requests",
           notes="was Selenium until 2026-08-31; the browser died mid-run and the "
                 "scraper fabricated dates for everything after. Listing markup "
                 "carries the dates, so no browser is needed."),
    Source("Somerville Theatre", "src.scrapers.somerville_theatre", "SomervilleTheatreScraper", "requests",
           runs_in_ci=False, notes="cloudscraper; SSL handshake fails in CI"),

    # ---- Selenium ------------------------------------------------------------
    Source("The Lily Pad", "src.scrapers.lilypad", "LilyPadScraper", "selenium"),
    Source("The Middle East", "src.scrapers.mideast", "MideastClubScraper", "selenium"),
    Source("Portico Brewing", "src.scrapers.portico", "PorticoScraper", "selenium"),
    Source("Arts at the Armory", "src.scrapers.armory", "ArtsAtTheArmoryScraper", "selenium"),
    Source("Central Square Theater", "src.scrapers.central_square", "CentralSquareTheaterScraper", "selenium"),
    Source("American Repertory Theater", "src.scrapers.art", "AmericanRepertoryTheaterScraper", "selenium"),
    Source("Aeronaut Brewing", "src.scrapers.aeronaut", "AeronautScraper", "selenium",
           runs_in_ci=False, notes="blocks GitHub cloud IPs; run scrape_local.py"),

    # ---- Playwright ----------------------------------------------------------
    Source("Porter Square Books", "src.scrapers.porter", "PorterSquareBooksScraper", "playwright",
           notes="Drupal behind bot protection: plain HTTP and Selenium both get 403, Playwright does not"),
    Source("Sanders Theatre", "src.scrapers.sanders_theatre", "SandersTheatreScraper", "playwright",
           notes="calendar.college.harvard.edu 404s; listings moved to the Harvard "
                 "Box Office, an AudienceView storefront that renders client-side"),
    Source("Longfellow House", "src.scrapers.longfellow_house", "LongfellowHouseScraper", "playwright",
           notes="captures the NPS calendar JSON from the browser: parsing the cards "
                 "races a progressive render, and calling the API directly takes ~150s"),
    Source("Museum of Science", "src.scrapers.museum_of_science", "MuseumOfScienceScraper", "playwright"),
    Source("Regent Theatre", "src.scrapers.regent_theatre", "RegentTheatreScraper", "playwright"),
    Source("Longy School of Music", "src.scrapers.longy", "LongyScraper", "playwright",
           notes="rate-limits into an Imunify360 challenge page under repeated "
                 "requests; a single daily scrape is fine, bursts are not"),
    Source("MIT Events", "src.scrapers.mit_calendar", "MITCalendarScraper", "playwright"),
    Source("MIT Music & Theater", "src.scrapers.mit_music_theater", "MITMusicTheaterScraper", "playwright"),
    Source("MIT Open Space", "src.scrapers.openspace_mit", "OpenSpaceMITScraper", "playwright"),
    Source("Skip the Small Talk", "src.scrapers.skip_small_talk", "SkipSmallTalkScraper", "playwright"),

    # Retired 2026-09-01: "Harvard Book Store". harvard.com moved behind
    # Cloudflare's interstitial — "Just a moment... Enable JavaScript and
    # cookies" — for plain HTTP, for headless Chromium, from CI and from a
    # residential IP alike. It had been marked CI-blocked; it is now blocked
    # everywhere, and getting past it means defeating protection the venue chose.
    #
    # Coverage continues: the Harvard Square aggregator lists 36 of their events
    # with correct dates and reaches a month further out than the direct
    # scraper's last good run did. A handful of their off-site events (the
    # virtual warehouse sale, readings hosted at First Parish) are not carried
    # there, which is the cost of this.

    # Retired 2026-09-01: "Harvard Memorial Church" returns 403 for every path on
    # memorialchurch.harvard.edu — homepage, calendar, RSS, REST — to plain HTTP
    # and to a real headless browser alike, from a residential IP as well as CI.
    # That is a deliberate block, not a broken scraper, and the Harvard-wide
    # calendar does not carry their listings either. Getting past it would mean
    # working around protection the venue chose to put up. If they ever open
    # access, git history has the scraper.

    # Retired 2026-09-01: "Cambridge Public Library" had its own scraper, but it
    # fabricated every date (datetime.now() at 10:00 for anything it could not
    # parse) and was wholly redundant — all 17 events it produced already appear
    # in City of Cambridge, which carries 802 library-branch listings with their
    # real times. Fixing it would have meant maintaining a second, worse path to
    # the same data. Library events still reach the calendar via City of Cambridge.

    # ---- aggregators (last, so original sources win deduplication) -----------
    Source("Harvard Square", "src.scrapers.harvard_square", "HarvardSquareScraper", "aggregator"),

    # ---- not scraped ---------------------------------------------------------
    Source("User Submitted", None, None, "manual",
           notes="fed by sync_user_events.py from Google Sheets; always preserved "
                 "by scrape.py rather than re-collected"),
)


BY_NAME: dict[str, Source] = {s.name: s for s in SOURCES}


def in_run_order(*, is_ci: bool = False) -> Iterator[Source]:
    """Scraped sources, cheapest transport first, aggregators last.

    In CI, sources that block GitHub's IP ranges are omitted; scrape.py preserves
    their existing events instead of dropping them.
    """
    runnable = [s for s in SOURCES if s.is_scraped and (s.runs_in_ci or not is_ci)]
    return iter(sorted(runnable, key=lambda s: KIND_ORDER[s.kind]))


def skipped_in_ci() -> list[str]:
    """Names whose events CI must preserve rather than re-scrape."""
    return [s.name for s in SOURCES if not s.runs_in_ci]


def preserved_always() -> list[str]:
    """Names never produced by the scrape pipeline; their events must survive."""
    return [s.name for s in SOURCES if not s.is_scraped]
