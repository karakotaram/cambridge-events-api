"""The Comedy Studio — schema.org JSON-LD on the home page.

`home-2026-10-06.html.gz` is the live page trimmed to its JSON-LD block:
163 shows, Oct 2026 to May 2027, in no particular order.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

from src.quality.invariants import check_invariants
from src.sources import BY_NAME
from tests.conftest import read_fixture


def _scrape(html: str):
    scraper = BY_NAME["The Comedy Studio"].load()
    scraper.fetch_html = lambda *a, **k: html          # type: ignore[method-assign]
    return scraper.scrape_events()


def test_every_show_in_the_listing_is_read(offline):
    """The scraper took `events[:30]`. The list is not in date order, so the
    30 it kept were an arbitrary slice of a season of 163 shows."""
    events = _scrape(read_fixture("comedy_studio", "home-2026-10-06.html.gz"))

    assert len(events) == 163
    assert min(e.start_datetime for e in events) == datetime(2026, 10, 6, 19, 0)
    assert max(e.start_datetime for e in events) == datetime(2027, 5, 22, 21, 30)
    # Offsets in the source (-04:00 / -05:00) become naive Eastern wall clock
    kill_tiny = [e for e in events if e.title == "Kill Tiny"]
    assert datetime(2026, 10, 22, 21, 30) in {e.start_datetime for e in kill_tiny}

    errors = [v for v in check_invariants([e.model_dump(mode="json") for e in events])
              if v.severity == "error"]
    assert not errors, "\n".join(str(v) for v in errors)


def test_a_show_without_its_own_image_uses_its_performers(offline):
    """`performer` is a list of Person objects on this site. The fallback
    only handled a single dict, so it never fired."""
    html = read_fixture("comedy_studio", "home-2026-10-06.html.gz")
    block = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL).group(1)
    data = json.loads(block)
    show = data["events"][0]
    performer_image = show["performer"][0]["image"]
    del show["image"]
    data["events"] = [show]

    events = _scrape(f'<script type="application/ld+json">{json.dumps(data)}</script>')

    assert len(events) == 1
    assert events[0].image_url == performer_image
