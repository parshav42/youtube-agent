"""Validated research topic schemas."""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TopicCandidate(BaseModel):
    """A researched topic idea and its independent quality signals."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    content_format: str = Field(min_length=1)
    relevance_score: float = Field(ge=0, le=10)
    freshness_score: float = Field(ge=0, le=10)
    story_potential: float = Field(ge=0, le=10)
    source_type: str = Field(min_length=1)

    @field_validator("topic", "audience", "content_format", "source_type", mode="before")
    @classmethod
    def require_non_blank_text(cls, value: str) -> str:
        """Reject strings that are empty or contain only whitespace."""
        if not value.strip():
            raise ValueError("This field must contain non-whitespace text.")
        return value


class SelectedTopic(TopicCandidate):
    """A validated topic candidate with its weighted final score."""

    final_score: float = Field(ge=0, le=10)


from app.schemas.script_schema import (  # noqa: E402
    MotivationalScript,
    ScriptEvaluation,
    ScriptRequest,
    ScriptScene,
)