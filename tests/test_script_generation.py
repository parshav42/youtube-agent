"""Validation tests for structured script requests and output."""

import pytest
from pydantic import ValidationError

from app.agents.scene_agent import plan_scenes
from app.agents.script_agent import HuggingFaceScriptGenerator, split_narration_for_scenes
from app.schemas.script_schema import MotivationalScript, ScriptDraft, ScriptRequest, ScriptSceneDraft


def make_script(duration: int = 30) -> dict[str, object]:
    segments = [
        "Subah alarm baja ",
        "aur usne snooze daba diya. ",
        "Discipline starts ",
        "with one ",
        "small promise.",
    ]
    narration = "".join(segments)
    scene_count = 5
    base, remainder = divmod(duration, scene_count)
    scenes = []
    for index in range(scene_count):
        scenes.append(
            {
                "scene_number": index + 1,
                "duration_seconds": base + (index < remainder),
                "narration": segments[index],
                "visual_description": f"Vertical 9:16 shot {index + 1}",
                "caption": f"Caption {index + 1}",
                "transition": "Cut",
                "audio_direction": "Soft piano builds",
            }
        )
    return {
        "topic": "discipline",
        "language": "Hinglish",
        "target_duration_seconds": duration,
        "title_candidates": ["One Small Promise", "Kal Nahi, Aaj", "The First Ten Minutes"],
        "opening_hook": "Subah alarm baja aur usne snooze daba diya.",
        "narration": narration,
        "story_beats": [
            "Hook: Alarm rings",
            "Problem: Work gets postponed",
            "Conflict: The pattern repeats",
            "Turning point: Phone is set aside",
            "Resolution: One small task gets done",
            "Closing message: Keep one promise today",
        ],
        "scenes": scenes,
        "closing_message": "Aaj khud se kiya ek chhota promise nibhao.",
        "description": "An original story about showing up.",
        "hashtags": ["#Discipline", "#Hinglish"],
        "quality_score": 8,
    }


def test_script_request_enforces_short_duration_and_required_topic() -> None:
    assert ScriptRequest(topic="discipline").target_duration_seconds == 35
    with pytest.raises(ValidationError):
        ScriptRequest(topic="", target_duration_seconds=29)


@pytest.mark.parametrize("duration", [30, 35, 45])
def test_script_accepts_valid_structure_and_balanced_scene_durations(duration: int) -> None:
    script = MotivationalScript.model_validate(make_script(duration))

    assert len(script.scenes) == 5
    assert sum(scene.duration_seconds for scene in script.scenes) == duration


def test_script_rejects_scene_duration_mismatch_and_incomplete_scene() -> None:
    invalid_duration = make_script()
    invalid_duration["scenes"][0]["duration_seconds"] += 1
    with pytest.raises(ValidationError, match="sum to the target duration"):
        MotivationalScript.model_validate(invalid_duration)

    incomplete_scene = make_script()
    del incomplete_scene["scenes"][0]["audio_direction"]
    with pytest.raises(ValidationError):
        MotivationalScript.model_validate(incomplete_scene)


def test_script_rejects_wrong_number_of_scenes() -> None:
    invalid = make_script()
    invalid["scenes"] = invalid["scenes"][:3]
    with pytest.raises(ValidationError):
        MotivationalScript.model_validate(invalid)


def test_script_rejects_duplicate_visual_scenes() -> None:
    invalid = make_script()
    invalid["scenes"][1]["visual_description"] = invalid["scenes"][0][
        "visual_description"
    ].upper()

    with pytest.raises(ValidationError, match="visual descriptions must be distinct"):
        MotivationalScript.model_validate(invalid)


def test_script_rejects_scene_text_that_differs_by_one_space() -> None:
    invalid = make_script()
    invalid["scenes"][0]["narration"] += " "

    with pytest.raises(ValidationError, match="exactly match"):
        MotivationalScript.model_validate(invalid)


def test_split_narration_preserves_exact_text_and_contiguous_segments() -> None:
    narration = "  Wait...  kal nahi,\naaj se. Keep going!!  "

    segments = split_narration_for_scenes(narration, 5)

    assert len(segments) == 5
    assert "".join(segments) == narration
    assert segments[0].startswith("  ")
    assert segments[-1].endswith("  ")


def test_hf_generation_uses_full_narration_as_scene_source() -> None:
    narration = "Wait...  kal nahi,\naaj se. Keep going!!"
    draft = ScriptDraft(
        topic="discipline",
        language="Hinglish",
        target_duration_seconds=35,
        title_candidates=["Wait", "Aaj Se", "One Small Promise"],
        opening_hook="Wait...",
        narration=narration,
        story_beats=[
            "Hook: Wait.",
            "Problem: Kal nahi.",
            "Conflict: Aaj se.",
            "Turning point: Begins now.",
            "Resolution: Keeps going.",
            "Closing message: Take one step.",
        ],
        scenes=[
            ScriptSceneDraft(
                scene_number=index,
                duration_seconds=1,
                visual_description=f"Vertical 9:16 shot {index}",
                caption=f"Caption {index}",
                transition="Cut",
                audio_direction="Piano",
            )
            for index in range(1, 6)
        ],
        closing_message="Take one step today.",
        description="An original story.",
        hashtags=["#Discipline"],
    )

    class HFResponseFixture:
        provider_name = "test fixture for schema regression"

        def generate_structured(self, response_model, *_args, **_kwargs):
            assert response_model is ScriptDraft
            return draft

    script = HuggingFaceScriptGenerator(HFResponseFixture()).generate(
        ScriptRequest(topic="discipline", target_duration_seconds=35)
    )

    assert script.narration == narration
    assert "".join(scene.narration for scene in script.scenes) == narration
    assert script.scenes[0].narration.startswith("Wait...")
    assert "  " in script.scenes[0].narration or "  " in script.scenes[1].narration
    assert "  " in "".join(scene.narration for scene in script.scenes)
    assert [scene.duration_seconds for scene in script.scenes] == [7, 7, 7, 7, 7]
    assert sum(scene.duration_seconds for scene in script.scenes) == 35


def test_scene_planner_preserves_generated_directions_and_adds_vertical_format() -> None:
    script = MotivationalScript.model_validate(make_script())
    first_scene = script.scenes[0].model_copy(
        update={
            "visual_description": "A rooftop at dawn, with a student opening a notebook.",
            "caption": "Start before you feel ready.",
            "audio_direction": "City ambience under a restrained piano note.",
        }
    )
    script = script.model_copy(update={"scenes": [first_scene, *script.scenes[1:]]})

    planned = plan_scenes(script)

    assert planned.scenes[0].visual_description.startswith("Vertical 9:16,")
    assert "rooftop at dawn" in planned.scenes[0].visual_description
    assert planned.scenes[0].caption == "Start before you feel ready."
    assert planned.scenes[0].audio_direction == "City ambience under a restrained piano note."