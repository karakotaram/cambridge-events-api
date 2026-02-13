"""Agent 4: Source Discovery - Find new Cambridge/Somerville venues with event calendars"""
import logging
import os
import re
from datetime import datetime

import requests

from src.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

EVENT_KEYWORDS = [
    "event", "calendar", "upcoming", "tickets", "schedule",
    "shows", "performances", "concerts", "classes", "workshops",
]


class SourceDiscoveryAgent(BaseAgent):
    """Discover new venue sources for the scraper system"""

    def __init__(self):
        super().__init__("source_discovery")

    def execute(self) -> dict:
        if not self.groq_client:
            return {"status": "skipped", "reason": "GROQ_API_KEY not set"}

        events = self.load_events()
        known_sources = sorted(set(e.get("source_name", "") for e in events))

        # Ask LLM for suggestions
        suggestions = self._get_suggestions(known_sources)
        if not suggestions:
            return {"status": "error", "error": "No suggestions from LLM"}

        # Validate each suggestion
        validated = []
        for suggestion in suggestions:
            result = self._validate_source(suggestion)
            suggestion["validation"] = result
            if result["has_events"]:
                validated.append(suggestion)

        # Auto-generate scrapers for validated venues (if ANTHROPIC_API_KEY is set)
        generation_results = []
        if validated and os.environ.get("ANTHROPIC_API_KEY"):
            generation_results = self._auto_generate_scrapers(validated)

        report = {
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "known_sources": len(known_sources),
            "suggestions": suggestions,
            "validated": validated,
            "generation_results": generation_results,
            "summary": {
                "total_suggested": len(suggestions),
                "validated": len(validated),
                "scrapers_generated": sum(
                    1 for r in generation_results if r.get("status") == "ok"
                ),
            },
        }

        self.save_report(report, "source_suggestions.json")

        if validated:
            self._create_issue(validated, generation_results)

        return report

    def _get_suggestions(self, known_sources: list) -> list:
        """Ask Groq for new venue suggestions"""
        known_list = "\n".join(f"- {s}" for s in known_sources if s)

        prompt = (
            "I'm building an event scraper for Cambridge and Somerville, Massachusetts. "
            "Here are the venues/sources I already scrape:\n\n"
            f"{known_list}\n\n"
            "Suggest 5-10 OTHER venues, cultural spaces, or event sources in "
            "Cambridge or Somerville, MA that have public event calendars or "
            "listings pages I could scrape. For each, provide:\n"
            "1. Name\n2. Website URL (the specific events/calendar page if possible)\n"
            "3. Type (music venue, theater, library, community space, etc.)\n\n"
            "Reply in this exact format, one per line:\n"
            "NAME | URL | TYPE\n\n"
            "Only include real venues with actual websites. No duplicates of my existing list."
        )

        response = self.llm_complete(
            prompt,
            system="You are an expert on Cambridge/Somerville, MA cultural venues. Only suggest real, existing venues with verifiable websites."
        )
        if not response:
            return []

        suggestions = []
        for line in response.strip().split("\n"):
            line = line.strip()
            if "|" not in line:
                continue
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 3:
                # Clean up numbering prefixes like "1. " or "- "
                name = re.sub(r"^\d+\.\s*", "", parts[0]).strip("- ")
                url = parts[1].strip()
                vtype = parts[2].strip()
                if name and url.startswith("http"):
                    suggestions.append({
                        "name": name,
                        "url": url,
                        "type": vtype,
                    })

        return suggestions

    def _validate_source(self, suggestion: dict) -> dict:
        """Fetch URL and check for event-like content"""
        url = suggestion["url"]
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                              "AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
            }
            response = requests.get(url, timeout=15, headers=headers)
            response.raise_for_status()
            html = response.text.lower()

            # Count event-related keywords
            keyword_hits = sum(1 for kw in EVENT_KEYWORDS if kw in html)
            has_events = keyword_hits >= 3

            return {
                "reachable": True,
                "status_code": response.status_code,
                "keyword_hits": keyword_hits,
                "has_events": has_events,
                "keywords_found": [kw for kw in EVENT_KEYWORDS if kw in html],
            }
        except requests.RequestException as e:
            return {
                "reachable": False,
                "error": str(e),
                "has_events": False,
            }

    def _auto_generate_scrapers(self, validated: list) -> list:
        """Attempt scraper generation for each validated venue."""
        from src.agents.scraper_generator import ScraperGeneratorAgent

        generator = ScraperGeneratorAgent()
        results = []
        for venue in validated:
            try:
                self.logger.info(f"Auto-generating scraper for {venue['name']}...")
                result = generator.generate_from_suggestion(venue["name"], venue["url"])
                results.append(result)
                status = result.get("status", "unknown")
                events = result.get("validation", {}).get("events_found", 0)
                self.logger.info(f"  {venue['name']}: {status} ({events} events)")
            except Exception as e:
                self.logger.warning(f"  {venue['name']}: generation failed: {e}")
                results.append({
                    "status": "error",
                    "error": str(e),
                    "venue": venue["name"],
                    "url": venue["url"],
                })
        return results

    def _create_issue(self, validated: list, generation_results: list = None):
        """Create GitHub issue with validated venue suggestions and generation results"""
        gen_ok = [r for r in (generation_results or []) if r.get("status") == "ok"]
        title = f"Source Discovery - {len(validated)} new venues"
        if gen_ok:
            title += f", {len(gen_ok)} scrapers generated"

        lines = ["## New Venue Suggestions\n"]
        lines.append("The following venues were validated as having event calendar pages:\n")

        # Build a lookup for generation results by venue name
        gen_by_venue = {}
        for r in (generation_results or []):
            gen_by_venue[r.get("venue", "")] = r

        for v in validated:
            validation = v.get("validation", {})
            keywords = ", ".join(validation.get("keywords_found", []))
            lines.append(f"### {v['name']}")
            lines.append(f"- **URL**: {v['url']}")
            lines.append(f"- **Type**: {v['type']}")
            lines.append(f"- **Event keywords found**: {keywords}")

            # Include generation results
            gen = gen_by_venue.get(v["name"])
            if gen:
                inv = gen.get("investigation", {})
                best = inv.get("best_source")
                if best:
                    lines.append(f"- **Data source**: {best['type']}")
                    if best.get("url"):
                        lines.append(f"- **API/Feed URL**: {best['url']}")
                    if best.get("total_items"):
                        lines.append(f"- **Items found**: {best['total_items']}")

                val = gen.get("validation", {})
                if gen["status"] == "ok":
                    lines.append(f"- **Scraper generated**: Yes ({val.get('events_found', 0)} events)")
                    if val.get("sample_titles"):
                        lines.append(f"- **Sample events**: {', '.join(val['sample_titles'][:3])}")
                else:
                    err = gen.get("error") or val.get("error", "unknown")
                    lines.append(f"- **Scraper generated**: No ({err})")
            lines.append("")

        # Include generated scraper code in a collapsed section
        if gen_ok:
            lines.append("## Generated Scraper Code\n")
            for r in gen_ok:
                lines.append(f"<details><summary>{r['venue']}</summary>\n")
                lines.append(f"```python\n{r.get('code', '')}\n```\n")
                lines.append("</details>\n")

        lines.append("---")
        lines.append(f"*Generated by Source Discovery Agent at {datetime.utcnow().isoformat()}*")

        self.create_github_issue(title, "\n".join(lines), assignee="karakotaram")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    agent = SourceDiscoveryAgent()
    result = agent.run()
    print(f"\nSource Discovery: {result.get('summary', result.get('reason', 'done'))}")
    for v in result.get("validated", []):
        print(f"  + {v['name']}: {v['url']}")
