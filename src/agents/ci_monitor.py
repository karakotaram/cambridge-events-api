"""Agent 1: CI Monitor - Track source freshness and detect stale/missing sources"""
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from dateutil import parser as dateparse

from src.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

# All registered scrapers with CI availability flag
# ci=True means the scraper runs in GitHub Actions; ci=False means local-only
REGISTERED_SOURCES = {
    # Non-Selenium scrapers (source_name values must match scraper definitions exactly)
    "Lamplighter Brewing": {"ci": True},
    "Harvard Book Store": {"ci": False},
    "Boston Swing Central": {"ci": False},
    "The Comedy Studio": {"ci": True},
    "The Dance Complex": {"ci": True},
    "BostonShows.org": {"ci": True},
    "Theatre at First": {"ci": True},
    "First Parish in Cambridge": {"ci": True},
    "Harvard Art Museums": {"ci": True},
    "Brattle Theatre": {"ci": True},
    "Grolier Poetry Book Shop": {"ci": True},
    "Multicultural Arts Center": {"ci": True},
    "The Rockwell": {"ci": True},
    "The Mad Monkfish": {"ci": True},
    "Mount Auburn Cemetery": {"ci": True},
    "Harvard Athletics": {"ci": True},
    # Selenium scrapers
    "City of Cambridge": {"ci": True},
    "The Lily Pad": {"ci": True},
    "The Middle East": {"ci": True},
    "Portico Brewing": {"ci": True},
    "Porter Square Books": {"ci": True},
    "Arts at the Armory": {"ci": True},
    "Harvard-Radcliffe Dramatic Club": {"ci": True},
    "Central Square Theater": {"ci": True},
    "Sanders Theatre": {"ci": True},
    "American Repertory Theater": {"ci": True},
    "Aeronaut Brewing": {"ci": False},
    "Somerville Theatre": {"ci": False},
    # Playwright scrapers
    "Longy School of Music": {"ci": True},
    "MIT Events": {"ci": True},
    "MIT Music & Theater": {"ci": True},
    "Harvard Memorial Church": {"ci": True},
    "Cambridge Public Library": {"ci": True},
    "MIT Open Space": {"ci": True},
    "Skip the Small Talk": {"ci": True},
    # Other scrapers
    "Longfellow House": {"ci": True},
    # Aggregator
    "Harvard Square": {"ci": True},
    # User-submitted (always preserved)
    "User Submitted": {"ci": True},
}

# Staleness thresholds
CI_STALE_DAYS = 3
LOCAL_STALE_DAYS = 14


class CIMonitorAgent(BaseAgent):
    """Monitor source freshness and detect stale/missing sources"""

    def __init__(self):
        super().__init__("ci_monitor")

    def execute(self) -> dict:
        events = self.load_events()
        if not events:
            return {"status": "error", "error": "No events loaded"}

        # Group events by source, find latest scraped_at per source
        source_stats = defaultdict(lambda: {"count": 0, "latest_scraped": None})
        for event in events:
            source = event.get("source_name", "Unknown")
            source_stats[source]["count"] += 1
            scraped_at = event.get("scraped_at")
            if scraped_at:
                try:
                    dt = dateparse.parse(scraped_at)
                    if dt.tzinfo:
                        dt = dt.replace(tzinfo=None)
                    current_latest = source_stats[source]["latest_scraped"]
                    if current_latest is None or dt > current_latest:
                        source_stats[source]["latest_scraped"] = dt
                except (ValueError, TypeError):
                    pass

        now = datetime.utcnow()
        stale_sources = []
        missing_sources = []
        healthy_sources = []

        for source_name, config in REGISTERED_SOURCES.items():
            stats = source_stats.get(source_name)

            if not stats or stats["count"] == 0:
                missing_sources.append({
                    "source": source_name,
                    "ci": config["ci"],
                    "issue": "No events in data",
                })
                continue

            latest = stats["latest_scraped"]
            threshold_days = CI_STALE_DAYS if config["ci"] else LOCAL_STALE_DAYS

            if latest is None:
                stale_sources.append({
                    "source": source_name,
                    "ci": config["ci"],
                    "event_count": stats["count"],
                    "issue": "No scraped_at timestamps",
                })
            elif (now - latest).days > threshold_days:
                stale_sources.append({
                    "source": source_name,
                    "ci": config["ci"],
                    "event_count": stats["count"],
                    "days_stale": (now - latest).days,
                    "latest_scraped": latest.isoformat(),
                    "threshold_days": threshold_days,
                    "issue": f"Last scraped {(now - latest).days} days ago (threshold: {threshold_days})",
                })
            else:
                healthy_sources.append({
                    "source": source_name,
                    "event_count": stats["count"],
                    "days_since_scrape": (now - latest).days,
                })

        # Detect unregistered sources in the data
        unregistered = []
        for source_name in source_stats:
            if source_name not in REGISTERED_SOURCES:
                unregistered.append({
                    "source": source_name,
                    "event_count": source_stats[source_name]["count"],
                })

        report = {
            "status": "ok",
            "timestamp": now.isoformat(),
            "total_events": len(events),
            "total_sources_registered": len(REGISTERED_SOURCES),
            "total_sources_with_events": len(source_stats),
            "healthy": healthy_sources,
            "stale": stale_sources,
            "missing": missing_sources,
            "unregistered": unregistered,
            "summary": {
                "healthy": len(healthy_sources),
                "stale": len(stale_sources),
                "missing": len(missing_sources),
                "unregistered": len(unregistered),
            },
        }

        self.save_report(report, "ci_monitor_report.json")

        # Create GitHub issue if there are problems
        if stale_sources or missing_sources:
            self._create_issue(stale_sources, missing_sources)

        return report

    def _create_issue(self, stale: list, missing: list):
        """Create a GitHub issue for stale/missing sources"""
        title = f"CI Monitor - {len(stale)} stale, {len(missing)} missing sources"

        lines = ["## CI Monitor Report\n"]

        if missing:
            lines.append("### Missing Sources (0 events)\n")
            for s in missing:
                ci_label = "CI" if s["ci"] else "Local-only"
                lines.append(f"- **{s['source']}** ({ci_label}): {s['issue']}")
            lines.append("")

        if stale:
            lines.append("### Stale Sources\n")
            for s in stale:
                ci_label = "CI" if s["ci"] else "Local-only"
                lines.append(f"- **{s['source']}** ({ci_label}): {s['issue']}")
            lines.append("")

        lines.append("---")
        lines.append(f"*Generated by CI Monitor Agent at {datetime.utcnow().isoformat()}*")

        self.create_github_issue(title, "\n".join(lines))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    agent = CIMonitorAgent()
    result = agent.run()
    print(f"\nCI Monitor: {result['summary']}")
    if result.get("stale"):
        print("\nStale sources:")
        for s in result["stale"]:
            print(f"  - {s['source']}: {s['issue']}")
    if result.get("missing"):
        print("\nMissing sources:")
        for s in result["missing"]:
            print(f"  - {s['source']}: {s['issue']}")
