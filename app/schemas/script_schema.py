"""Validated request, scene, script, and quality evaluation models."""

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ScriptRequest(BaseModel):
    """Requested subject, narration language, and short-form duration."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    topic: str = Field(min_length=1)
    language: str = Field(default="Hinglish", min_length=1)
    target_duration_seconds: int = Field(default=35, ge=30, le=45)

    @field_validator("topic", "language", mode="before")
    @classmethod
    def require_non_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("This field must contain non-whitespace text.")
        return value


class ScriptScene(BaseModel):
    """Production-ready visual and audio instructions for one vertical scene."""

    model_config = ConfigDict(extra="forbid")

    scene_number: int = Field(ge=1, le=7)
    duration_seconds: int = Field(ge=1, le=45)
    narration: str = Field(min_length=1)
    visual_description: str = Field(min_length=1)
    caption: str = Field(min_length=1)
    transition: str = Field(min_length=1)
    audio_direction: str = Field(min_length=1)

    @field_validator("narration", mode="before")
    @classmethod
    def preserve_non_blank_narration(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("This field must contain non-whitespace text.")
        return value

    @field_validator("visual_description", "caption", "transition", "audio_direction", mode="before")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("This field must contain non-whitespace text.")
        return value.strip()


class ScriptSceneDraft(BaseModel):
    """HF-generated scene directions; narration is assigned from the canonical script."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    scene_number: int = Field(ge=1, le=7)
    duration_seconds: int = Field(ge=1, le=45)
    visual_description: str = Field(min_length=1)
    caption: str = Field(min_length=1)
    transition: str = Field(min_length=1)
    audio_direction: str = Field(min_length=1)


class ScriptDraft(BaseModel):
    """HF draft where one full narration is canonical and scenes omit narration."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1)
    language: str = Field(min_length=1)
    target_duration_seconds: int = Field(ge=30, le=45)
    title_candidates: list[str] = Field(min_length=3, max_length=5)
    opening_hook: str = Field(min_length=1)
    narration: str = Field(min_length=1)
    story_beats: list[str] = Field(min_length=6, max_length=6)
    scenes: list[ScriptSceneDraft] = Field(min_length=4, max_length=7)
    closing_message: str = Field(min_length=1)
    description: str = Field(min_length=1)
    hashtags: list[str] = Field(min_length=1)
    quality_score: float = Field(default=0, ge=0, le=10)
    revision_notes: list[str] = Field(default_factory=list)

    @field_validator("topic", "language", "opening_hook", "closing_message", "description", mode="before")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("This field must contain non-whitespace text.")
        return value.strip()

    @field_validator("narration", mode="before")
    @classmethod
    def preserve_complete_narration(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Narration must contain non-whitespace text.")
        return value

    @field_validator("title_candidates", "hashtags", "story_beats", mode="before")
    @classmethod
    def trim_list_text(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values]


class MotivationalScript(BaseModel):
    """Complete short-form script with a fixed six-beat narrative arc."""

    model_config = ConfigDict(extra="forbid")

    topic: str = Field(min_length=1)
    language: str = Field(min_length=1)
    target_duration_seconds: int = Field(ge=30, le=45)
    title_candidates: list[str] = Field(min_length=3, max_length=5)
    opening_hook: str = Field(min_length=1)
    narration: str = Field(min_length=1)
    story_beats: list[str] = Field(min_length=6, max_length=6)
    scenes: list[ScriptScene] = Field(min_length=4, max_length=7)
    closing_message: str = Field(min_length=1)
    description: str = Field(min_length=1)
    hashtags: list[str] = Field(min_length=1)
    quality_score: float = Field(ge=0, le=10)
    revision_notes: list[str] = Field(default_factory=list)

    @field_validator("topic", "language", "opening_hook", "closing_message", "description", mode="before")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("This field must contain non-whitespace text.")
        return value.strip()

    @field_validator("narration", mode="before")
    @classmethod
    def preserve_complete_narration(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Narration must contain non-whitespace text.")
        return value

    @field_validator("title_candidates", "hashtags", "story_beats", "revision_notes", mode="before")
    @classmethod
    def trim_list_text(cls, values: list[str]) -> list[str]:
        return [value.strip() for value in values]

    @model_validator(mode="after")
    def validate_scene_sequence_and_duration(self) -> "MotivationalScript":
        if [scene.scene_number for scene in self.scenes] != list(
            range(1, len(self.scenes) + 1)
        ):
            raise ValueError("Scene numbers must be consecutive and start at 1.")
        duration = sum(scene.duration_seconds for scene in self.scenes)
        if duration != self.target_duration_seconds:
            raise ValueError("Scene durations must sum to the target duration.")
        visual_signatures = [
            re.sub(r"\s+", " ", scene.visual_description).strip().casefold()
            for scene in self.scenes
        ]
        if len(set(visual_signatures)) != len(visual_signatures):
            raise ValueError("Scene visual descriptions must be distinct.")
        scene_narration = "".join(scene.narration for scene in self.scenes)
        if scene_narration != self.narration:
            raise ValueError(
                "Concatenated scene narration must exactly match the full narration."
            )
        if [beat.split(":", 1)[0].strip().lower() for beat in self.story_beats] != [
            "hook",
            "problem",
            "conflict",
            "turning point",
            "resolution",
            "closing message",
        ]:
            raise ValueError("Story beats must follow the six required stages in order.")
        return self


class ScriptEvaluation(BaseModel):
    """Heuristic content checks, not an audience-engagement prediction."""

    model_config = ConfigDict(extra="forbid")

    quality_score: float = Field(ge=0, le=10)
    threshold: float = Field(ge=0, le=10)
    passed: bool
    language_consistency: bool
    story_coherence: bool
    originality_safeguards: bool
    duration_feasibility: bool
    repetition: bool
    motivational_message: bool
    scene_completeness: bool
    caption_scene_alignment: bool
    structural_labels_absent: bool
    unsupported_factual_assertions: bool
    unsupported_guarantees: bool
    feedback: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_decision_consistency(self) -> "ScriptEvaluation":
        checks = (
            self.language_consistency,
            self.story_coherence,
            self.originality_safeguards,
            self.duration_feasibility,
            self.repetition,
            self.motivational_message,
            self.scene_completeness,
            self.caption_scene_alignment,
            self.structural_labels_absent,
            self.unsupported_factual_assertions,
            self.unsupported_guarantees,
        )
        expected_passed = self.quality_score >= self.threshold and all(checks)
        if self.passed != expected_passed:
            raise ValueError("passed must match the quality threshold and all evaluation checks.")
        return self


class StoryBeatPlan(BaseModel):
    """Structured story-beat output from a configured LLM provider."""

    story_beats: list[str] = Field(min_length=6, max_length=6)


class ScenePlan(BaseModel):
    """Structured scene plan returned by a configured LLM provider."""

    scenes: list[ScriptScene] = Field(min_length=4, max_length=7)


class LLMQualityReview(BaseModel):
    """Qualitative LLM review combined with deterministic quality safeguards."""

    language_consistency: bool
    story_coherence: bool
    originality_safeguards: bool
    motivational_message: bool
    unsupported_factual_assertions: bool
    feedback: list[str] = Field(default_factory=list)


class ProductionMetadata(BaseModel):
    """Discoverability metadata generated from an approved script."""

    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=2000)
    hashtags: list[str] = Field(min_length=1, max_length=15)
    tags: list[str] = Field(default_factory=list, max_length=30)
    language: str = Field(default="hi", min_length=2, max_length=35)
    category_id: str = Field(default="22", pattern=r"^\d{1,3}$")
    publishing_notes: str = Field(default="", max_length=1000)