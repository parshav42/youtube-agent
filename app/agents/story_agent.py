"""Shape a generated draft into a coherent six-beat narrative arc."""

from app.schemas.script_schema import MotivationalScript
from app.schemas.script_schema import StoryBeatPlan
from app.providers.huggingface_llm import HuggingFaceLLMProvider

STORY_BEAT_LABELS = (
    "Hook",
    "Problem",
    "Conflict",
    "Turning point",
    "Resolution",
    "Closing message",
)


def structure_story(
    script: MotivationalScript, llm_provider: HuggingFaceLLMProvider | None = None
) -> MotivationalScript:
    """Describe the hook-to-resolution progression using the draft's scenes."""
    if llm_provider is not None:
        plan = llm_provider.generate_structured(
            StoryBeatPlan,
            "Plan coherent story structure without adding unsupported facts or copied expressions. "
            "Beat labels belong in story_beats only and must never be inserted into the spoken narration.",
            "Create six concise ordered beats labeled exactly Hook, Problem, Conflict, Turning point, "
            f"Resolution, Closing message.\nTopic: {script.topic}\nNarration: {script.narration}\n"
            f"Closing message: {script.closing_message}",
            max_tokens=700,
        )
        beats = [
            f"{label}: {beat.partition(':')[2].strip() or beat.strip()}"
            for label, beat in zip(STORY_BEAT_LABELS, plan.story_beats, strict=True)
        ]
        return MotivationalScript.model_validate(script.model_copy(update={"story_beats": beats}))
    scene_text = [scene.narration for scene in script.scenes]
    midpoint = max(1, len(scene_text) // 2)
    story_beats = [
        f"Hook: {script.opening_hook}",
        f"Problem: {scene_text[1] if len(scene_text) > 1 else scene_text[0]}",
        f"Conflict: {scene_text[midpoint]}",
        f"Turning point: {scene_text[min(midpoint + 1, len(scene_text) - 1)]}",
        f"Resolution: {scene_text[-1]}",
        f"Closing message: {script.closing_message}",
    ]
    return MotivationalScript.model_validate(
        script.model_copy(update={"story_beats": story_beats})
    )