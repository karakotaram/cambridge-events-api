"""Website interaction tracking models for event ranking system"""
import uuid
from datetime import datetime
from sqlalchemy import Column, String, DateTime, Index
from sqlalchemy.dialects.postgresql import UUID

from src.db.database import Base


class WebsiteInteraction(Base):
    """
    Tracks user interactions with events on the website.

    Used to calculate event popularity scores for ranking.
    Interaction types:
    - card_expand: User clicked to expand event details
    - click_external: User clicked through to source URL
    - calendar_add: User added event to calendar
    """
    __tablename__ = "website_interactions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_id = Column(String(64), nullable=False, index=True)
    interaction_type = Column(String(32), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        # Composite index for efficient queries by event and time range
        Index('ix_website_interactions_event_created', 'event_id', 'created_at'),
    )
