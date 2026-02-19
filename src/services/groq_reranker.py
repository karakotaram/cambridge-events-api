"""Groq LLM re-ranking service for email digest semantic diversity."""
import json
import os
import re
from typing import List, Dict, Optional

from src.models.event import Event


def _build_preference_summary(prefs: dict) -> str:
    """Build natural language summary of user preferences."""
    parts = []

    cat_weights = prefs.get("category_weights", {})
    if cat_weights:
        top_cats = sorted(cat_weights.items(), key=lambda x: x[1], reverse=True)[:3]
        cats_str = ", ".join(c for c, _ in top_cats if _ > 0.3)
        if cats_str:
            parts.append(f"Favorite categories: {cats_str}")

    timing_weights = prefs.get("timing_weights", {})
    if timing_weights:
        top_times = sorted(timing_weights.items(), key=lambda x: x[1], reverse=True)[:2]
        times_str = ", ".join(t.replace("_", " ") for t, _ in top_times if _ > 0.3)
        if times_str:
            parts.append(f"Preferred times: {times_str}")

    venue_weights = prefs.get("venue_weights", {})
    if venue_weights:
        top_venues = sorted(venue_weights.items(), key=lambda x: x[1], reverse=True)[:3]
        venues_str = ", ".join(v for v, _ in top_venues if _ > 0.3)
        if venues_str:
            parts.append(f"Liked venues: {venues_str}")

    if prefs.get("prefers_family_friendly"):
        parts.append("Prefers family-friendly events")

    price_sens = prefs.get("price_sensitivity", 0.5)
    if price_sens < 0.3:
        parts.append("Prefers free/cheap events")
    elif price_sens > 0.7:
        parts.append("Open to paid events")

    return "; ".join(parts) if parts else "No strong preferences yet"


def build_reranking_prompt(
    events: List[tuple],
    user_prefs: dict,
    count: int = 7,
) -> str:
    """
    Build the re-ranking prompt for Groq.

    Args:
        events: List of (Event, score) tuples (candidates)
        user_prefs: User preference dict
        count: Number of events to select

    Returns:
        Prompt string
    """
    pref_summary = _build_preference_summary(user_prefs)

    event_lines = []
    for i, (ev, score) in enumerate(events, 1):
        cat = ev.category.value if hasattr(ev.category, "value") else str(ev.category) if ev.category else "other"
        date_str = ev.start_datetime.strftime("%a %m/%d %I:%M%p")
        venue = ev.venue_name or ev.source_name or "Unknown"
        cost = ev.cost or "Unknown"
        desc = (ev.description or "")[:150]
        event_lines.append(
            f"{i}. [id={ev.id}] {ev.title} | {date_str} | {venue} | {cat} | {cost} | {desc}"
        )

    events_text = "\n".join(event_lines)

    return f"""You are a personalized event recommendation engine for Cambridge/Somerville, MA.

USER PREFERENCES: {pref_summary}

CANDIDATE EVENTS (ranked by initial ML scoring):
{events_text}

TASK: Select the best {count} events for this user's weekly email digest.

RULES:
1. Prioritize events matching user preferences
2. Ensure variety: mix different categories, venues, and days
3. Include at least one "discovery" pick - something outside their usual preferences that looks interesting
4. Prefer events happening sooner over later
5. Avoid selecting multiple events from the same venue

OUTPUT: Return ONLY a JSON array of objects with "event_id" and "reason" (1 sentence each).
Example: [{{"event_id": "abc123", "reason": "Top-rated music event at your favorite venue"}}]

Return exactly {count} events. JSON only, no other text."""


def rerank_events_with_groq(
    candidates: List[tuple],
    user_prefs: dict,
    count: int = 7,
) -> Optional[List[Dict]]:
    """
    Re-rank candidate events using Groq LLM for semantic diversity.

    Args:
        candidates: List of (Event, score) tuples
        user_prefs: User preference dict
        count: Number of events to select

    Returns:
        List of {"event_id": str, "reason": str} or None on failure
    """
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("[Groq Reranker] No GROQ_API_KEY, skipping")
        return None

    if len(candidates) < count:
        return None

    try:
        from groq import Groq

        prompt = build_reranking_prompt(candidates, user_prefs, count)

        client = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1024,
        )

        raw = response.choices[0].message.content.strip()

        # Strip markdown code fences if present
        raw = re.sub(r"^```(?:json)?\s*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)

        result = json.loads(raw)

        if not isinstance(result, list):
            print(f"[Groq Reranker] Expected list, got {type(result)}")
            return None

        # Validate structure
        valid = []
        for item in result:
            if isinstance(item, dict) and "event_id" in item:
                valid.append({
                    "event_id": item["event_id"],
                    "reason": item.get("reason", ""),
                })

        if len(valid) < count // 2:
            print(f"[Groq Reranker] Only {len(valid)} valid results, need at least {count // 2}")
            return None

        return valid[:count]

    except Exception as e:
        print(f"[Groq Reranker] Error: {e}")
        return None
