"""The Dance Complex — paging through the Tribe Events REST API.

The payload is the saved first page (`events-api-page1.json.gz`, 50 events).
Later pages are that page again with each event's URL made unique, so the
scraper's (url, start) de-duplication does not hide a page it actually read.
"""
from __future__ import annotations

import copy
import json
import logging

import pytest
import requests

from src.scrapers import dance_complex
from src.sources import BY_NAME
from tests.conftest import read_fixture

PAGE_ONE = json.loads(read_fixture("dance_complex", "events-api-page1.json.gz"))


def _serve(monkeypatch, total_pages: int) -> list:
    """Answer requests.get like the API would for `total_pages` pages."""
    requested = []

    def fake_get(url, params=None, **kwargs):
        page = params["page"]
        requested.append(page)
        body = copy.deepcopy(PAGE_ONE)
        body["total_pages"] = total_pages
        body["total"] = total_pages * len(body["events"])
        for item in body["events"]:
            item["url"] = f"{item['url']}?page={page}"
        if page > total_pages:
            return type("_R", (), {"status_code": 400})()     # Tribe past the end
        return type("_R", (), {"status_code": 200,
                               "raise_for_status": lambda s: None,
                               "json": lambda s: body})()

    monkeypatch.setattr(requests, "get", fake_get)
    return requested


def test_every_page_the_api_reports_is_read(monkeypatch):
    """MAX_PAGES was 20 x 50 = 1,000 events, and the 60-day window held 868 on
    2026-10-06 and rising. Past the cap the loop stopped as if it had reached
    the end. 25 pages must mean 25 requests and 1,250 events."""
    requested = _serve(monkeypatch, total_pages=25)

    events = BY_NAME["The Dance Complex"].load().scrape_events()

    assert requested == list(range(1, 26))
    assert len(events) == 25 * len(PAGE_ONE["events"])
    assert any(e.source_url.endswith("?page=25") for e in events)
    # (Invariants are not checked here: the repeated pages share start times
    # by construction. test_tribe_api_scrapers checks the real page.)


def test_hitting_the_sanity_cap_is_an_error(monkeypatch, caplog):
    """A cap that truncates must say so. The old loop just stopped."""
    total = dance_complex.MAX_PAGES + 5
    requested = _serve(monkeypatch, total_pages=total)

    with caplog.at_level(logging.ERROR, logger=dance_complex.__name__):
        events = BY_NAME["The Dance Complex"].load().scrape_events()

    assert requested == list(range(1, dance_complex.MAX_PAGES + 1))
    assert len(events) == dance_complex.MAX_PAGES * len(PAGE_ONE["events"])
    truncation = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert truncation and f"reports {total} pages" in truncation[0].getMessage()


@pytest.mark.parametrize("total_pages", [1, 3])
def test_a_complete_listing_logs_no_error(monkeypatch, caplog, total_pages):
    requested = _serve(monkeypatch, total_pages=total_pages)
    with caplog.at_level(logging.WARNING, logger=dance_complex.__name__):
        BY_NAME["The Dance Complex"].load().scrape_events()

    assert requested == list(range(1, total_pages + 1))
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
