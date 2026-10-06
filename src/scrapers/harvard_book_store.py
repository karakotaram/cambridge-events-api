"""Harvard Book Store: an IndieCommerce calendar, read in a visible browser.

Retired 2026-09-01 when harvard.com moved behind a Cloudflare interstitial that
refused plain HTTP and headless Chromium (the old scraper also spoofed a Safari
user-agent). Revived 2026-10-06 on the store's month calendar, which an
ordinary visible browser is served. See base_indiecommerce.
"""
from src.scrapers.base_indiecommerce import BaseIndieCommerceScraper


class HarvardBookStoreScraper(BaseIndieCommerceScraper):
    HOMES = {"Harvard Book Store": ("Harvard Book Store", "1256 Massachusetts Ave", "Cambridge", "02138")}

    def __init__(self):
        super().__init__(source_name="Harvard Book Store", base="https://www.harvard.com")
