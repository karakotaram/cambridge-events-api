"""Agent 2: Health Monitor - Detect broken scrapers using rolling history"""
import json
import logging
import os
from collections import defaultdict
from datetime import datetime

from src.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

HEALTH_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "scraper_health.json"
)
MAX_HISTORY = 5  # Keep last 5 runs


class HealthMonitorAgent(BaseAgent):
    """Detect broken scrapers by tracking event counts over time"""

    def __init__(self):
        super().__init__("health_monitor")

    def _load_health_history(self) -> dict:
        """Load rolling health history from disk"""
        if os.path.exists(HEALTH_FILE):
            try:
                with open(HEALTH_FILE, "r") as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
        return {"runs": []}

    def _save_health_history(self, history: dict):
        """Save health history, keeping only last MAX_HISTORY runs"""
        history["runs"] = history["runs"][-MAX_HISTORY:]
        os.makedirs(os.path.dirname(HEALTH_FILE), exist_ok=True)
        with open(HEALTH_FILE, "w") as f:
            json.dump(history, f, indent=2, default=str)

    def execute(self) -> dict:
        events = self.load_events()
        if not events:
            return {"status": "error", "error": "No events loaded"}

        # Count events per source
        current_counts = defaultdict(int)
        for event in events:
            current_counts[event.get("source_name", "Unknown")] += 1

        now = datetime.utcnow()
        current_run = {
            "timestamp": now.isoformat(),
            "counts": dict(current_counts),
        }

        # Load history and determine if this is baseline
        history = self._load_health_history()
        past_runs = history.get("runs", [])
        is_baseline = len(past_runs) == 0

        # Add current run to history
        history["runs"].append(current_run)
        self._save_health_history(history)

        if is_baseline:
            report = {
                "status": "baseline",
                "timestamp": now.isoformat(),
                "message": "First run - baseline recorded, no alerts",
                "sources": dict(current_counts),
                "alerts": [],
            }
            self.save_report(report, "health_monitor_report.json")
            return report

        # Calculate historical averages from past runs (excluding current)
        historical_totals = defaultdict(list)
        all_historical_sources = set()
        for run in past_runs:
            for source, count in run.get("counts", {}).items():
                historical_totals[source].append(count)
                all_historical_sources.add(source)

        alerts = []
        for source in set(list(current_counts.keys()) + list(all_historical_sources)):
            past_counts = historical_totals.get(source, [])
            current = current_counts.get(source, 0)

            if not past_counts:
                # New source, no history - skip
                continue

            avg = sum(past_counts) / len(past_counts)

            # CRITICAL: source had events before (avg > 5) but now has 0
            if current == 0 and avg > 5:
                alerts.append({
                    "source": source,
                    "severity": "CRITICAL",
                    "current": current,
                    "average": round(avg, 1),
                    "message": f"0 events (avg: {avg:.0f}) - scraper may be broken",
                })
            # WARNING: significantly below average (< 50%)
            elif avg > 0 and current < avg * 0.5 and current > 0:
                alerts.append({
                    "source": source,
                    "severity": "WARNING",
                    "current": current,
                    "average": round(avg, 1),
                    "message": f"{current} events vs avg {avg:.0f} ({current/avg*100:.0f}%)",
                })
            # MISSING: appeared in history but absent from current data entirely
            elif source in all_historical_sources and source not in current_counts:
                alerts.append({
                    "source": source,
                    "severity": "MISSING",
                    "current": 0,
                    "average": round(avg, 1),
                    "message": f"Source disappeared from data (avg: {avg:.0f})",
                })

        # Sort alerts: CRITICAL first, then WARNING, then MISSING
        severity_order = {"CRITICAL": 0, "WARNING": 1, "MISSING": 2}
        alerts.sort(key=lambda a: severity_order.get(a["severity"], 3))

        # Optional: get Groq diagnosis for critical alerts
        diagnosis = None
        critical_alerts = [a for a in alerts if a["severity"] == "CRITICAL"]
        if critical_alerts and self.groq_client:
            diagnosis = self._diagnose_failures(critical_alerts)

        report = {
            "status": "ok",
            "timestamp": now.isoformat(),
            "total_events": len(events),
            "total_sources": len(current_counts),
            "history_runs": len(past_runs),
            "alerts": alerts,
            "diagnosis": diagnosis,
            "summary": {
                "critical": len([a for a in alerts if a["severity"] == "CRITICAL"]),
                "warning": len([a for a in alerts if a["severity"] == "WARNING"]),
                "missing": len([a for a in alerts if a["severity"] == "MISSING"]),
            },
        }

        self.save_report(report, "health_monitor_report.json")

        # Create GitHub issue for critical alerts
        if critical_alerts:
            self._create_issue(critical_alerts, diagnosis)

        return report

    def _diagnose_failures(self, critical_alerts: list) -> str:
        """Use Groq to diagnose why scrapers might be failing"""
        sources = ", ".join(a["source"] for a in critical_alerts)
        prompt = (
            f"These web scrapers for Cambridge/Somerville event venues returned 0 events "
            f"when they normally return many: {sources}\n\n"
            f"What are the most likely reasons a web scraper would suddenly return 0 events? "
            f"Give 3-4 concise bullet points of common causes."
        )
        return self.llm_complete(prompt, system="You are a web scraping expert. Be concise.")

    def _create_issue(self, critical_alerts: list, diagnosis: str = None):
        """Create GitHub issue for critical health alerts"""
        title = f"Health Monitor - {len(critical_alerts)} scrapers returning 0 events"

        lines = ["## Scraper Health Alert\n"]
        lines.append("### Critical: Scrapers returning 0 events\n")
        for a in critical_alerts:
            lines.append(f"- **{a['source']}**: {a['message']}")
        lines.append("")

        if diagnosis:
            lines.append("### Possible Causes\n")
            lines.append(diagnosis)
            lines.append("")

        lines.append("---")
        lines.append(f"*Generated by Health Monitor Agent at {datetime.utcnow().isoformat()}*")

        self.create_github_issue(title, "\n".join(lines))


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    agent = HealthMonitorAgent()
    result = agent.run()
    print(f"\nHealth Monitor: {result.get('summary', result.get('message', 'done'))}")
    for alert in result.get("alerts", []):
        print(f"  [{alert['severity']}] {alert['source']}: {alert['message']}")
