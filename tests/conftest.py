"""Shared test fixtures.

The important one is `offline`: it makes any network call from a scraper raise,
so a test that accidentally reaches the internet fails loudly instead of quietly
becoming slow, flaky, and dependent on a venue's uptime.
"""
from __future__ import annotations

import gzip
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def read_fixture(module: str, name: str) -> str:
    """Read a saved page. Fixtures are gzipped; raw HTML would bloat the repo."""
    path = FIXTURES / module / name
    if not path.exists():
        pytest.skip(f"fixture missing: {path.relative_to(Path(__file__).parent.parent)} "
                    f"— capture with `cal scrape <source> --save-fixture`")
    data = path.read_bytes()
    if path.suffix == ".gz":
        data = gzip.decompress(data)
    return data.decode("utf-8", errors="replace")


@pytest.fixture
def fixture_html():
    return read_fixture


@pytest.fixture
def offline(monkeypatch):
    """Make any outbound HTTP call fail.

    Scrapers are meant to be pure — URL in, events out — so their tests should
    parse saved HTML and never touch a network. Anything that slips through
    should be an error, not a silent slowdown.
    """
    import requests
    import urllib.request

    def blocked(*args, **kwargs):
        raise AssertionError(
            "network call in a test — parse a fixture instead "
            "(see tests/conftest.py and docs/ROADMAP.md item 7)")

    monkeypatch.setattr(requests, "get", blocked)
    monkeypatch.setattr(requests, "post", blocked)
    monkeypatch.setattr(requests.Session, "request", blocked)
    monkeypatch.setattr(urllib.request, "urlopen", blocked)
