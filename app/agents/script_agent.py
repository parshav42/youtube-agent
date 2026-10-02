"""Hugging Face-backed structured script generation and explicit test provider."""

import re
from typing import Protocol

from app.config import get_speaking_rate_wps
from app.providers.huggingface_llm import HuggingFaceLLMProvider
from app.providers.llm_factory import build_llm_provider
from app.schemas.script_schema import (
    MotivationalScript,
    ScriptDraft,
    ScriptRequest,
    ScriptScene,
)


class ScriptGenerator(Protocol):
    """Provider contract shared by configured generation and injected test doubles."""

    provider_name: str
    is_mock: bool

    def generate(
        self, request: ScriptRequest, feedback: str = ""
    ) -> MotivationalScript:
        """Generate a validated script, optionally addressing review feedback."""


class HuggingFaceScriptGenerator:
    """Generate validated script JSON through Hugging Face Inference Providers."""

    is_mock = False

    def __init__(self, provider: HuggingFaceLLMProvider) -> None:
        self.llm = provider
        self.provider_name = provider.provider_name

    def generate(
        self, request: ScriptRequest, feedback: str = ""
    ) -> MotivationalScript:
        speaking_rate_wps = get_speaking_rate_wps()
        prompt = f"""Create a fully original YouTube Shorts motivational story.
Topic: {request.topic}
Language: Natural Hindi-English Hinglish (romanized Hindi mixed with English).
Duration: exactly {request.target_duration_seconds} seconds.

Requirements:
- The story must be about ONE specific fictional person with a name. Show their struggle and small win. Do not generalize.
- Example of a GOOD line: 'Rohan ne ek din decide kiya ki woh roz 5 baje uthega.'
- Example of a BAD line: 'Studies show 90% of successful people wake up early.'
- Write in natural Hinglish: use Hindi sentence structure with common English words interspersed, in Roman Hindi and/or Devanagari. Do NOT write pure English or pure Hindi.
- Count words in the final narration: at least 30% must be Hindi and at least 20% must be common English. Use English words such as alarm, phone, page, timer, focus, routine, practice, and confidence.
- Use at least 8 distinct everyday Hindi words across the narration (for example: aaj, aur, bas, hai, har, kal, ki, ko, par, phir, roz, subah, toh, usne, woh, ek). Do not insert these as a list.
- Tell one cinematic, emotionally grounded story. Make the small win a specific action by the fictional character, not a measurable result or claim about productivity, grades, health, or future success. Show transformation through narrative, not through claims.
- Include a compelling opening, one clear message, and a memorable ending.
- Keep total narration between 90 and 140 words, appropriate for 30-45 seconds at a normal pace.
- Before returning JSON, count narration words, verify the 30% Hindi and 20% English minimums, and scan for every forbidden category listed in the system prompt. Revise if any check fails, then repeat all checks.
- Write the entire narration exactly once in the top-level narration field. This is the only
  authoritative narration text. Preserve its punctuation and spacing.
- Use exactly these story-beat labels in order in story_beats only: Hook, Problem, Conflict,
    Turning point, Resolution, Closing message. Never speak or insert these labels into narration.
- Include 4-7 scene objects with consecutive scene numbers. The application assigns exact
    scene durations so they total {request.target_duration_seconds} seconds. Scene objects contain visual_description, caption,
  transition, and audio_direction only. Do not include or rewrite narration in scene objects;
  the application will split the canonical narration into exact consecutive scene segments.
- Write each scene caption as a short, accurate summary of that scene's own spoken words. Reuse
    key subject/action words from that scene, not a different scene's events.
- Make each visual description suitable for original, licensed 9:16 production.
- Do not name or imitate creators, or quote existing scripts.
- The quality score is initialized to 0 and will be assigned by the evaluation stage.
Revision feedback: {feedback or "No revision requested."}
"""
        draft = self.llm.generate_structured(
            ScriptDraft,
            """You write original, grounded motivational stories. Return only schema-compliant content.
Write in natural Hinglish. Use Hindi sentence structure with common English words interspersed.
Do NOT write pure English or pure Hindi. For the final narration of N words, include at least
ceil(0.30 * N) Hindi words and ceil(0.20 * N) common English words, such as alarm, phone, page,
timer, focus, routine, practice, and confidence. Count and verify both groups.
The total narration must be between 90 and 140 words. Count the words and verify both language
percentages before returning.

Do NOT include any of the following in the narration:
- Statistics or percentages ('95% of people', 'most successful people')
- Scientific claims ('research shows', 'studies prove', 'science says')
- Universal guarantees ('if you do X, you WILL achieve Y')
- Absolute predictions ('this will change your life')
- Named experts, universities, or studies
- Historical facts that could be disputed
- Medical or health claims of any kind

Instead, tell a specific personal story about a fictional character.
Show transformation through narrative, not through claims.""",
            prompt,
            max_tokens=2200,
        )
        segments = split_narration_for_scenes(draft.narration, len(draft.scenes))
        base_duration, extra_seconds = divmod(
            request.target_duration_seconds, len(draft.scenes)
        )
        scenes = [
            ScriptScene(
                **{
                    **scene.model_dump(mode="python"),
                    "duration_seconds": base_duration + (index < extra_seconds),
                },
                narration=segment,
            )
            for index, (scene, segment) in enumerate(
                zip(draft.scenes, segments, strict=True)
            )
        ]
        return MotivationalScript.model_validate(
            {
                **draft.model_dump(exclude={"scenes"}),
                "scenes": scenes,
            }
        )


def split_narration_for_scenes(narration: str, scene_count: int) -> list[str]:
    """Split canonical narration into balanced, exact contiguous source substrings."""
    if not narration.strip():
        raise ValueError("Cannot segment empty narration.")
    if scene_count < 1:
        raise ValueError("scene_count must be at least 1.")
    leading_whitespace = len(narration) - len(narration.lstrip())
    words = list(re.finditer(r"\S+\s*", narration[leading_whitespace:]))
    if len(words) < scene_count:
        raise ValueError("Narration must contain at least one word for every planned scene.")

    boundaries = [0]
    previous = 0
    for scene_index in range(1, scene_count):
        remaining_scenes = scene_count - scene_index
        minimum = previous + 1
        maximum = len(words) - remaining_scenes
        target = round(len(words) * scene_index / scene_count)
        candidates = range(minimum, maximum + 1)
        sentence_ends = [
            boundary
            for boundary in candidates
            if re.search(r"[.!?][\"'’”)]*$", words[boundary - 1].group().rstrip())
        ]
        boundary = min(
            sentence_ends or list(candidates),
            key=lambda value: (abs(value - target), value),
        )
        boundaries.append(boundary)
        previous = boundary
    boundaries.append(len(words))

    segments = []
    for index, (start_word, end_word) in enumerate(
        zip(boundaries[:-1], boundaries[1:], strict=True)
    ):
        start = leading_whitespace if start_word == 0 else leading_whitespace + words[start_word].start()
        end = leading_whitespace + words[end_word - 1].end()
        segment = narration[start:end]
        if index == 0 and leading_whitespace:
            segment = narration[:leading_whitespace] + segment
        segments.append(segment)
    if "".join(segments) != narration:
        raise RuntimeError("Narration segmentation did not preserve the exact source text.")
    return segments


class MockScriptGenerator:
    """Deterministic fixture provider; production never selects it implicitly."""

    provider_name = "explicit test fixture"
    is_mock = True

    def generate(
        self, request: ScriptRequest, feedback: str = ""
    ) -> MotivationalScript:
        segments = [
            "Subah alarm baja, Aarav ne snooze dabaya, phir woh bola, kal nahi, aaj ek page se start karunga. ",
            "Exam paas tha, kitaab saamne thi, par first page kholna mushkil laga; notes untouched, mind tired tha. ",
            "Phone phir chamka, notifications aaye, aur woh scroll karne laga; phir usne apna timer set kiya, bas itna hi. ",
            "Is baar, usne phone side rakha aur sirf ek page padha; lines confusing thi, lekin Aarav ne slowly read kiya, sentence by sentence. ",
            "Page khatam hua, exam abhi bhi baaki tha, par Aarav ko thoda confidence mila; kal ke liye usne next small step likha, and smiled.",
        ]
        visuals = [
            "Close-up of a student silencing an alarm in a dim room.",
            "Study desk, unopened book, morning light across the page.",
            "Phone lighting a quiet room beside a waiting notebook.",
            "Hand places phone face down and opens one book page.",
            "Student returns to the desk as soft sunrise fills the frame.",
        ]
        captions = [
            "Aarav snoozes, then chooses one page today.",
            "The first page feels difficult beside his notes.",
            "Phone chamka, timer set, scrolling stopped.",
            "He puts the phone aside and reads one page.",
            "Aarav writes one small next step.",
        ]
        transitions = ["Fade in", "Slow dissolve", "Match cut", "Gentle push", "Fade to black"]
        audio = [
            "Low room tone; a single muted alarm note.",
            "Sparse piano begins under natural room tone.",
            "A subtle pulse adds tension.",
            "Warm piano chord as the page turns.",
            "Resolve to a restrained piano note and quiet room tone.",
        ]
        base, remainder = divmod(request.target_duration_seconds, len(segments))
        scenes = [
            {
                "scene_number": index + 1,
                "duration_seconds": base + (index < remainder),
                "narration": segment,
                "visual_description": f"Vertical 9:16, {visuals[index]}",
                "caption": captions[index],
                "transition": transitions[index],
                "audio_direction": audio[index],
            }
            for index, segment in enumerate(segments)
        ]
        beats = [
            "Hook: Aarav snoozes his alarm despite yesterday's promise.",
            "Problem: His exam is near while the book stays closed.",
            "Conflict: Another evening offers the familiar scroll-or-study choice.",
            "Turning point: He silences the phone and opens one page.",
            "Resolution: Ten minutes become another page and a repeatable action.",
            "Closing message: Keep one small promise to yourself each day.",
        ]
        notes = ["Explicit deterministic test fixture; not a live model response."]
        if feedback:
            notes.append(f"Test revision input: {feedback[:240]}")
        return MotivationalScript(
            topic=request.topic,
            language=request.language,
            target_duration_seconds=request.target_duration_seconds,
            title_candidates=["The Promise After Snooze", "Bas Ek Page", "One Small Choice"],
            opening_hook=segments[0],
            narration="".join(segments),
            story_beats=beats,
            scenes=scenes,
            closing_message="Aaj khud se kiya ek chhota promise nibhao.",
            description=f"An original Hinglish story about {request.topic}.",
            hashtags=["#Discipline", "#Consistency", "#Hinglish"],
            quality_score=0,
            revision_notes=notes,
        )


def build_script_generator(
    provider: HuggingFaceLLMProvider | None = None,
) -> HuggingFaceScriptGenerator:
    """Construct the production generator; missing provider configuration is an error."""
    return HuggingFaceScriptGenerator(provider or build_llm_provider())