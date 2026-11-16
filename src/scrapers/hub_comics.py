"""Hub Comics (Union Square, Somerville) — event announcements on the shop's blog.

Hub posts each event as a WordPress blog post in its "Events" category: the
monthly BOOK CLHUB, signings, Free Comic Book Day. There is no events plugin,
so the date is read from the post's prose, through the WordPress REST API
(the category is looked up by slug, then its newest posts are read):

    "Join us Wednesday, December 9th at 7pm!"
    "... will sign at Hub Comics on Sunday, May 31st from 2pm to 4pm!"

**Dates.** Posts print a weekday, month and day but no year. The year is the
one — of the post's own year and the next — that puts the printed weekday on
that date and does not fall before the post was published. A post that
mentions two different dates (a reschedule notice, a pre-order deadline) is
skipped rather than guessed at. The book club meets monthly, but on no fixed
week (the second Wednesday in December 2026, the third in January), so no
recurrence is ever inferred: one post, one dated event.

**Times.** Only a time printed right after the date ("at 7pm", "from 2pm to
4pm") is used. Without one the listing is all-day.
"""
import html
import logging
import re
from datetime import date, datetime, timedelta
from typing import List, Optional, Tuple

import requests

from src.models.event import EventCategory, EventCreate
from src.scrapers.base_feed import BaseFeedScraper, Place
from src.scrapers.base_printed_date import at, clock, dated, month_number, weekday_number, year_for_weekday

logger = logging.getLogger(__name__)

SITE = "https://hubcomics.com"
API = f"{SITE}/wp-json/wp/v2"
CATEGORY_SLUG = "events"
POSTS = 10

MENTION = re.compile(
    r"\b((?:mon|tues|wednes|thurs|fri|satur|sun)day),?\s+"
    r"(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b",
    re.I)
TIME = r"\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)|noon"
AFTER = re.compile(rf"^\W{{0,3}}(?:at|from)\s+({TIME})(?:\s*(?:to|-|–|—)\s*({TIME}))?", re.I)

# Announcements go up days to weeks ahead; one dated a little before its post
# (an edit after the fact) is still that event.
BACKDATE = timedelta(days=7)


class HubComicsScraper(BaseFeedScraper):
    HOME = Place("Hub Comics", "19 Bow Street", "Somerville", "02143")
    CATEGORY = EventCategory.ARTS_CULTURE

    def __init__(self):
        super().__init__(source_name="Hub Comics", source_url=f"{SITE}/category/news/events/")

    def fetch_json(self, url: str, params: dict):
        response = requests.get(url, params=params, headers=self.get_browser_headers(), timeout=30)
        response.raise_for_status()
        return response.json()

    def scrape_events(self) -> List[EventCreate]:
        categories = self.fetch_json(f"{API}/categories", {"slug": CATEGORY_SLUG})
        if not categories:
            raise ValueError(f"{self.source_name}: no '{CATEGORY_SLUG}' category at {API}/categories")
        posts = self.fetch_json(f"{API}/posts", {
            "categories": categories[0]["id"], "per_page": POSTS,
            "_fields": "id,date,link,title,content,excerpt",
        })
        events = [e for e in (self.parse_post(p) for p in posts) if e]
        logger.info(f"Scraped {len(events)} events from {self.source_name}")
        return events

    def parse_post(self, post: dict) -> Optional[EventCreate]:
        title = self.text(html.unescape((post.get("title") or {}).get("rendered", "")))
        url = post.get("link") or self.source_url
        if not self.is_event(title):
            return None
        try:
            posted = datetime.fromisoformat(post["date"]).date()
        except (KeyError, TypeError, ValueError):
            logger.warning(f"Skipping '{title}' - post has no publication date ({url})")
            return None

        body = self.text((post.get("content") or {}).get("rendered", ""))
        when = self.when(body, posted, title, url)
        if when is None:
            return None
        start, end, all_day = when

        description = body[:2000] if len(body) >= 20 else self.fallback_description(title, self.HOME)
        return EventCreate(
            title=title[:200],
            description=description,
            start_datetime=start,
            end_datetime=end,
            all_day=all_day,
            venue_name=self.HOME.venue,
            street_address=self.HOME.street,
            city=self.HOME.city,
            state="MA",
            zip_code=self.HOME.zip_code,
            category=self.categorize(title),
            source_name=self.source_name,
            source_url=url,
        )

    def when(self, body: str, posted: date, title: str, url: str
             ) -> Optional[Tuple[datetime, Optional[datetime], bool]]:
        """The one date the post announces, with its time if printed beside it."""
        found = {}
        for match in MENTION.finditer(body):
            weekday, month, dom = weekday_number(match.group(1)), month_number(match.group(2)), int(match.group(3))
            if month is None:
                continue
            year = year_for_weekday(month, dom, weekday, (posted.year, posted.year + 1))
            day = dated(year, month, dom) if year else None
            if day is None or day < posted - BACKDATE:
                logger.warning(f"Skipping '{title}' - {match.group(0)!r} matches no date on or after "
                               f"the post ({posted}) ({url})")
                return None
            times = AFTER.match(body[match.end():])
            if day not in found or (times and not found[day]):
                found[day] = times

        if not found:
            logger.info(f"{self.source_name}: skipping '{title}' - no dated mention ({url})")
            return None
        if len(found) > 1:
            logger.warning(f"Skipping '{title}' - mentions {len(found)} different dates "
                           f"{sorted(map(str, found))} ({url})")
            return None

        (day, times), = found.items()
        start_time = clock(times.group(1)) if times else None
        if start_time is None:
            return at(day, None), None, True
        start = at(day, start_time)
        end_time = clock(times.group(2)) if times.group(2) else None
        end = at(day, end_time) if end_time else None
        return start, (end if end and end > start else None), False
