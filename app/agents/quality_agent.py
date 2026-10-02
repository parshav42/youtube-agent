"""Transparent heuristic script checks; scores are not engagement predictions."""

import re
from collections import Counter
from math import ceil

from app.schemas.script_schema import MotivationalScript, ScriptEvaluation
from app.providers.huggingface_llm import HuggingFaceLLMProvider
from app.schemas.script_schema import LLMQualityReview

HINDI_MARKERS = {
    "aaj", "aur", "bas", "hai", "har", "kal", "ki", "kitaab", "ko", "par",
    "phir", "roz", "subah", "toh", "usne", "waade", "woh", "yaad", "ek",
}
ENGLISH_MARKERS = {
    "action", "alarm", "book", "choice", "discipline", "exam", "page", "phone",
    "promise", "scroll", "silent", "small", "student", "time", "turning",
}
COMMON_PHRASES = (
    "never give up",
    "believe in yourself",
    "winners never quit",
    "no pain no gain",
)
FACTUAL_CLAIM_PATTERNS = (
    r"\b\d+(?:\.\d+)?\s*%",
    r"\bstudies show\b",
    r"\bresearch proves\b",
    r"\bscientists (?:say|prove|found)\b",
)
UNSUPPORTED_GUARANTEE_PATTERNS = (
    r"\byou(?:['’]ll| will)\s+(?:definitely\s+)?(?:ace|pass|top|win)\b",
    r"\byou(?:['’]re| are)\s+guaranteed\b",
    r"\bguarantee(?:d|s)?\s+(?:you|that you)\b",
    r"\b(?:always|certainly)\s+(?:succeed|win|pass|ace)\b",
)
STRUCTURAL_LABEL_PATTERN = re.compile(
    r"(?im)(?:^|[.!?])\s*(?:hook|problem|conflict|turning point|resolution|closing message)"
    r"\s*:?(?=\s*(?:\n|$))"
)
CAPTION_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "he", "her", "his", "i", "in", "is", "it", "its", "me", "of", "on", "or",
    "our", "she", "so", "that", "the", "their", "them", "they", "this", "to",
    "was", "we", "were", "with", "you", "your", "hai", "haii", "ka", "ki", "ko",
    "mein", "ne", "par", "se", "tha", "thi", "toh", "woh",
}
CAPTION_WORD_PATTERN = re.compile(r"[a-z0-9\u0900-\u097f]+", re.IGNORECASE)
STORY_STAGES = (
    "hook",
    "problem",
    "conflict",
    "turning point",
    "resolution",
    "closing message",
)


def evaluate_script(
    script: MotivationalScript,
    threshold: float = 7.5,
    llm_provider: HuggingFaceLLMProvider | None = None,
    speaking_rate_wps: float = 2.5,
) -> ScriptEvaluation:
    """Evaluate content and production readiness with deterministic safeguards plus HF review."""
    if not 0 <= threshold <= 10:
        raise ValueError("Quality threshold must be between 0 and 10.")
    if not 1 <= speaking_rate_wps <= 5:
        raise ValueError("Speaking rate must be between 1 and 5 words per second.")

    narration = script.narration.lower()
    tokens = re.findall(r"[a-z]+", narration)
    token_set = set(tokens)
    has_hindi = bool(HINDI_MARKERS & token_set) or bool(re.search(r"[\u0900-\u097f]", narration))
    has_english = bool(ENGLISH_MARKERS & token_set)
    language_consistency = has_hindi and has_english

    beat_labels = tuple(beat.split(":", 1)[0].strip().lower() for beat in script.story_beats)
    story_coherence = beat_labels == STORY_STAGES and len(script.scenes) >= 4
    originality_safeguards = not any(phrase in narration for phrase in COMMON_PHRASES)

    word_count = len(script.narration.split())
    words_per_second = word_count / script.target_duration_seconds
    duration_feasibility = (
        sum(scene.duration_seconds for scene in script.scenes)
        == script.target_duration_seconds
        and 0.7 * speaking_rate_wps <= words_per_second <= 1.25 * speaking_rate_wps
    )

    sentence_text = [
        " ".join(sentence.lower().split())
        for sentence in re.split(r"[.!?]+", narration)
        if sentence.strip()
    ]
    counts = Counter(tokens)
    repeated_ngrams = Counter(
        tuple(tokens[index : index + 6]) for index in range(max(0, len(tokens) - 5))
    )
    repetition = (
        len(sentence_text) == len(set(sentence_text))
        and (not counts or max(counts.values()) <= max(5, len(tokens) // 10))
        and not any(count > 1 for count in repeated_ngrams.values())
    )
    motivational_message = bool(script.closing_message.strip()) and len(
        script.closing_message.split()
    ) >= 5
    scene_completeness = 4 <= len(script.scenes) <= 7 and all(
        scene.visual_description.strip()
        and scene.narration.strip()
        and scene.caption.strip()
        and scene.transition.strip()
        and scene.audio_direction.strip()
        for scene in script.scenes
    )
    unsupported_factual_assertions = not any(
        re.search(pattern, " ".join((script.narration, script.description)), re.IGNORECASE)
        for pattern in FACTUAL_CLAIM_PATTERNS
    )
    caption_scene_alignment = all(
        _caption_matches_scene(scene.caption, scene.narration)
        for scene in script.scenes
    )
    structural_labels_absent = not STRUCTURAL_LABEL_PATTERN.search(script.narration)
    unsupported_guarantees = not any(
        re.search(pattern, script.narration, re.IGNORECASE)
        for pattern in UNSUPPORTED_GUARANTEE_PATTERNS
    )

    checks = {
        "language_consistency": language_consistency,
        "story_coherence": story_coherence,
        "originality_safeguards": originality_safeguards,
        "duration_feasibility": duration_feasibility,
        "repetition": repetition,
        "motivational_message": motivational_message,
        "scene_completeness": scene_completeness,
        "caption_scene_alignment": caption_scene_alignment,
        "structural_labels_absent": structural_labels_absent,
        "unsupported_factual_assertions": unsupported_factual_assertions,
        "unsupported_guarantees": unsupported_guarantees,
    }
    llm_feedback: list[str] = []
    if llm_provider is not None:
        review = llm_provider.generate_structured(
            LLMQualityReview,
            "Review this original short-form script for language consistency, story coherence, "
            "copying risk, motivational clarity, and unsupported factual assertions. Be conservative. "
            "This is not an engagement or audience-performance prediction.",
            f"Topic: {script.topic}\nLanguage: {script.language}\nStory beats: {script.story_beats}\n"
            f"Narration: {script.narration}\nDescription: {script.description}",
            max_tokens=800,
        )
        for name in (
            "language_consistency",
            "story_coherence",
            "originality_safeguards",
            "motivational_message",
            "unsupported_factual_assertions",
        ):
            checks[name] = checks[name] and getattr(review, name)
        llm_feedback = review.feedback
    score = round(10 * sum(checks.values()) / len(checks), 2)
    feedback = [
        f"Address the {name.replace('_', ' ')} check."
        for name, passed in checks.items()
        if not passed
    ]
    if not checks["language_consistency"]:
        feedback[0] = (
            "Rewrite the narration as natural Hinglish with meaningful romanized Hindi and English; "
            "do not merely label an English script as Hinglish."
        )
    feedback.extend(llm_feedback)
    return ScriptEvaluation(
        quality_score=score,
        threshold=threshold,
        passed=score >= threshold and all(checks.values()),
        **checks,
        feedback=feedback,
    )


def _caption_matches_scene(caption: str, narration: str) -> bool:
    """Require a caption to share meaningful words with its own spoken scene."""
    caption_words = {
        word for word in CAPTION_WORD_PATTERN.findall(caption.lower())
        if word not in CAPTION_STOP_WORDS and len(word) > 2
    }
    narration_words = {
        word for word in CAPTION_WORD_PATTERN.findall(narration.lower())
        if word not in CAPTION_STOP_WORDS and len(word) > 2
    }
    if not caption_words or not narration_words:
        return False
    shared_words = caption_words & narration_words
    return len(shared_words) >= max(1, ceil(len(caption_words) * 0.25))