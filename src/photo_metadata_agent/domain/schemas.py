"""Provider-independent contracts; generated JSON schemas live in configs/schemas."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PeopleBucket = Literal["0", "1", "2-3", "4-6", "7-9", "10+", "unknown"]
CaptureStyle = Literal["candid", "staged", "mixed", "unknown"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ContextTags(Contract):
    """Null = unknown; false = an explicit negative decision."""

    event: bool | None
    ministry_trip: bool | None
    family: bool | None


class Tags(Contract):
    people_count_bucket: PeopleBucket
    capture_style: CaptureStyle
    contexts: ContextTags

    def keywords(self) -> list[str]:
        tags = []
        if self.people_count_bucket != "unknown":
            tags.append(f"people:{self.people_count_bucket}")
        if self.capture_style != "unknown":
            tags.append(f"capture:{self.capture_style}")
        tags.extend(f"context:{k}" for k, v in self.contexts.model_dump().items() if v is True)
        return tags


class Prediction(Contract):
    tags: Tags
    scores: dict[str, float] = Field(default_factory=dict)
    raw: dict = Field(default_factory=dict)
    latency_ms: float = Field(default=0, ge=0)
    resolved_model: str


class Review(Contract):
    tags: Tags
    notes: str = Field(default="", max_length=4000)
    reviewer: str = Field(default="personal", min_length=1, max_length=100)
