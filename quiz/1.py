from enum import Enum
from typing import Literal, Optional
from pydantic import BaseModel, Field, field_validator,model_validator,ValidationError


class ActivityCategory(str, Enum):
    SIGHTSEEING = "sightseeing"
    FOOD = "food"
    ADVENTURE = "adventure"
    CULTURE = "culture"
    RELAXATION = "relaxation"

class Currency(str, Enum):
    EGP = "EGP"
    USD = "USD"
    EUR = "EUR"

class Activity(BaseModel):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    category: ActivityCategory
    cost: float = Field(ge=0)
    duration_minutes: int = Field(ge=15, le=480)
    location_name: Optional[str] = None

    @field_validator("title", mode="after")
    @classmethod
    def validate_title(cls, value: str) -> str:
        clean_title = value.strip()

        if not clean_title:
            raise ValueError("the title is empty.")

        return clean_title

class TripRequest(BaseModel):
    destination: str = Field(min_length=1)
    total_budget: float = Field(gt=0)
    currency: Currency
    duration_days: int = Field(ge=1, le=30)
    travel_style: Literal["budget", "standard", "luxury"]
    interests: list[ActivityCategory]

    @field_validator("interests", mode="after")
    @classmethod
    def validate_interests(
            cls, value: list[ActivityCategory]
    ) -> list[ActivityCategory]:

        if len(value) < 1:
            raise ValueError("يجب اختيار اهتمام واحد على الأقل")

        return value

    @model_validator(mode="after")
    def validate_daily_budget(self) -> "TripRequest":
        daily_budget = self.total_budget / self.duration_days
        if daily_budget < 20:
            raise ValueError(
                "الميزانية اليومية غير واقعية لعدد أيام الرحلة"
            )
        return self



try:
    invalid_trip = TripRequest(
        destination="Cairo",
        total_budget=50,
        currency="EGP",
        duration_days=5,
        travel_style="budget",
        interests=[]
    )
except ValidationError as e:
    print(" تفاصيل الـ Validation Errors:")
    print(e.json(indent=2))