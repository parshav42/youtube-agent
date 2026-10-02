"""Transparent heuristic script checks; scores are not engagement predictions."""

import re
from collections import Counter
from math import ceil

from app.config import get_quality_threshold
from app.schemas.script_schema import MotivationalScript, ScriptEvaluation
from app.providers.huggingface_llm import HuggingFaceLLMProvider
from app.schemas.script_schema import LLMQualityReview

HINDI_MARKERS = {
    "aage", "aaj", "aata", "ab", "abhi", "aaye", "aaya", "agle", "apna",
    "apne", "apni", "aur", "awaaz", "baad", "baaki", "baja", "baje", "baar",
    "baitha", "baithi", "badhi", "bade", "bas", "bhi", "bina", "bola",
    "chhoda", "chhodi", "chhota", "chhoti", "daba", "dabana", "dabaya", "de",
    "dilati", "din", "diya", "dheere", "ek", "gaya", "gayi", "hai", "hain",
    "har", "hafte", "hi", "himmat", "ho", "hote", "hua", "hui", "hum",
    "iska", "isse", "is", "itna", "jaise", "jab", "jo", "ka", "kaam", "kam",
    "kal", "kar", "karne", "karega", "karta", "karungi", "karke", "kaha",
    "ke", "ki", "kiye", "kiya", "kitaab", "ko", "koshish", "kuch", "khud",
    "kholna", "laga", "lekin", "liya", "liye", "magar", "maine", "mein",
    "mera", "meri", "mila", "mushkil", "na", "nahi", "ne", "neend", "paas",
    "par", "pehle", "phir", "padh", "padha", "padhai", "padhna", "raha",
    "rakha", "rakho", "roz", "sa", "saamne", "sach", "sakte", "se", "shaam",
    "shuru", "sirf", "socha", "subah", "tab", "tareef", "thak", "thakan",
    "tha", "thaa", "thi", "thoda", "toh", "todne", "unka", "uthkar",
    "uthungi", "uska", "uske", "uski", "usko", "usne", "waada", "waade",
    "woh", "yaad", "yahan", "yeh", "zindagi", "karunga", "likha", "chamka",
    "sunke", "sapne", "hote",
}
ENGLISH_MARKERS = {
    "a", "about", "again", "alarm", "all", "always", "and", "are", "as", "at",
    "achievable", "back", "because", "before", "begin", "boost", "book", "but",
    "button", "by", "can", "celebrate", "change", "choice", "clerk", "confidence",
    "confusing", "day", "desk", "diary", "did", "discipline", "distract", "double",
    "do", "down", "each", "end", "exam", "face", "feel", "first", "focus",
    "for", "from", "get", "give", "goals",
    "i", "if", "in", "into", "is", "it", "its", "just", "keep", "know", "last",
    "jog", "life", "like", "little", "look", "made", "make", "manager", "me",
    "mind", "minute", "more", "morning", "move", "my", "need", "never", "new", "next", "not",
    "now", "of", "off", "on", "one", "only", "open", "or", "other", "our",
    "line", "lines", "note", "notes", "notification", "notifications", "out",
    "page", "phone", "place", "plan", "promise", "put", "read", "ready",
    "practice", "pressure", "productivity", "punctuality", "rhythm", "right",
    "consistency", "routine", "said", "same", "say", "see", "set", "she", "small",
    "so", "sofa", "some", "steady",
    "something", "start", "started", "step", "still", "stop", "student", "study",
    "take", "tell", "than", "that", "the", "their", "then", "there", "they",
    "scroll", "scrolling", "sentence", "side", "slowly", "smiled", "snooze",
    "thing", "think", "this", "those", "thought", "through", "time", "timer",
    "tired", "to", "today", "together", "too", "try", "turning", "untouched",
    "up", "us", "use", "very", "wait", "want", "was", "way", "we", "were",
    "what", "when", "where", "which", "will", "with", "work", "would", "write",
    "walk", "week", "yoga", "year", "you", "your", "improved", "push", "ups",
}
COMMON_PHRASES = (
    "never give up",
    "believe in yourself",
    "winners never quit",
    "no pain no gain",
)
FACTUAL_CLAIM_PATTERNS = (
    r"\b\d+(?:\.\d+)?\s*%",
    r"\bmost\s+(?:successful|people|students|professionals)\b",
    r"\b(?:productivity|grades?|scores?|performance)\s+(?:double[ds]?|triple[ds]?|improved|increased)\b",
    r"\b(?:productivity|grades?|scores?|performance)\s+(?:doubled|tripled|improved|increased)\b",
    r"\b(?:studies?|research|science|scientists?)\b",
    r"\b(?:according to|experts? (?:say|agree|prove)|professors?)\b",
    r"\b(?:dr\.?|doctor|prof(?:essor)?\.?)\s+[a-z][a-z'-]+\b",
    r"\b(?:university|universities|college|institute)\b",
    r"\b(?:harvard|stanford|oxford|cambridge|mit)\b",
    r"\b(?:medical|health|disease|diagnos(?:is|e)|treatment|therapy|cure|medication|blood pressure|mental health|anxiety|depression)\b",
    r"\b(?:19|20)\d{2}\b",
)
UNSUPPORTED_GUARANTEE_PATTERNS = (
    r"\bif\s+you\b[^.!?\n]{0,120}\byou\s+will\b",
    r"\byou(?:['’]ll| will)\s+(?:definitely\s+)?(?:ace|pass|top|win)\b",
    r"\byou(?:['’]ll| will)\s+(?:definitely\s+)?(?:achieve|succeed|become|get)\b",
    r"\byou(?:['’]re| are)\s+guaranteed\b",
    r"\bguarantee(?:d|s)?\s+(?:you|that you)\b",
    r"\b(?:always|certainly)\s+(?:succeed|win|pass|ace)\b",
    r"\bthis\s+will\s+(?:change|transform|fix)\s+your\s+life\b",
    r"\byour\s+life\s+will\s+(?:change|transform)\b",
)
NARRATION_WORD_PATTERN = re.compile(r"[a-z]+|[\u0900-\u097f]+", re.IGNORECASE)
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
    threshold: float | None = None,
    llm_provider: HuggingFaceLLMProvider | None = None,
    speaking_rate_wps: float = 2.5,
) -> ScriptEvaluation:
    """Evaluate content and production readiness with deterministic safeguards plus HF review."""
    threshold = get_quality_threshold() if threshold is None else threshold
    if not 0 <= threshold <= 100:
        raise ValueError("Quality threshold must be between 0 and 100.")
    if not 1 <= speaking_rate_wps <= 5:
        raise ValueError("Speaking rate must be between 1 and 5 words per second.")

    narration = script.narration.lower()
    tokens = re.findall(r"[a-z]+", narration)
    words = NARRATION_WORD_PATTERN.findall(narration)
    hindi_word_count = sum(
        "\u0900" <= word[0] <= "\u097f" or word in HINDI_MARKERS
        for word in words
    )
    english_word_count = sum(
        word in ENGLISH_MARKERS and word not in HINDI_MARKERS
        for word in words
    )
    language_consistency = bool(words) and (
        hindi_word_count / len(words) >= 0.30
        and english_word_count / len(words) >= 0.20
    )

    beat_labels = tuple(beat.split(":", 1)[0].strip().lower() for beat in script.story_beats)
    story_coherence = beat_labels == STORY_STAGES and len(script.scenes) >= 4
    originality_safeguards = not any(phrase in narration for phrase in COMMON_PHRASES)

    word_count = len(script.narration.split())
    words_per_second = word_count / script.target_duration_seconds
    duration_feasibility = (
        90 <= word_count <= 140
        and sum(scene.duration_seconds for scene in script.scenes)
        == script.target_duration_seconds
        and 0.7 * speaking_rate_wps <= words_per_second <= 1.4 * speaking_rate_wps
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
    score = round(100 * sum(checks.values()) / len(checks), 2)
    feedback = []
    for name, passed in checks.items():
        if passed:
            continue
        if name == "language_consistency":
            feedback.append(
                "Rewrite as natural Hinglish with Hindi sentence structure; ensure at least 30% "
                "Hindi words and 20% common English words, and verify both percentages."
            )
        elif name == "duration_feasibility":
            feedback.append(
                f"Revise narration to 90-140 words for a {script.target_duration_seconds}-second "
                "normal-paced script, then recount before returning."
            )
        elif name == "unsupported_factual_assertions":
            feedback.append(
                "Remove statistics, research claims, unsupported results, named experts or studies, "
                "disputed historical details, and medical or health claims."
            )
        else:
            feedback.append(f"Address the {name.replace('_', ' ')} check.")
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