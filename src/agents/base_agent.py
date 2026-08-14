"""Base agent class for all monitoring/quality agents"""
import json
import logging
import os
import subprocess
import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional


logger = logging.getLogger(__name__)


class BaseAgent(ABC):
    """Abstract base class for all agents, mirroring BaseScraper pattern"""

    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"agent.{name}")
        self._groq_client = None
        self._anthropic_client = None

    @property
    def groq_client(self):
        """Lazy-load Groq client, returns None if key missing"""
        if self._groq_client is None:
            api_key = os.environ.get("GROQ_API_KEY")
            if api_key:
                try:
                    from groq import Groq
                    self._groq_client = Groq(api_key=api_key)
                except ImportError:
                    self.logger.warning("groq package not installed")
        return self._groq_client

    @property
    def anthropic_client(self):
        """Lazy-load Anthropic client, returns None if key missing"""
        if self._anthropic_client is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if api_key:
                try:
                    import anthropic
                    self._anthropic_client = anthropic.Anthropic(api_key=api_key)
                except ImportError:
                    self.logger.warning("anthropic package not installed")
        return self._anthropic_client

    def llm_complete(self, prompt: str, system: str = "", provider: str = "groq") -> Optional[str]:
        """Unified LLM interface. Returns None if provider unavailable."""
        if provider == "groq":
            client = self.groq_client
            if not client:
                self.logger.warning("Groq not available (missing GROQ_API_KEY or package)")
                return None
            try:
                messages = []
                if system:
                    messages.append({"role": "system", "content": system})
                messages.append({"role": "user", "content": prompt})
                response = client.chat.completions.create(
                    model="openai/gpt-oss-120b",
                    messages=messages,
                    temperature=0.3,
                    max_tokens=4096,
                    reasoning_effort="low",
                )
                return response.choices[0].message.content
            except Exception as e:
                self.logger.error(f"Groq completion failed: {e}")
                return None

        elif provider == "anthropic":
            client = self.anthropic_client
            if not client:
                self.logger.warning("Anthropic not available (missing ANTHROPIC_API_KEY or package)")
                return None
            try:
                kwargs = {
                    "model": "claude-sonnet-4-5-20250929",
                    "max_tokens": 8192,
                    "messages": [{"role": "user", "content": prompt}],
                }
                if system:
                    kwargs["system"] = system
                response = client.messages.create(**kwargs)
                return response.content[0].text
            except Exception as e:
                self.logger.error(f"Anthropic completion failed: {e}")
                return None

        else:
            self.logger.error(f"Unknown LLM provider: {provider}")
            return None

    def load_events(self) -> list:
        """Load events from data/events.json"""
        events_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data", "events.json"
        )
        try:
            with open(events_path, "r") as f:
                return json.load(f)
        except FileNotFoundError:
            self.logger.warning(f"Events file not found: {events_path}")
            return []

    def save_report(self, data: dict, filename: str):
        """Save report to data/agent_reports/"""
        reports_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            "data", "agent_reports"
        )
        os.makedirs(reports_dir, exist_ok=True)
        filepath = os.path.join(reports_dir, filename)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2, default=str)
        self.logger.info(f"Report saved to {filepath}")

    def create_github_issue(self, title: str, body: str, assignee: str = None) -> bool:
        """Create GitHub issue via gh CLI, deduping by title prefix"""
        # Check for gh CLI
        try:
            subprocess.run(["gh", "--version"], capture_output=True, check=True)
        except (FileNotFoundError, subprocess.CalledProcessError):
            self.logger.warning("gh CLI not available, skipping issue creation")
            return False

        # Check for existing open issue with same title prefix
        title_prefix = title.split(" - ")[0] if " - " in title else title[:50]
        try:
            result = subprocess.run(
                ["gh", "issue", "list", "--state", "open", "--search", title_prefix, "--json", "title"],
                capture_output=True, text=True, check=True
            )
            existing = json.loads(result.stdout) if result.stdout.strip() else []
            for issue in existing:
                if issue.get("title", "").startswith(title_prefix):
                    self.logger.info(f"Open issue already exists: {issue['title']}")
                    return False
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            pass  # Proceed with creation if check fails

        # Create issue
        try:
            cmd = ["gh", "issue", "create", "--title", title, "--body", body]
            if assignee:
                cmd.extend(["--assignee", assignee])
            subprocess.run(
                cmd,
                capture_output=True, text=True, check=True
            )
            self.logger.info(f"Created GitHub issue: {title}")
            return True
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Failed to create issue: {e.stderr}")
            return False

    @abstractmethod
    def execute(self) -> dict:
        """Run the agent logic. Must return a dict with at least a 'status' key."""
        pass

    def run(self) -> dict:
        """Execute agent with timing, logging, and error handling"""
        self.logger.info(f"Starting agent: {self.name}")
        start = time.time()
        try:
            result = self.execute()
            elapsed = time.time() - start
            self.logger.info(f"Agent {self.name} completed in {elapsed:.1f}s")
            result["elapsed_seconds"] = round(elapsed, 1)
            return result
        except Exception as e:
            elapsed = time.time() - start
            self.logger.error(f"Agent {self.name} failed after {elapsed:.1f}s: {e}")
            return {"status": "error", "error": str(e), "elapsed_seconds": round(elapsed, 1)}
