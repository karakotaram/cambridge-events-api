"""Event data models following PRD schema"""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, HttpUrl, EmailStr
from enum import Enum


class EventCategory(str, Enum):
    """Event category types"""
    MUSIC = "music"
    ARTS_CULTURE = "arts and culture"
    FOOD_DRINK = "food and drink"
    THEATER = "theater"
    LECTURES = "lectures"
    SPORTS = "sports"
    COMMUNITY = "community"
    OTHER = "other"


class Event(BaseModel):
    """Core event model matching PRD schema"""

    # Core fields
    id: str = Field(..., description="Unique identifier")
    title: str = Field(..., max_length=200, description="Event name")
    description: str = Field(..., max_length=2000, description="Event description")
    start_datetime: datetime = Field(..., description="Event start time")
    end_datetime: Optional[datetime] = Field(None, description="Event end time")
    all_day: bool = Field(default=False, description="All-day event flag")

    # Location information
    venue_name: Optional[str] = Field(None, max_length=150, description="Venue name")
    street_address: Optional[str] = Field(None, max_length=200, description="Street address")
    city: Optional[str] = Field(None, max_length=50, description="City")
    state: Optional[str] = Field(None, max_length=2, description="State abbreviation")
    zip_code: Optional[str] = Field(None, max_length=10, description="ZIP code")
    latitude: Optional[float] = Field(None, description="Latitude")
    longitude: Optional[float] = Field(None, description="Longitude")

    # Categorization & metadata
    category: Optional[EventCategory] = Field(None, description="Event category")
    tags: List[str] = Field(default_factory=list, description="Event tags")
    age_restrictions: Optional[str] = Field(None, description="Age restrictions")
    cost: Optional[str] = Field(None, description="Cost information")
    registration_required: bool = Field(default=False, description="Registration required")

    # Source attribution
    source_url: str = Field(..., description="Original event page URL")
    source_name: str = Field(..., description="Source website name")
    scraped_at: datetime = Field(default_factory=datetime.utcnow, description="Scraping timestamp")
    last_updated: datetime = Field(default_factory=datetime.utcnow, description="Last update time")

    # Contact & additional info
    contact_email: Optional[EmailStr] = Field(None, description="Contact email")
    contact_phone: Optional[str] = Field(None, description="Contact phone")
    website_url: Optional[str] = Field(None, description="Event website")
    image_url: Optional[str] = Field(None, description="Event image URL")
    recurring_pattern: Optional[dict] = Field(None, description="Recurrence information")

    class Config:
        use_enum_values = True


class EventCreate(BaseModel):
    """Model for creating new events (before ID assignment)"""
    title: str = Field(..., max_length=200)
    description: str = Field(..., max_length=2000)
    start_datetime: datetime
    end_datetime: Optional[datetime] = None
    all_day: bool = False

    venue_name: Optional[str] = None
    street_address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    category: Optional[EventCategory] = None
    tags: List[str] = Field(default_factory=list)
    age_restrictions: Optional[str] = None
    cost: Optional[str] = None
    registration_required: bool = False

    source_url: str
    source_name: str

    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[str] = None
    website_url: Optional[str] = None
    image_url: Optional[str] = None
    recurring_pattern: Optional[dict] = None


class ScraperConfig(BaseModel):
    """Configuration for individual event source scrapers"""
    name: str = Field(..., description="Scraper name")
    url: str = Field(..., description="Target URL")
    priority: str = Field(..., description="Priority level: low, medium, high")
    enabled: bool = Field(default=True, description="Scraper enabled status")
    schedule_cron: str = Field(default="0 */6 * * *", description="Cron schedule")
    custom_scraper: bool = Field(default=False, description="Uses custom scraper logic")
    last_run: Optional[datetime] = Field(None, description="Last execution time")
    success_rate: float = Field(default=0.0, description="Success rate percentage")
