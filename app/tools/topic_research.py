"""Provider interface and deterministic sample motivational topic data."""

from typing import Protocol

from app.schemas import TopicCandidate

_SAMPLE_TOPICS = (
    {
        "topic": "Build discipline when motivation disappears",
        "audience": "Students and early-career professionals",
        "content_format": "Relatable challenge with a practical rule",
        "relevance_score": 9.5,
        "freshness_score": 7.0,
        "story_potential": 9.0,
    },
    {
        "topic": "The small daily promise that creates consistency",
        "audience": "People restarting personal habits",
        "content_format": "One-minute habit story",
        "relevance_score": 9.0,
        "freshness_score": 7.5,
        "story_potential": 8.5,
    },
    {
        "topic": "Protect your focus from constant distractions",
        "audience": "Students and remote workers",
        "content_format": "Problem-and-solution mini lesson",
        "relevance_score": 8.5,
        "freshness_score": 8.0,
        "story_potential": 8.0,
    },
    {
        "topic": "Choose delayed gratification over easy comfort",
        "audience": "Young adults building long-term goals",
        "content_format": "A choice with two possible outcomes",
        "relevance_score": 8.5,
        "freshness_score": 7.0,
        "story_potential": 9.5,
    },
    {
        "topic": "Turn a setback into a resilient next step",
        "audience": "People recovering from failure",
        "content_format": "Short comeback narrative",
        "relevance_score": 8.0,
        "freshness_score": 8.5,
        "story_potential": 9.0,
    },
    {
        "topic": "Make self-improvement measurable, not overwhelming",
        "audience": "Beginners creating a growth routine",
        "content_format": "Three-step practical framework",
        "relevance_score": 8.5,
        "freshness_score": 8.0,
        "story_potential": 7.5,
    },
    {
        "topic": "Learn faster by showing up for deliberate practice",
        "audience": "Learners developing a new skill",
        "content_format": "Practice-versus-talent comparison",
        "relevance_score": 8.0,
        "freshness_score": 8.5,
        "story_potential": 8.0,
    },
    {
        "topic": "Consistency beats intensity you cannot sustain",
        "audience": "People pursuing fitness or study goals",
        "content_format": "Contrasting two weekly routines",
        "relevance_score": 9.0,
        "freshness_score": 7.5,
        "story_potential": 8.5,
    },
    {
        "topic": "Focus on the next useful action, not the whole mountain",
        "audience": "People facing large or uncertain goals",
        "content_format": "Step-by-step motivational prompt",
        "relevance_score": 8.0,
        "freshness_score": 8.0,
        "story_potential": 8.5,
    },
    {
        "topic": "Personal growth starts with honest self-reflection",
        "audience": "Viewers seeking meaningful change",
        "content_format": "Reflective question and takeaway",
        "relevance_score": 7.5,
        "freshness_score": 8.5,
        "story_potential": 8.0,
    },
    {
        "topic": "Keep learning when progress feels invisible",
        "audience": "Self-taught learners and creators",
        "content_format": "Progress-over-time analogy",
        "relevance_score": 8.0,
        "freshness_score": 8.0,
        "story_potential": 8.5,
    },
    {
        "topic": "Discipline is remembering what you want most",
        "audience": "People balancing short-term urges and goals",
        "content_format": "Values-based personal story",
        "relevance_score": 8.5,
        "freshness_score": 7.5,
        "story_potential": 9.0,
    },
)


class TopicResearchProvider(Protocol):
    """Contract for future YouTube or trend API research providers."""

    def research(self, niche: str) -> list[TopicCandidate]:
        """Return structured topic candidates for the requested niche."""


class SampleTopicResearchProvider:
    """Return deterministic seed ideas, not verified or live trend data."""

    def research(self, niche: str) -> list[TopicCandidate]:
        """Validate the niche and return labeled sample motivational ideas."""
        if not isinstance(niche, str) or not niche.strip():
            raise ValueError("The research niche must be a non-empty string.")
        return [
            TopicCandidate(**topic, source_type="sample_seed (not verified trends)")
            for topic in _SAMPLE_TOPICS
        ]