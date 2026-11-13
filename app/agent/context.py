from dataclasses import dataclass


@dataclass(frozen=True)
class CustomerContext:
    customer_id: int
    full_name: str
    food_preferences: str
    disliked_foods: str
    dietary_preferences: str
    profile_summary: str
