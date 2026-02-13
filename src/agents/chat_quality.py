"""Agent 6: Chat Quality - Test the /chat endpoint with predefined queries"""
import argparse
import logging
import re
from datetime import datetime

import requests

from src.agents.base_agent import BaseAgent

logger = logging.getLogger(__name__)

# Test queries with expected behaviors
TEST_QUERIES = [
    {
        "query": "What's happening this weekend?",
        "expect": {"has_links": True, "min_events": 2},
    },
    {
        "query": "Any live music tonight?",
        "expect": {"has_links": True, "category_match": "music"},
    },
    {
        "query": "Free events this week",
        "expect": {"has_links": True, "min_events": 1},
    },
    {
        "query": "What can I do with my toddler this weekend?",
        "expect": {"has_links": True, "age_appropriate": True},
    },
    {
        "query": "Family friendly events near Harvard Square",
        "expect": {"has_links": True, "age_appropriate": True},
    },
    {
        "query": "Are there any comedy shows coming up?",
        "expect": {"has_links": True, "category_match": "comedy"},
    },
    {
        "query": "Theater performances this month",
        "expect": {"has_links": True, "category_match": "theater"},
    },
    {
        "query": "Book readings or author events",
        "expect": {"has_links": True},
    },
    {
        "query": "What's happening at MIT this week?",
        "expect": {"has_links": True},
    },
    {
        "query": "Dance classes in Cambridge",
        "expect": {"has_links": True},
    },
    {
        "query": "Food and drink events",
        "expect": {"has_links": True, "category_match": "food"},
    },
    {
        "query": "Events at the Brattle Theatre",
        "expect": {"has_links": True},
    },
    {
        "query": "What should I do on a date night?",
        "expect": {"has_links": True, "min_events": 2},
    },
    {
        "query": "Outdoor activities this weekend",
        "expect": {"has_links": True},
    },
    {
        "query": "Tell me about community events in Somerville",
        "expect": {"has_links": True},
    },
]

# Words that should NOT appear in toddler/kid-appropriate responses
INAPPROPRIATE_FOR_KIDS = [
    "bar crawl", "beer tasting", "wine tasting", "cocktail",
    "21+", "18+", "adults only", "late night",
]


class ChatQualityAgent(BaseAgent):
    """Test the /chat endpoint quality with predefined queries"""

    def __init__(self):
        super().__init__("chat_quality")
        self.base_url = "http://localhost:8000"

    def execute(self) -> dict:
        results = []
        passed = 0
        failed = 0

        for test in TEST_QUERIES:
            result = self._run_test(test)
            results.append(result)
            if result["passed"]:
                passed += 1
            else:
                failed += 1

        # Optional: Groq grading of overall quality
        overall_grade = None
        if self.groq_client and results:
            overall_grade = self._grade_overall(results)

        report = {
            "status": "ok",
            "timestamp": datetime.utcnow().isoformat(),
            "base_url": self.base_url,
            "results": results,
            "overall_grade": overall_grade,
            "summary": {
                "total": len(results),
                "passed": passed,
                "failed": failed,
                "pass_rate": f"{passed / len(results) * 100:.0f}%" if results else "N/A",
            },
        }

        self.save_report(report, "chat_quality_report.json")
        return report

    def _run_test(self, test: dict) -> dict:
        """Run a single test query against the chat endpoint"""
        query = test["query"]
        expect = test["expect"]

        result = {
            "query": query,
            "passed": True,
            "checks": {},
            "response": None,
            "error": None,
        }

        # Send request
        try:
            response = requests.post(
                f"{self.base_url}/chat",
                json={"message": query},
                timeout=30,
            )
            response.raise_for_status()
            data = response.json()
            reply = data.get("response", data.get("reply", ""))
            result["response"] = reply
        except requests.RequestException as e:
            result["error"] = str(e)
            result["passed"] = False
            return result

        if not reply:
            result["passed"] = False
            result["error"] = "Empty response"
            return result

        # Check: has_links
        if expect.get("has_links"):
            has_links = bool(re.search(r"https?://\S+", reply))
            result["checks"]["has_links"] = has_links
            if not has_links:
                result["passed"] = False

        # Check: min_events
        if expect.get("min_events"):
            # Count URLs as proxy for event mentions
            links = re.findall(r"https?://\S+", reply)
            result["checks"]["event_links_found"] = len(links)
            if len(links) < expect["min_events"]:
                result["passed"] = False

        # Check: age_appropriate (for toddler/family queries)
        if expect.get("age_appropriate"):
            reply_lower = reply.lower()
            inappropriate = [w for w in INAPPROPRIATE_FOR_KIDS if w in reply_lower]
            result["checks"]["age_appropriate"] = len(inappropriate) == 0
            result["checks"]["inappropriate_found"] = inappropriate
            if inappropriate:
                result["passed"] = False

        # Check: category_match
        if expect.get("category_match"):
            cat = expect["category_match"].lower()
            result["checks"]["category_mentioned"] = cat in reply.lower()

        # Optional: Groq grade for this response
        if self.groq_client:
            grade = self._grade_response(query, reply)
            result["grade"] = grade
            if grade and grade.startswith(("D", "F")):
                result["passed"] = False

        return result

    def _grade_response(self, query: str, response: str) -> str:
        """Use Groq to grade a single chat response A-F"""
        prompt = (
            f"Grade this chatbot response for a Cambridge/Somerville event finder.\n\n"
            f"User query: {query}\n\n"
            f"Response: {response[:1500]}\n\n"
            f"Grade A-F based on: relevance, helpfulness, accuracy of event info, "
            f"inclusion of links/details. Reply with ONLY the letter grade (A, B, C, D, or F)."
        )
        result = self.llm_complete(prompt, system="You grade chatbot responses concisely.")
        if result:
            grade = result.strip().upper()[:1]
            if grade in "ABCDF":
                return grade
        return None

    def _grade_overall(self, results: list) -> str:
        """Grade overall chat quality"""
        summaries = []
        for r in results[:10]:
            status = "PASS" if r["passed"] else "FAIL"
            grade = r.get("grade", "?")
            summaries.append(f"Q: {r['query']} -> {status} (grade: {grade})")

        prompt = (
            "Grade the overall quality of this event chatbot (A-F) based on these test results:\n\n"
            + "\n".join(summaries) + "\n\n"
            "Reply with ONLY the letter grade."
        )
        result = self.llm_complete(prompt, system="You grade chatbot quality concisely.")
        if result:
            grade = result.strip().upper()[:1]
            if grade in "ABCDF":
                return grade
        return None


def main():
    parser = argparse.ArgumentParser(description="Test chat endpoint quality")
    parser.add_argument("--base-url", default="http://localhost:8000", help="API base URL")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    agent = ChatQualityAgent()
    agent.base_url = args.base_url
    result = agent.run()

    print(f"\nChat Quality: {result['summary']}")
    if result.get("overall_grade"):
        print(f"Overall grade: {result['overall_grade']}")
    for r in result.get("results", []):
        status = "PASS" if r["passed"] else "FAIL"
        grade = r.get("grade", "")
        print(f"  [{status}] {grade} - {r['query']}")
        if r.get("error"):
            print(f"         Error: {r['error']}")


if __name__ == "__main__":
    main()
