"""User and email tracking models for onboarding system"""
import uuid
import secrets
from datetime import datetime
from enum import Enum
from typing import Optional, List
from sqlalchemy import Column, String, Boolean, DateTime, JSON, Integer, ForeignKey, Enum as SQLEnum, Float
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID
from pydantic import BaseModel, EmailStr, Field

from src.db.database import Base


class EmailStatus(str, Enum):
    """Email delivery status"""
    SENT = "sent"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    FAILED = "failed"


# SQLAlchemy Models (Database Tables)

class User(Base):
    """User table for storing subscriber information"""
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String(255), unique=True, nullable=False, index=True)
    email_opt_in = Column(Boolean, default=True)
    unsubscribe_token = Column(String(64), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(32))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_email_sent = Column(DateTime, nullable=True)

    # Legacy archetype columns (nullable for migration, will be dropped later)
    primary_archetype = Column(String(64), nullable=True)
    secondary_archetype = Column(String(64), nullable=True)
    questionnaire_responses = Column(JSON, nullable=True)

    # Relationships
    email_logs = relationship("EmailLog", back_populates="user", cascade="all, delete-orphan")
    click_tracking = relationship("ClickTracking", back_populates="user", cascade="all, delete-orphan")
    preferences = relationship("UserPreferences", back_populates="user", uselist=False, cascade="all, delete-orphan")
    onboarding_likes = relationship("OnboardingLike", back_populates="user", cascade="all, delete-orphan")
    digest_override = relationship("DigestOverride", back_populates="user", uselist=False, cascade="all, delete-orphan")


class UserPreferences(Base):
    """Learned preference weights per user"""
    __tablename__ = "user_preferences"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    category_weights = Column(JSON, default=dict)  # {"music": 0.8, "lectures": 0.4, ...}
    timing_weights = Column(JSON, default=dict)  # {"weekday_evening": 1.0, ...}
    venue_weights = Column(JSON, default=dict)  # {"Harvard Art Museums": 0.5, ...}
    price_sensitivity = Column(Float, default=0.5)  # 0.0 = prefers free, 1.0 = any price
    prefers_family_friendly = Column(Boolean, default=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="preferences")


class OnboardingLike(Base):
    """Raw record of which events each user liked during onboarding"""
    __tablename__ = "onboarding_likes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    event_id = Column(String(64), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="onboarding_likes")


class DigestOverride(Base):
    """Per-user override for next weekly email. Consumed and cleared after sending."""
    __tablename__ = "digest_overrides"

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    event_ids = Column(JSON, nullable=False, default=list)  # Array of event IDs
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(64), default="admin")

    # Relationships
    user = relationship("User", back_populates="digest_override")


class EmailLog(Base):
    """Email log table for tracking sent emails"""
    __tablename__ = "email_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    subject = Column(String(255), nullable=False)
    sent_at = Column(DateTime, default=datetime.utcnow)
    event_ids = Column(JSON, nullable=True)  # Array of event IDs included in email
    resend_message_id = Column(String(255), nullable=True)
    opened_at = Column(DateTime, nullable=True)
    open_count = Column(Integer, default=0)
    status = Column(SQLEnum(EmailStatus), default=EmailStatus.SENT)

    # Relationships
    user = relationship("User", back_populates="email_logs")
    clicks = relationship("ClickTracking", back_populates="email_log", cascade="all, delete-orphan")


class ClickTracking(Base):
    """Click tracking table for tracking link clicks in emails"""
    __tablename__ = "click_tracking"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    email_log_id = Column(UUID(as_uuid=True), ForeignKey("email_logs.id", ondelete="CASCADE"), nullable=False)
    event_id = Column(String(64), nullable=False)
    clicked_at = Column(DateTime, default=datetime.utcnow)
    event_position = Column(Integer, nullable=True)  # Position in email (1, 2, 3...)

    # Relationships
    user = relationship("User", back_populates="click_tracking")
    email_log = relationship("EmailLog", back_populates="clicks")


class EventPopularity(Base):
    """Event popularity scoring table"""
    __tablename__ = "event_popularity"

    event_id = Column(String(64), primary_key=True)
    venue_score = Column(Float, default=0.5)
    source_score = Column(Float, default=0.5)
    cost_score = Column(Float, default=0.5)
    recurrence_score = Column(Float, default=0.0)
    freshness_score = Column(Float, default=1.0)
    category_score = Column(Float, default=0.5)
    popularity_score = Column(Float, default=0.5)  # Composite score
    click_count = Column(Integer, default=0)
    calculated_at = Column(DateTime, default=datetime.utcnow)


# Pydantic Models (API Request/Response)

class OnboardingSubmit(BaseModel):
    """Request model for new onboarding submission"""
    email: EmailStr
    liked_event_ids: List[str] = Field(default_factory=list, description="IDs of events the user liked")


class OnboardingResponse(BaseModel):
    """Response model for onboarding submission"""
    success: bool
    user_id: str
    message: str
    liked_count: int


class UnsubscribeRequest(BaseModel):
    """Request model for unsubscribe"""
    token: str


class AdminStats(BaseModel):
    """Admin statistics response"""
    total_users: int
    emails_sent_last_7_days: int
    total_opens: int
    total_clicks: int
    open_rate: float
    click_rate: float
