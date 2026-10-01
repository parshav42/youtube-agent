"""Tests for topic validation, scoring, sorting, and selection."""

import pytest
from pydantic import ValidationError

from app.agents.research_agent import score_candidate, score_topics, select_top_topics
from app.schemas import TopicCandidate
from app.tools.topic_research import SampleTopicResearchProvider


def make_candidate(topic: str, relevance: float, freshness: float, story: float) -> TopicCandidate:
    """Build a compact valid topic candidate for scoring tests."""
    return TopicCandidate(
        topic=topic,
        audience="Learners",
        content_format="Short story",
        relevance_score=relevance,
        freshness_score=freshness,
        story_potential=story,
        source_type="test fixture",
    )


def test_sample_provider_returns_at_least_ten_labeled_topics() -> None:
    topics = SampleTopicResearchProvider().research("discipline")

    assert len(topics) >= 10
    assert all(isinstance(topic, TopicCandidate) for topic in topics)
    assert all("sample" in topic.source_type.lower() for topic in topics)
    assert all("not verified trends" in topic.source_type.lower() for topic in topics)


def test_topic_candidate_rejects_scores_outside_zero_to_ten() -> None:
    with pytest.raises(ValidationError, match="less than or equal to 10"):
        make_candidate("Invalid", 10.1, 5, 5)


def test_topic_candidate_rejects_blank_required_text() -> None:
    with pytest.raises(ValidationError, match="non-whitespace text"):
        TopicCandidate(
            topic="  ",
            audience="Learners",
            content_format="Short story",
            relevance_score=5,
            freshness_score=5,
            story_potential=5,
            source_type="test",
        )


def test_scoring_formula_and_score_range() -> None:
    scored = score_candidate(make_candidate("Balanced", 10, 8, 6))

    assert scored.final_score == pytest.approx((10 * 0.40) + (8 * 0.25) + (6 * 0.35))
    assert 0 <= scored.final_score <= 10


def test_topics_are_sorted_and_top_three_selected() -> None:
    candidates = [
        make_candidate("Third", 8, 8, 8),
        make_candidate("First", 10, 10, 10),
        make_candidate("Fifth", 1, 2, 1),
        make_candidate("Second", 9, 9, 9),
        make_candidate("Fourth", 5, 5, 5),
    ]

    scored = score_topics(candidates)
    selected = select_top_topics(scored)

    assert [topic.topic for topic in scored] == ["First", "Second", "Third", "Fourth", "Fifth"]
    assert [topic.topic for topic in selected] == ["First", "Second", "Third"]
    assert all(left.final_score >= right.final_score for left, right in zip(scored, scored[1:]))


def test_invalid_selection_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        select_top_topics([], top_n=0)