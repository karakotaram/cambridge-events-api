"""Duplicate event detection and merging"""
from typing import List, Set
from datetime import timedelta
from difflib import SequenceMatcher
from src.models.event import EventCreate, Event


class EventDeduplicator:
    """Detects and merges duplicate events"""

    @staticmethod
    def find_duplicates(events: List[EventCreate]) -> List[List[int]]:
        """
        Find duplicate events and return groups of indices
        Returns: List of lists, where each inner list contains indices of duplicate events
        """
        duplicate_groups = []
        processed_indices: Set[int] = set()

        for i, event1 in enumerate(events):
            if i in processed_indices:
                continue

            duplicates = [i]

            for j in range(i + 1, len(events)):
                if j in processed_indices:
                    continue

                if EventDeduplicator.are_duplicates(event1, events[j]):
                    duplicates.append(j)
                    processed_indices.add(j)

            if len(duplicates) > 1:
                duplicate_groups.append(duplicates)
                processed_indices.add(i)

        return duplicate_groups

    @staticmethod
    def are_duplicates(event1: EventCreate, event2: EventCreate) -> bool:
        """
        Check if two events are duplicates based on:
        - Similar titles (>= 85% match)
        - Same date (within 1 hour)
        - Similar location (if available)
        """

        # Check title similarity
        title_similarity = EventDeduplicator.text_similarity(
            event1.title.lower(),
            event2.title.lower()
        )

        if title_similarity < 0.85:
            return False

        # Check date/time proximity (within 1 hour)
        time_diff = abs((event1.start_datetime - event2.start_datetime).total_seconds())
        if time_diff > 3600:  # 1 hour
            return False

        # If both have venue information, check if they match
        if event1.venue_name and event2.venue_name:
            venue_similarity = EventDeduplicator.text_similarity(
                event1.venue_name.lower(),
                event2.venue_name.lower()
            )
            if venue_similarity < 0.7:
                return False

        return True

    @staticmethod
    def text_similarity(text1: str, text2: str) -> float:
        """Calculate similarity between two text strings (0-1)"""
        return SequenceMatcher(None, text1, text2).ratio()

    @staticmethod
    def merge_duplicates(events: List[EventCreate]) -> EventCreate:
        """
        Merge multiple duplicate events into one, keeping the best data
        """
        if not events:
            raise ValueError("Cannot merge empty list")

        if len(events) == 1:
            return events[0]

        # Start with the first event as base
        merged = events[0].model_copy()

        # Merge data from other events, preferring more complete information
        for event in events[1:]:
            # Use longer description
            if len(event.description) > len(merged.description):
                merged.description = event.description

            # Use more complete location data
            if event.venue_name and not merged.venue_name:
                merged.venue_name = event.venue_name

            if event.street_address and not merged.street_address:
                merged.street_address = event.street_address

            if event.latitude and not merged.latitude:
                merged.latitude = event.latitude
                merged.longitude = event.longitude

            # Use more specific category
            if event.category and not merged.category:
                merged.category = event.category

            # Merge tags
            merged.tags = list(set(merged.tags + event.tags))

            # Use populated contact info
            if event.contact_email and not merged.contact_email:
                merged.contact_email = event.contact_email

            if event.contact_phone and not merged.contact_phone:
                merged.contact_phone = event.contact_phone

            # Prefer event-specific URLs over general source URLs
            if event.website_url and not merged.website_url:
                merged.website_url = event.website_url

            if event.image_url and not merged.image_url:
                merged.image_url = event.image_url

        return merged

    @staticmethod
    def deduplicate_events(events: List[EventCreate]) -> List[EventCreate]:
        """
        Remove duplicates from event list
        Returns deduplicated list of events
        """
        if not events:
            return []

        duplicate_groups = EventDeduplicator.find_duplicates(events)

        # Merge duplicate groups
        merged_events = []
        processed_indices = set()

        # Add merged events
        for group in duplicate_groups:
            group_events = [events[i] for i in group]
            merged = EventDeduplicator.merge_duplicates(group_events)
            merged_events.append(merged)
            processed_indices.update(group)

        # Add non-duplicate events
        for i, event in enumerate(events):
            if i not in processed_indices:
                merged_events.append(event)

        return merged_events
