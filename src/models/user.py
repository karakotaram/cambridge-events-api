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


class ArchetypeEnum(str, Enum):
    """User archetype types"""
    CULTURE_PROFESSIONAL = "culture_professional"
    FAMILY_EXPLORER = "family_explorer"
    NIGHTLIFE_ENTHUSIAST = "nightlife_enthusiast"
    ACADEMIC_CURIOUS = "academic_curious"
    SOCIAL_CONNECTOR = "social_connector"
    ARTS_AFICIONADO = "arts_aficionado"
    ACTIVE_ADVENTURER = "active_adventurer"
    BUDGET_EXPLORER = "budget_explorer"


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
    primary_archetype = Column(SQLEnum(ArchetypeEnum), nullable=False)
    secondary_archetype = Column(SQLEnum(ArchetypeEnum), nullable=True)
    questionnaire_responses = Column(JSON, nullable=False)
    email_opt_in = Column(Boolean, default=True)
    unsubscribe_token = Column(String(64), unique=True, nullable=False, default=lambda: secrets.token_urlsafe(32))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    last_email_sent = Column(DateTime, nullable=True)

    # Relationships
    email_logs = relationship("EmailLog", back_populates="user", cascade="all, delete-orphan")
    click_tracking = relationship("ClickTracking", back_populates="user", cascade="all, delete-orphan")


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


class CuratedDigest(Base):
    """Saved curated event picks per archetype for weekly emails. Keeps full history."""
    __tablename__ = "curated_digests"

    id = Column(Integer, primary_key=True, autoincrement=True)
    archetype = Column(String(64), nullable=False, index=True)
    events = Column(JSON, nullable=False, default=list)  # [{"event_id": "...", "score": 0.85}, ...]
    created_at = Column(DateTime, default=datetime.utcnow)


# Pydantic Models (API Request/Response)

class QuestionnaireResponses(BaseModel):
    """User's answers to the 4 onboarding questions"""
    lifestyle: str = Field(..., description="Lifestyle type: professional, parent, student, retired, active")
    interests: List[str] = Field(..., max_length=2, description="Top 2 interests")
    timing: str = Field(..., description="Preferred timing: weekday_evening, weekend_daytime, weekend_evening, flexible")
    budget: str = Field(..., description="Budget preference: free_only, under_20, moderate, any")


class OnboardingSubmit(BaseModel):
    """Request model for onboarding submission"""
    email: EmailStr
    responses: QuestionnaireResponses


class OnboardingResponse(BaseModel):
    """Response model for onboarding submission"""
    success: bool
    user_id: str
    primary_archetype: str
    secondary_archetype: Optional[str]
    archetype_description: str
    message: str


class UnsubscribeRequest(BaseModel):
    """Request model for unsubscribe"""
    token: str


class QuestionOption(BaseModel):
    """Single option for a question"""
    value: str
    label: str
    description: Optional[str] = None


class Question(BaseModel):
    """Single question in the questionnaire"""
    id: str
    question: str
    type: str  # "single" or "multi"
    max_selections: Optional[int] = None
    options: List[QuestionOption]


class QuestionsResponse(BaseModel):
    """Response model for questionnaire questions"""
    questions: List[Question]


class AdminStats(BaseModel):
    """Admin statistics response"""
    total_users: int
    users_by_archetype: dict
    emails_sent_last_7_days: int
    total_opens: int
    total_clicks: int
    open_rate: float
    click_rate: float
