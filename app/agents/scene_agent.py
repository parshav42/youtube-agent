"""Prepare scene timings and 9:16 production directions from script narration."""

from app.schemas.script_schema import MotivationalScript, ScriptScene
from app.providers.huggingface_llm import HuggingFaceLLMProvider
from app.schemas.script_schema import ScenePlan


def plan_scenes(
    script: MotivationalScript, llm_provider: HuggingFaceLLMProvider | None = None
) -> MotivationalScript:
    """Balance timings and add concise, original vertical-video directions."""
    if llm_provider is not None:
        existing = [scene.model_dump(mode="json") for scene in script.scenes]
        plan = llm_provider.generate_structured(
            ScenePlan,
            "Plan original visual and sound details for licensed vertical 9:16 video production. "
            "Do not name or imitate creators or use copyrighted characters or footage.",
            "Preserve scene count, scene numbers, exact durations, and narration strings from this "
            "source plan. Each caption must accurately summarize the spoken narration in that same "
            "scene and share its key subject/action words. Do not use a caption about a later or "
            "different scene. Improve visual description, transition, "
            "and audio direction. Use no URLs.\n"
            f"Target duration: {script.target_duration_seconds}\nSource scenes: {existing}",
            max_tokens=2000,
        )
        revised_scenes = [
            item.model_copy(
                update={
                    "visual_description": (
                        item.visual_description
                        if "9:16" in item.visual_description
                        else f"Vertical 9:16, {item.visual_description}"
                    ),
                    "duration_seconds": source.duration_seconds,
                    "narration": source.narration,
                }
            )
            for item, source in zip(plan.scenes, script.scenes, strict=True)
        ]
        return MotivationalScript.model_validate(
            script.model_copy(update={"scenes": revised_scenes})
        )
    count = min(7, max(4, len(script.scenes)))
    source_scenes = script.scenes[:count]
    segments = [scene.narration for scene in source_scenes]
    base, remainder = divmod(script.target_duration_seconds, count)
    scenes = [
        ScriptScene(
            scene_number=index + 1,
            duration_seconds=base + (index < remainder),
            narration=segment,
            visual_description=(
                source_scenes[index].visual_description
                if "9:16" in source_scenes[index].visual_description
                else f"Vertical 9:16, {source_scenes[index].visual_description}"
            ),
            caption=source_scenes[index].caption,
            transition=source_scenes[index].transition,
            audio_direction=source_scenes[index].audio_direction,
        )
        for index, segment in enumerate(segments)
    ]
    return MotivationalScript.model_validate(script.model_copy(update={"scenes": scenes}))