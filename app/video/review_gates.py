"""Fail-closed validation for production QA and human review records."""

from collections.abc import Mapping
from datetime import datetime
from typing import Any


def has_recorded_decision(value: Any, decision: str) -> bool:
    if not isinstance(value, Mapping):
        return False
    reviewer = value.get("reviewer")
    reviewed_at = value.get("reviewed_at")
    if (
        value.get("decision") != decision
        or value.get("confirmed") is not True
        or not isinstance(reviewer, str)
        or not reviewer.strip()
        or not isinstance(reviewed_at, str)
    ):
        return False
    try:
        timestamp = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return timestamp.tzinfo is not None


def production_qa_allows_approval(metadata: Any) -> bool:
    if not isinstance(metadata, Mapping):
        return False
    audio_qa = metadata.get("audio_qa")
    video_qa = metadata.get("video_qa")
    if not isinstance(audio_qa, Mapping) or not isinstance(video_qa, Mapping):
        return False
    audio_status = audio_qa.get("technical_status")
    audio_allowed = audio_status == "passed" or (
        audio_status == "needs_review"
        and has_recorded_decision(metadata.get("audio_review_decision"), "approve")
    )
    return audio_allowed and video_qa.get("technical_status") == "passed"