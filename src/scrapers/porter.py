"""Porter Square Books: an IndieCommerce calendar, read in a visible browser.

Retired 2026-10-06 when its Cloudflare settings refused headless Chromium and
plain HTTP; revived the same day reading the same page in an ordinary visible
browser, which it serves. See base_indiecommerce for how the page is read.

The store has a Cambridge and a Boston branch, and hosts events elsewhere, so
each event's city comes from its own address rather than being assumed.
"""
from src.scrapers.base_indiecommerce import BaseIndieCommerceScraper


class PorterSquareBooksScraper(BaseIndieCommerceScraper):
    def __init__(self):
        super().__init__(source_name="Porter Square Books", base="https://portersquarebooks.com")
