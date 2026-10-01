"""Topic scoring and ranking, kept independent from research data collection."""

from collections.abc import Iterable

from pydantic import BaseModel, Field

from app.providers.huggingface_llm import HuggingFaceLLMProvider
from app.schemas import SelectedTopic, TopicCandidate

RELEVANCE_WEIGHT = 0.40
FRESHNESS_WEIGHT = 0.25
STORY_POTENTIAL_WEIGHT = 0.35


class ResearchTopicBatch(BaseModel):
    """Structured candidate list returned from Hugging Face research prompting."""

    candidates: list[TopicCandidate] = Field(min_length=5, max_length=12)


class HuggingFaceResearchProvider:
    """Generate topic candidates with the configured Hugging Face model."""

    def __init__(self, llm_provider: HuggingFaceLLMProvider) -> None:
        self.llm_provider = llm_provider

    def research(self, niche: str) -> list[TopicCandidate]:
        batch = self.llm_provider.generate_structured(
            ResearchTopicBatch,
            "Generate original motivational video topic ideas. Do not assert live popularity or trends.",
            "Return 8 distinct topic candidates for this niche: "
            f"{niche!r}. Audience: students and young professionals. Use source_type 'LLM idea, "
            "not verified trend'. Score topic relevance, freshness as an editorial estimate, and story potential 0-10.",
            max_tokens=1800,
        )
        return batch.candidates


def score_candidate(candidate: TopicCandidate) -> SelectedTopic:
    """Apply the initial 0-10 heuristic and return a scored topic."""
    final_score = (
        candidate.relevance_score * RELEVANCE_WEIGHT
        + candidate.freshness_score * FRESHNESS_WEIGHT
        + candidate.story_potential * STORY_POTENTIAL_WEIGHT
    )
    return SelectedTopic(**candidate.model_dump(), final_score=round(final_score, 4))


def score_topics(candidates: Iterable[TopicCandidate]) -> list[SelectedTopic]:
    """Score candidates and order them from highest to lowest final score."""
    scored_topics = [score_candidate(candidate) for candidate in candidates]
    return sorted(scored_topics, key=lambda topic: topic.final_score, reverse=True)


def select_top_topics(
    scored_topics: Iterable[SelectedTopic], top_n: int = 3
) -> list[SelectedTopic]:
    """Return up to ``top_n`` topics in descending score order."""
    if top_n < 1:
        raise ValueError("top_n must be at least 1.")
    return sorted(scored_topics, key=lambda topic: topic.final_score, reverse=True)[:top_n]