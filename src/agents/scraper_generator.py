"""Agent 5: Scraper Generator - Multi-step investigation + code generation via Anthropic"""
import argparse
import ast
import importlib.util
import io
import json
import logging
import os
import re
import tempfile
from datetime import datetime
from typing import Optional, Tuple
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from src.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

# Event-like keys we look for in JSON data
EVENT_KEYS = {"title", "name", "event", "date", "start", "time", "location", "venue"}

# Patterns that suggest an API endpoint in JS source
API_PATTERNS = [
    r"""(?:fetch|axios\.get|axios\.post|\$\.(?:get|getJSON|ajax|post)|XMLHttpRequest)\s*\(\s*['"`]([^'"`\s]+)['"`]""",
    r"""['"`](\/(?:api|services|rest|v[12]|graphql|calendar|events?)\b[^'"`\s]*)['"`]""",
    r"""['"`](https?://[^'"`\s]*(?:api|services|calendar|events?|\.ashx|\.json)[^'"`\s]*)['"`]""",
    r"""url\s*[:=]\s*['"`]([^'"`\s]+\.(?:ashx|json|xml))['"`]""",
    r"""data-(?:url|api|endpoint|src)\s*=\s*['"`]([^'"`\s]+)['"`]""",
]

# Patterns for embedded JSON data
EMBEDDED_JSON_PATTERNS = [
    (r"var\s+initialEvents\s*=\s*\[\]\.concat\((\[.*?\])\);", "initialEvents"),
    (r"var\s+\w*[Ee]vents?\w*\s*=\s*(\[[\s\S]*?\]);\s*(?:var|let|const|function|//|$)", "varEvents"),
    (r"window\.__NEXT_DATA__\s*=\s*(\{.*?\});\s*</script>", "nextData"),
    (r"window\.__DATA__\s*=\s*(\{.*?\});\s*</script>", "windowData"),
    (r"window\.\w+\s*=\s*(\{[^}]*['\"]events?['\"][^}]*\})", "windowObj"),
]


def _has_event_keys(obj) -> bool:
    """Check if a dict or list-of-dicts contains event-like fields."""
    if isinstance(obj, dict):
        keys_lower = {k.lower() for k in obj.keys()}
        return len(keys_lower & EVENT_KEYS) >= 2
    if isinstance(obj, list) and obj:
        return _has_event_keys(obj[0]) if isinstance(obj[0], dict) else False
    return False


def _extract_event_items(data) -> list:
    """Recursively find the list of event-like dicts inside nested JSON."""
    if isinstance(data, list):
        if data and isinstance(data[0], dict) and _has_event_keys(data[0]):
            return data
        for item in data:
            result = _extract_event_items(item)
            if result:
                return result
    elif isinstance(data, dict):
        # Check if this dict itself has an events-like key
        for key in data:
            if re.search(r"events?", key, re.I):
                val = data[key]
                if isinstance(val, list) and val and isinstance(val[0], dict):
                    return val
        # Recurse into values
        for val in data.values():
            result = _extract_event_items(val)
            if result:
                return result
    return []


class ScraperGeneratorAgent(BaseAgent):
    """Generate new scraper code from a venue URL using multi-step investigation + Anthropic Claude"""

    def __init__(self):
        super().__init__("scraper_generator")
        self._args = None

    def execute(self) -> dict:
        if not self._args:
            return {"status": "error", "error": "No URL provided. Use --url and --venue flags."}

        url = self._args.url
        venue = self._args.venue
        write = self._args.write
        dry_run = self._args.dry_run

        # Step 1: Investigate the page
        investigation = self._investigate(url)
        investigation["venue_name"] = venue

        if dry_run:
            investigation.pop("_raw_html", None)
            report = {
                "status": "dry_run",
                "investigation": investigation,
            }
            self._print_investigation(investigation)
            self.save_report(report, "scraper_generator_report.json")
            return report

        # Step 2: Generate scraper code with Anthropic
        if not self.anthropic_client:
            investigation.pop("_raw_html", None)
            return {
                "status": "error",
                "error": "ANTHROPIC_API_KEY not set. Use --dry-run for investigation only.",
                "investigation": investigation,
            }

        # Step 3: Generate + validate with progressive retries
        code, validation = self._generate_with_retries(investigation, url, venue)

        investigation.pop("_raw_html", None)

        if not code:
            return {"status": "error", "error": "Failed to generate scraper code", "investigation": investigation}

        report = {
            "status": "ok",
            "investigation": investigation,
            "code_valid": validation["valid"],
            "validation": validation,
            "timestamp": datetime.utcnow().isoformat(),
        }

        # Step 4: Optionally write to file
        if write and validation["valid"]:
            filepath = self.write_scraper(venue, code)
            report["written_to"] = filepath
        elif write and not validation["valid"]:
            report["write_skipped"] = "Validation failed"

        report["code"] = code
        self.save_report(report, "scraper_generator_report.json")
        return report

    # ------------------------------------------------------------------
    # Public helpers
    # ------------------------------------------------------------------

    def write_scraper(self, venue: str, code: str) -> str:
        """Write scraper code to src/scrapers/{venue}.py. Returns filepath."""
        filename = self._venue_to_filename(venue)
        filepath = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "scrapers", filename
        )
        with open(filepath, "w") as f:
            f.write(code)
        self.logger.info(f"Scraper written to {filepath}")
        return filepath

    # ------------------------------------------------------------------
    # Public pipeline method for source_discovery integration
    # ------------------------------------------------------------------

    def generate_from_suggestion(self, name: str, url: str) -> dict:
        """End-to-end: investigate → generate → validate for one venue.

        Returns dict with keys: status, investigation, code, validation, venue, url
        """
        investigation = self._investigate(url)
        investigation["venue_name"] = name

        if not self.anthropic_client:
            investigation.pop("_raw_html", None)
            return {
                "status": "skipped",
                "reason": "ANTHROPIC_API_KEY not set",
                "investigation": investigation,
                "venue": name,
                "url": url,
            }

        code, validation = self._generate_with_retries(investigation, url, name)

        investigation.pop("_raw_html", None)

        if not code:
            return {
                "status": "error",
                "error": "Code generation failed",
                "investigation": investigation,
                "venue": name,
                "url": url,
            }

        return {
            "status": "ok" if validation["valid"] else "error",
            "investigation": investigation,
            "code": code,
            "validation": validation,
            "venue": name,
            "url": url,
        }

    # ------------------------------------------------------------------
    # Investigation pipeline (no LLM — pure code)
    # ------------------------------------------------------------------

    def _investigate(self, url: str) -> dict:
        """Orchestrate all investigation steps and pick the best data source."""
        result = {
            "url": url,
            "best_source": None,
            "apis": [],
            "embedded_json": [],
            "feeds": [],
            "static_html": {},
            "fetch_error": None,
        }

        html, headers = self._fetch_page(url)
        if html is None:
            result["fetch_error"] = headers  # headers holds the error string
            return result

        result["_raw_html"] = html
        base_url = f"{urlparse(url).scheme}://{urlparse(url).netloc}"

        # Run all investigation steps
        result["apis"] = self._discover_apis(html, base_url)
        result["embedded_json"] = self._extract_embedded_json(html)
        result["feeds"] = self._detect_feeds(html, base_url)

        soup = BeautifulSoup(html, "html.parser")
        result["static_html"] = self._assess_static_html(soup)
        result["json_ld"] = self._extract_json_ld(soup)

        # If page needs JS and we found nothing useful, retry with Playwright
        needs_js = result["static_html"].get("needs_js", False)
        has_data = (
            result["apis"]
            or result["embedded_json"]
            or result.get("json_ld")
            or result["feeds"]
        )
        if needs_js and not has_data:
            self.logger.info("Page needs JS rendering — retrying with Playwright...")
            pw_html, pw_err = self._fetch_page_playwright(url)
            if pw_html:
                result["used_playwright"] = True
                result["_raw_html"] = pw_html
                result["apis"] = self._discover_apis(pw_html, base_url)
                result["embedded_json"] = self._extract_embedded_json(pw_html)
                result["feeds"] = self._detect_feeds(pw_html, base_url)
                soup = BeautifulSoup(pw_html, "html.parser")
                result["static_html"] = self._assess_static_html(soup)
                result["json_ld"] = self._extract_json_ld(soup)
            elif pw_err:
                self.logger.warning(f"Playwright fallback failed: {pw_err}")

        # Pick best source by priority
        if result["apis"]:
            best = result["apis"][0]
            result["best_source"] = {
                "type": "api",
                "url": best["url"],
                "sample": best["sample"][:3],
                "total_items": best["item_count"],
            }
        elif result["embedded_json"]:
            best = result["embedded_json"][0]
            result["best_source"] = {
                "type": "embedded_json",
                "pattern": best["pattern_name"],
                "sample": best["sample"][:3],
                "total_items": best["item_count"],
            }
        elif result["json_ld"]:
            result["best_source"] = {
                "type": "json_ld",
                "sample": result["json_ld"][:3],
                "total_items": len(result["json_ld"]),
            }
        elif result["feeds"]:
            best = result["feeds"][0]
            result["best_source"] = {
                "type": "feed",
                "url": best["url"],
                "feed_type": best["type"],
            }
        elif result["static_html"].get("event_containers"):
            result["best_source"] = {
                "type": "static_html",
                "containers": result["static_html"]["event_containers"][:3],
                "has_pagination": result["static_html"].get("has_pagination", False),
            }

        return result

    def _fetch_page(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """Fetch raw HTML. Returns (html, None) on success or (None, error) on failure."""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            }
            response = requests.get(url, timeout=15, headers=headers)
            response.raise_for_status()
            return response.text, None
        except requests.RequestException as e:
            return None, str(e)

    def _fetch_page_playwright(self, url: str) -> Tuple[Optional[str], Optional[str]]:
        """Fetch page with Playwright for JS-rendered sites."""
        try:
            from playwright.sync_api import sync_playwright

            pw = sync_playwright().start()
            try:
                browser = pw.chromium.launch(
                    headless=True,
                    args=[
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-gpu",
                        "--disable-extensions",
                        "--disable-software-rasterizer",
                    ],
                )
                context = browser.new_context(
                    viewport={"width": 1920, "height": 1080},
                    user_agent=(
                        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    java_script_enabled=True,
                    bypass_csp=True,
                    extra_http_headers={
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                        "DNT": "1",
                    },
                )
                context.route(
                    "**/*.{png,jpg,jpeg,gif,svg,ico,woff,woff2}",
                    lambda route: route.abort(),
                )
                page = context.new_page()
                page.goto(url, wait_until="networkidle", timeout=30000)
                page.wait_for_timeout(2000)
                html = page.content()
                page.close()
                context.close()
                browser.close()
            finally:
                pw.stop()

            return html, None
        except Exception as e:
            return None, f"Playwright error: {e}"

    def _discover_apis(self, html: str, base_url: str) -> list:
        """Search HTML/JS for API endpoints, test each for JSON with event data."""
        candidate_urls = set()

        for pattern in API_PATTERNS:
            for match in re.finditer(pattern, html):
                raw_url = match.group(1)
                # Resolve relative URLs
                if raw_url.startswith("/"):
                    raw_url = base_url + raw_url
                elif not raw_url.startswith("http"):
                    continue
                candidate_urls.add(raw_url)

        # Also extract JS param objects near API URLs for parameterized endpoints
        param_sets = self._extract_api_params(html, candidate_urls, base_url)

        apis = []
        tested = set()
        for api_url in candidate_urls:
            # First try with extracted params
            if api_url in param_sets:
                for params in param_sets[api_url]:
                    url_with_params = api_url
                    key = (api_url, frozenset(params.items()))
                    if key in tested:
                        continue
                    tested.add(key)
                    result = self._test_api(api_url, params=params)
                    if result:
                        apis.append(result)

            # Then try bare URL
            if api_url not in tested:
                tested.add(api_url)
                result = self._test_api(api_url)
                if result:
                    apis.append(result)

        # Sort by item count (most items = most likely the events API)
        apis.sort(key=lambda x: x["item_count"], reverse=True)
        return apis

    def _extract_api_params(self, html: str, urls: set, base_url: str) -> dict:
        """Extract query parameters from JS source near API endpoint calls.

        For patterns like $.getJSON("/api/endpoint", {type: 'events', sport: 0}),
        extract the params object and resolve dynamic values to sensible defaults.
        """
        param_sets = {}

        for url in urls:
            # Get the relative path for matching in JS
            path = url.replace(base_url, "") if url.startswith(base_url) else url

            # Find the JS call context around this URL
            escaped_path = re.escape(path)
            # Match: getJSON("url", {key: 'value', ...})  or  fetch("url", {body: ...})
            param_pattern = (
                escaped_path
                + r"""['"`]\s*,\s*\{([^}]{5,200})\}"""
            )
            for match in re.finditer(param_pattern, html):
                raw_params = match.group(1)
                params = self._parse_js_params(raw_params)
                if params:
                    param_sets.setdefault(url, []).append(params)

        return param_sets

    def _parse_js_params(self, raw: str) -> dict:
        """Parse a JS object literal into a dict, resolving dynamic values to defaults."""
        params = {}
        # Match all key: value pairs in the JS object
        # This captures: key: 'string', key: "string", key: number, key: variable/expr
        for match in re.finditer(r"""(\w+)\s*:\s*(?:['"]([^'"]*)['"]\s*[,}]|(\d+)\s*[,}])""", raw):
            key = match.group(1)
            value = match.group(2) if match.group(2) is not None else match.group(3)
            if value is not None:
                params[key] = value

        # Find all keys (including those with dynamic values like variables/function calls)
        all_keys = re.findall(r"(\w+)\s*:", raw)
        for key in all_keys:
            if key not in params:
                # Apply sensible defaults for common calendar param names
                if key == "date":
                    params[key] = datetime.now().strftime("%-m/%-d/%Y")
                elif key in ("sport", "category", "page"):
                    params[key] = "0"  # Common "all" default for numeric filters
                else:
                    params[key] = ""

        # Common defaults for calendar APIs
        if "type" not in params and any(k in raw.lower() for k in ["event", "calendar"]):
            params["type"] = "events"
        return params

    def _test_api(self, url: str, params: dict = None) -> Optional[dict]:
        """Test a URL for JSON response containing event-like data."""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                "Accept": "application/json, */*",
            }
            resp = requests.get(url, params=params, timeout=10, headers=headers)
            if resp.status_code != 200:
                return None

            content_type = resp.headers.get("Content-Type", "")
            # Try to parse as JSON regardless of content-type
            try:
                data = resp.json()
            except (json.JSONDecodeError, ValueError):
                return None

            if not data:
                return None

            # Find event-like items
            items = _extract_event_items(data)
            if not items:
                return None

            # Record the actual URL used (with params) for the LLM prompt
            actual_url = resp.url

            return {
                "url": actual_url,
                "item_count": len(items),
                "sample": items[:5],
                "keys": list(items[0].keys()) if items else [],
                "params": params,
            }
        except requests.RequestException:
            return None

    def _extract_embedded_json(self, html: str) -> list:
        """Find embedded JSON in script tags (var X = {...}, window.__DATA__, etc.)."""
        results = []

        for pattern, name in EMBEDDED_JSON_PATTERNS:
            for match in re.finditer(pattern, html, re.DOTALL):
                raw = match.group(1)
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    # Try fixing common JS-to-JSON issues
                    try:
                        # Replace single quotes, trailing commas
                        fixed = re.sub(r"'", '"', raw)
                        fixed = re.sub(r",\s*([}\]])", r"\1", fixed)
                        data = json.loads(fixed)
                    except json.JSONDecodeError:
                        continue

                items = _extract_event_items(data)
                if items:
                    results.append({
                        "pattern_name": name,
                        "item_count": len(items),
                        "sample": items[:5],
                        "keys": list(items[0].keys()) if items else [],
                    })

        # Sort by item count
        results.sort(key=lambda x: x["item_count"], reverse=True)
        return results

    def _extract_json_ld(self, soup: BeautifulSoup) -> list:
        """Extract JSON-LD Event objects from script tags."""
        events = []
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string)
                if isinstance(data, dict):
                    if data.get("@type") == "Event":
                        events.append(data)
                    elif data.get("@type") == "ItemList":
                        for item in data.get("itemListElement", []):
                            if isinstance(item, dict) and item.get("@type") == "Event":
                                events.append(item)
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict) and item.get("@type") == "Event":
                            events.append(item)
            except (json.JSONDecodeError, TypeError):
                continue
        return events

    def _detect_feeds(self, html: str, base_url: str) -> list:
        """Find iCal, RSS, and Atom feeds."""
        feeds = []

        soup = BeautifulSoup(html, "html.parser")

        # RSS/Atom link tags
        for link in soup.find_all("link", rel=re.compile(r"alternate", re.I)):
            link_type = (link.get("type") or "").lower()
            href = link.get("href", "")
            if not href:
                continue
            if href.startswith("/"):
                href = base_url + href

            if "rss" in link_type or "atom" in link_type:
                feeds.append({"url": href, "type": "rss" if "rss" in link_type else "atom"})

        # iCal links in HTML
        ical_patterns = [
            r'href=["\']([^"\']*\.ics)["\']',
            r'href=["\'](webcal://[^"\']+)["\']',
            r'(https?://[^\s"\'<>]+\.ics)',
        ]
        for pattern in ical_patterns:
            for match in re.finditer(pattern, html):
                url = match.group(1)
                if url.startswith("/"):
                    url = base_url + url
                feeds.append({"url": url, "type": "ical"})

        return feeds

    def _assess_static_html(self, soup: BeautifulSoup) -> dict:
        """Assess static HTML structure as fallback data source."""
        result = {
            "page_title": "",
            "event_containers": [],
            "has_pagination": False,
            "needs_js": False,
            "date_patterns": [],
        }

        result["page_title"] = soup.title.string.strip() if soup.title and soup.title.string else ""

        # Detect JS-rendering signals
        js_signals = [
            soup.find("div", id="__next"),
            soup.find("div", id="root"),
            soup.find("div", id="app"),
            soup.find("script", src=re.compile(r"webpack|bundle|chunk")),
        ]
        result["needs_js"] = any(js_signals)

        # Detect event-like containers
        event_patterns = re.compile(r"event|listing|calendar|show|performance", re.I)
        containers = soup.find_all(
            ["div", "article", "section", "li"],
            class_=event_patterns,
            limit=10,
        )
        result["event_containers"] = [
            {"tag": c.name, "class": " ".join(c.get("class", []))}
            for c in containers
        ]

        # Detect pagination
        pagination_patterns = re.compile(r"page|pager|pagination|next|load.?more", re.I)
        if soup.find(["nav", "div", "a", "button"], class_=pagination_patterns):
            result["has_pagination"] = True
        if soup.find("a", string=re.compile(r"next|more events|load more", re.I)):
            result["has_pagination"] = True

        # Sample date-like strings from the page
        text = soup.get_text()
        date_re = re.compile(
            r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
            r"Jul(?:y)?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
            r"\s+\d{1,2}(?:,?\s*\d{4})?",
            re.I,
        )
        result["date_patterns"] = date_re.findall(text)[:10]

        return result

    # ------------------------------------------------------------------
    # Code generation (Anthropic)
    # ------------------------------------------------------------------

    def _generate_scraper(
        self, investigation: dict, retry_error: str = None,
        previous_code: str = None, html_sample: str = None,
    ) -> Optional[str]:
        """Generate scraper code using Anthropic Claude with actual data samples."""
        # Load reference files
        scrapers_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "scrapers")
        models_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "models")

        used_playwright = investigation.get("used_playwright", False)

        base_scraper_code = self._read_file(os.path.join(scrapers_dir, "base_scraper.py"))
        model_code = self._read_file(os.path.join(models_dir, "event.py"))

        # If Playwright is needed, also load the Playwright base class
        if used_playwright:
            pw_base_code = self._read_file(os.path.join(scrapers_dir, "base_playwright_scraper.py"))

        # Pick reference scraper based on data source type
        best = investigation.get("best_source") or {}
        source_type = best.get("type", "static_html")
        ref_scraper_name = self._select_reference_scraper(source_type, used_playwright)
        ref_code = self._read_file(os.path.join(scrapers_dir, f"{ref_scraper_name}.py"))

        # Build the data source description
        source_desc = self._build_source_description(investigation)

        if used_playwright:
            base_class_name = "BasePlaywrightScraper"
            base_class_section = (
                f"## BasePlaywrightScraper class to extend\n```python\n{pw_base_code}\n```\n"
            )
            extend_instruction = (
                f"1. Extend BasePlaywrightScraper (this site requires JS rendering via Playwright)\n"
            )
        else:
            base_class_name = "BaseScraper"
            base_class_section = (
                f"## BaseScraper class to extend\n```python\n{base_scraper_code}\n```\n"
            )
            extend_instruction = (
                f"1. Extend BaseScraper (use_selenium=False unless the data source requires JS)\n"
            )

        prompt_parts = [
            f"Generate a Python scraper for the venue '{investigation['venue_name']}' "
            f"at URL: {investigation['url']}\n",
            f"## Discovered Data Source\n{source_desc}\n",
            base_class_section,
            f"## EventCreate model\n```python\n{model_code}\n```\n",
            f"## Reference scraper ({ref_scraper_name}.py) — follow this pattern\n```python\n{ref_code}\n```\n",
            "## Requirements\n"
            f"{extend_instruction}"
            f"2. Implement scrape_events() -> List[EventCreate]\n"
            f"3. Set source_name='{investigation['venue_name']}'\n"
            "4. Parse dates properly using dateutil.parser\n"
            "5. Handle errors gracefully with try/except per event\n"
            "6. Include proper imports\n"
            "7. Return ONLY the Python code, no markdown fences\n"
            "8. The scraper should work immediately when called — use the exact API URL, JSON path, or HTML selectors discovered above\n",
        ]

        if previous_code:
            prompt_parts.append(
                f"\n## PREVIOUS CODE (your last attempt)\n"
                f"```python\n{previous_code}\n```\n"
                "Identify the specific bug and fix it. Keep the overall structure.\n"
            )

        if retry_error:
            prompt_parts.append(
                f"\n## PREVIOUS ATTEMPT FAILED\n"
                f"Error details:\n```\n{retry_error[:3000]}\n```\n"
                "Fix the issue and generate working code.\n"
            )

        if html_sample:
            prompt_parts.append(
                f"\n## Raw HTML Sample (actual event containers from the page)\n"
                f"```html\n{html_sample}\n```\n"
                "Use this HTML to verify your CSS selectors / tag names match the actual page structure.\n"
            )

        prompt = "\n".join(prompt_parts)

        system = (
            "You are an expert Python web scraping developer. Generate clean, "
            "production-ready scraper code. Return ONLY valid Python code with "
            "no markdown formatting or code fences."
        )

        response = self.llm_complete(prompt, system=system, provider="anthropic")
        if not response:
            return None

        # Clean up response — remove markdown fences if present
        code = response.strip()
        if code.startswith("```"):
            lines = code.split("\n")
            lines = [line for line in lines if not line.strip().startswith("```")]
            code = "\n".join(lines)

        return code

    def _select_reference_scraper(self, source_type: str, used_playwright: bool = False) -> str:
        """Pick the best reference scraper based on data source type."""
        if used_playwright:
            return "mit_calendar"
        mapping = {
            "api": "harvard_athletics",
            "embedded_json": "harvard_art_museums",
            "json_ld": "harvard_art_museums",
            "feed": "lamplighter",
            "static_html": "lamplighter",
        }
        return mapping.get(source_type, "lamplighter")

    def _build_source_description(self, investigation: dict) -> str:
        """Build a concise description of the discovered data source for the LLM prompt."""
        best = investigation.get("best_source")
        if not best:
            return "No structured data source found. Fall back to static HTML parsing.\n"

        lines = [f"**Type**: {best['type']}\n"]

        if best["type"] == "api":
            lines.append(f"**API URL**: {best['url']}")
            lines.append(f"**Total items**: {best['total_items']}")
            lines.append(f"**Sample data** (first {len(best['sample'])} items):")
            lines.append(f"```json\n{json.dumps(best['sample'], indent=2, default=str)[:4000]}\n```")

        elif best["type"] == "embedded_json":
            lines.append(f"**Extraction pattern**: {best['pattern']}")
            lines.append(f"**Total items**: {best['total_items']}")
            lines.append(f"**Sample data** (first {len(best['sample'])} items):")
            lines.append(f"```json\n{json.dumps(best['sample'], indent=2, default=str)[:4000]}\n```")

        elif best["type"] == "json_ld":
            lines.append(f"**Total JSON-LD Event objects**: {best['total_items']}")
            lines.append(f"**Sample data**:")
            lines.append(f"```json\n{json.dumps(best['sample'], indent=2, default=str)[:4000]}\n```")

        elif best["type"] == "feed":
            lines.append(f"**Feed URL**: {best['url']}")
            lines.append(f"**Feed type**: {best['feed_type']}")

        elif best["type"] == "static_html":
            lines.append(f"**Event containers found**: {len(best.get('containers', []))}")
            for c in best.get("containers", []):
                lines.append(f"  - <{c['tag']} class=\"{c['class']}\">")
            lines.append(f"**Has pagination**: {best.get('has_pagination', False)}")
            # Include date patterns from the page
            static = investigation.get("static_html", {})
            if static.get("date_patterns"):
                lines.append(f"**Sample dates on page**: {static['date_patterns'][:5]}")

        return "\n".join(lines)

    def _read_file(self, path: str) -> str:
        """Read a file, return empty string if missing."""
        try:
            with open(path) as f:
                return f.read()
        except FileNotFoundError:
            return ""

    # ------------------------------------------------------------------
    # Validation (actually run the generated scraper)
    # ------------------------------------------------------------------

    def _validate_scraper(self, code: str, url: str, venue: str) -> dict:
        """Validate generated scraper by actually running it."""
        result = {"valid": False, "errors": [], "events_found": 0}

        # Step 1: Syntax check
        try:
            ast.parse(code)
        except SyntaxError as e:
            result["errors"].append(f"Syntax error: {e}")
            result["error"] = f"Syntax error: {e}"
            return result

        # Step 2: Basic structure checks
        if "BaseScraper" not in code and "BasePlaywrightScraper" not in code:
            result["errors"].append("Does not extend BaseScraper or BasePlaywrightScraper")
        if "def scrape_events" not in code:
            result["errors"].append("Missing scrape_events() method")
        if "EventCreate" not in code:
            result["errors"].append("Does not use EventCreate model")
        if result["errors"]:
            result["error"] = "; ".join(result["errors"])
            return result

        # Step 3: Actually import and run the scraper
        tmp = None
        log_capture = io.StringIO()
        log_handler = logging.StreamHandler(log_capture)
        log_handler.setLevel(logging.DEBUG)
        log_handler.setFormatter(logging.Formatter("%(name)s %(levelname)s: %(message)s"))
        root_logger = logging.getLogger()
        root_logger.addHandler(log_handler)
        try:
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".py", delete=False, dir=tempfile.gettempdir()
            )
            tmp.write(code)
            tmp.flush()
            tmp.close()

            spec = importlib.util.spec_from_file_location("generated_scraper", tmp.name)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find the scraper class (subclass of BaseScraper)
            scraper_class = None
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and attr_name != "BaseScraper"
                    and hasattr(attr, "scrape_events")
                ):
                    scraper_class = attr
                    break

            if not scraper_class:
                result["errors"].append("No scraper class found in generated code")
                result["error"] = "No scraper class found in generated code"
                return result

            # Instantiate and run
            scraper = scraper_class()
            events = scraper.scrape_events()

            result["events_found"] = len(events)
            if len(events) > 0:
                result["valid"] = True
                result["sample_titles"] = [e.title for e in events[:3]]
                self.logger.info(f"Validation passed: {len(events)} events found for {venue}")
            else:
                result["errors"].append("Scraper returned 0 events")
                result["error"] = "Scraper returned 0 events"

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            result["errors"].append(f"Runtime error: {error_msg}")
            result["error"] = error_msg
            self.logger.warning(f"Validation failed for {venue}: {error_msg}")
        finally:
            root_logger.removeHandler(log_handler)
            result["scraper_logs"] = log_capture.getvalue()
            log_capture.close()
            if tmp and os.path.exists(tmp.name):
                os.unlink(tmp.name)

        # On failure, add URL diagnostics
        if not result["valid"]:
            result["diagnostics"] = self._diagnose_url(url, code)

        return result

    # ------------------------------------------------------------------
    # Generation with retries
    # ------------------------------------------------------------------

    def _generate_with_retries(
        self, investigation: dict, url: str, venue: str, max_attempts: int = 3,
    ) -> Tuple[Optional[str], dict]:
        """Generate and validate a scraper with progressive debugging context.

        Returns (code, validation) — code may be None if all attempts fail.
        """
        best_source_type = (investigation.get("best_source") or {}).get("type", "static_html")
        code = None
        validation = {"valid": False, "error": "No attempts made"}

        for attempt in range(1, max_attempts + 1):
            self.logger.info(f"Generation attempt {attempt}/{max_attempts} for {venue}")

            if attempt == 1:
                # First attempt: normal generation, include HTML sample for static sources
                html_sample = None
                if best_source_type == "static_html":
                    html_sample = self._extract_html_sample(investigation)
                code = self._generate_scraper(investigation, html_sample=html_sample)
            elif attempt == 2:
                # Second attempt: include previous code + rich error context
                error_context = self._build_error_context(validation)
                code = self._generate_scraper(
                    investigation,
                    retry_error=error_context,
                    previous_code=code,
                )
            else:
                # Third attempt: previous code + error + always include HTML sample
                error_context = self._build_error_context(validation)
                html_sample = self._extract_html_sample(investigation)
                if not html_sample:
                    html_sample = self._fetch_html_sample(url)
                code = self._generate_scraper(
                    investigation,
                    retry_error=error_context,
                    previous_code=code,
                    html_sample=html_sample,
                )

            if not code:
                self.logger.warning(f"Code generation returned None on attempt {attempt}")
                continue

            validation = self._validate_scraper(code, url, venue)
            if validation["valid"]:
                return code, validation

            self.logger.info(
                f"Attempt {attempt} failed: {validation.get('error', 'unknown')}"
            )

        return code, validation

    # ------------------------------------------------------------------
    # HTML sampling
    # ------------------------------------------------------------------

    def _extract_html_sample(self, investigation: dict, max_chars: int = 2000) -> Optional[str]:
        """Extract raw HTML snippets of event containers from stored _raw_html."""
        raw_html = investigation.get("_raw_html")
        if not raw_html:
            return None

        soup = BeautifulSoup(raw_html, "html.parser")

        # Try to find event containers using classes from investigation
        containers = investigation.get("static_html", {}).get("event_containers", [])
        snippets = []
        for container_info in containers[:3]:
            cls = container_info.get("class", "")
            tag = container_info.get("tag", "div")
            if cls:
                # Search by first class name
                first_class = cls.split()[0]
                found = soup.find_all(tag, class_=re.compile(re.escape(first_class)), limit=2)
                for el in found:
                    snippet = str(el)[:max_chars // 2]
                    snippets.append(snippet)
            if snippets:
                break

        # Fallback: grab elements with common event-related classes
        if not snippets:
            event_re = re.compile(r"event|listing|calendar|show|performance", re.I)
            for el in soup.find_all(["div", "article", "section", "li"], class_=event_re, limit=2):
                snippets.append(str(el)[:max_chars // 2])

        if not snippets:
            return None

        result = "\n\n<!-- next event -->\n\n".join(snippets)
        return result[:max_chars]

    def _fetch_html_sample(self, url: str, max_chars: int = 2000) -> Optional[str]:
        """Quick fetch fallback for HTML sample when _raw_html wasn't stored."""
        try:
            resp = requests.get(
                url, timeout=10,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                },
            )
            if resp.status_code == 200:
                soup = BeautifulSoup(resp.text, "html.parser")
                event_re = re.compile(r"event|listing|calendar|show|performance", re.I)
                snippets = []
                for el in soup.find_all(["div", "article", "section", "li"], class_=event_re, limit=2):
                    snippets.append(str(el)[:max_chars // 2])
                if snippets:
                    return "\n\n<!-- next event -->\n\n".join(snippets)[:max_chars]
        except requests.RequestException:
            pass
        return None

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def _diagnose_url(self, url: str, code: str) -> str:
        """Independently fetch the URL and report HTTP-level diagnostics."""
        lines = []
        try:
            resp = requests.get(
                url, timeout=10,
                headers={
                    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                  "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
                },
            )
            lines.append(f"HTTP {resp.status_code}")
            lines.append(f"Content-Type: {resp.headers.get('Content-Type', 'unknown')}")
            lines.append(f"Body length: {len(resp.text)} chars")
            snippet = resp.text[:500].replace("\n", " ")
            lines.append(f"Body start: {snippet}")
        except requests.RequestException as e:
            lines.append(f"Fetch failed: {e}")

        # Check if the generated code references Playwright
        uses_pw = "playwright" in code.lower() or "BasePlaywrightScraper" in code
        if uses_pw:
            try:
                import playwright  # noqa: F401
                lines.append("Playwright: importable")
            except ImportError:
                lines.append("Playwright: NOT importable (missing dependency)")
        return "\n".join(lines)

    def _build_error_context(self, validation: dict) -> str:
        """Assemble a rich error string from the validation dict."""
        parts = []
        if validation.get("error"):
            parts.append(f"Error: {validation['error']}")
        if validation.get("scraper_logs"):
            log_text = validation["scraper_logs"].strip()
            if log_text:
                parts.append(f"Scraper logs:\n{log_text[:2000]}")
        if validation.get("diagnostics"):
            parts.append(f"URL diagnostics:\n{validation['diagnostics']}")
        return "\n\n".join(parts) if parts else "Unknown error"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _venue_to_filename(self, venue: str) -> str:
        """Convert venue name to Python filename."""
        name = venue.lower()
        name = re.sub(r"[^a-z0-9]+", "_", name)
        name = name.strip("_")
        return f"{name}.py"

    def _print_investigation(self, investigation: dict):
        """Pretty-print investigation results for --dry-run."""
        print(f"\n{'='*60}")
        print(f"Investigation: {investigation.get('venue_name', 'unknown')}")
        print(f"URL: {investigation['url']}")
        print(f"{'='*60}")

        best = investigation.get("best_source")
        if best:
            print(f"\nBest data source: {best['type'].upper()}")
            if best["type"] == "api":
                print(f"  API URL: {best['url']}")
                print(f"  Items found: {best['total_items']}")
            elif best["type"] in ("embedded_json", "json_ld"):
                print(f"  Items found: {best['total_items']}")
            elif best["type"] == "feed":
                print(f"  Feed URL: {best['url']}")
                print(f"  Type: {best['feed_type']}")
            elif best["type"] == "static_html":
                print(f"  Containers: {len(best.get('containers', []))}")

            if "sample" in best and best["sample"]:
                print(f"\n  Sample data (first item):")
                sample = best["sample"][0]
                print(f"  {json.dumps(sample, indent=4, default=str)[:1000]}")
        else:
            print("\nNo structured data source found.")

        # Summary of all findings
        print(f"\nAPIs discovered: {len(investigation.get('apis', []))}")
        for api in investigation.get("apis", []):
            print(f"  - {api['url']} ({api['item_count']} items)")

        print(f"Embedded JSON: {len(investigation.get('embedded_json', []))}")
        for ej in investigation.get("embedded_json", []):
            print(f"  - {ej['pattern_name']} ({ej['item_count']} items)")

        print(f"JSON-LD events: {len(investigation.get('json_ld', []))}")
        print(f"Feeds: {len(investigation.get('feeds', []))}")
        for feed in investigation.get("feeds", []):
            print(f"  - {feed['type']}: {feed['url']}")

        static = investigation.get("static_html", {})
        print(f"Static HTML containers: {len(static.get('event_containers', []))}")
        print(f"Needs JS: {static.get('needs_js', False)}")
        if investigation.get("used_playwright"):
            print(f"Used Playwright: True")
        print()


def main():
    parser = argparse.ArgumentParser(description="Generate a new scraper from a venue URL")
    parser.add_argument("--url", required=True, help="URL of the venue's event page")
    parser.add_argument("--venue", required=True, help="Venue name")
    parser.add_argument("--write", action="store_true", help="Write scraper to src/scrapers/")
    parser.add_argument("--dry-run", action="store_true", help="Investigate page only, no code generation")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    agent = ScraperGeneratorAgent()
    agent._args = args
    result = agent.run()

    if result.get("validation"):
        v = result["validation"]
        print(f"\nValidation: {'PASS' if v['valid'] else 'FAIL'}")
        if v.get("events_found"):
            print(f"  Events found: {v['events_found']}")
        if v.get("sample_titles"):
            for t in v["sample_titles"]:
                print(f"    - {t}")
        for err in v.get("errors", []):
            print(f"  Error: {err}")
        if result.get("written_to"):
            print(f"  Written to: {result['written_to']}")


if __name__ == "__main__":
    main()
