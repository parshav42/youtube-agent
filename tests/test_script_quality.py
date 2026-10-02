"""Tests for script quality checks and threshold routing inputs."""

import pytest

from app.agents.quality_agent import evaluate_script
from app.config import get_daily_pipeline_settings, get_quality_threshold, get_speaking_rate_wps
from app.agents.scene_agent import plan_scenes
from app.agents.script_agent import MockScriptGenerator
from app.agents.story_agent import structure_story
from app.schemas.script_schema import ScriptEvaluation, ScriptRequest, StoryBeatPlan


def make_script():
    draft = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    return plan_scenes(structure_story(draft))


def test_mock_script_passes_all_heuristic_quality_checks() -> None:
    evaluation = evaluate_script(make_script(), threshold=75)

    assert evaluation.passed is True
    assert evaluation.quality_score == 100
    assert evaluation.threshold == 75
    assert evaluation.feedback == []
    assert evaluation.language_consistency
    assert evaluation.story_coherence
    assert evaluation.originality_safeguards
    assert evaluation.duration_feasibility
    assert evaluation.repetition
    assert evaluation.motivational_message
    assert evaluation.scene_completeness
    assert evaluation.caption_scene_alignment
    assert evaluation.structural_labels_absent
    assert evaluation.unsupported_factual_assertions
    assert evaluation.unsupported_guarantees


def test_story_structure_canonicalizes_model_beat_labels() -> None:
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))

    class LabelDriftProvider:
        def generate_structured(self, response_model, system_prompt, user_prompt, *, max_tokens):
            assert response_model is StoryBeatPlan
            return StoryBeatPlan(
                story_beats=[
                    "Opening: first",
                    "Challenge: second",
                    "Obstacle: third",
                    "Change: fourth",
                    "Outcome: fifth",
                    "Takeaway: sixth",
                ]
            )

    structured = structure_story(script, LabelDriftProvider())

    assert [beat.split(":", 1)[0] for beat in structured.story_beats] == [
        "Hook",
        "Problem",
        "Conflict",
        "Turning point",
        "Resolution",
        "Closing message",
    ]
    assert structured.story_beats[-1] == "Closing message: sixth"


def test_quality_evaluator_rejects_inconsistent_language_and_statistics() -> None:
    script = make_script()
    english_scenes = [
        scene.model_copy(update={"narration": f"This student begins again in scene {scene.scene_number}."})
        for scene in script.scenes
    ]
    narration = "".join(scene.narration for scene in english_scenes)
    script = script.model_copy(update={"narration": narration, "scenes": english_scenes})
    script = script.model_copy(update={"description": "Studies show 90% improve."})

    evaluation = evaluate_script(script)

    assert evaluation.language_consistency is False
    assert evaluation.unsupported_factual_assertions is False
    assert evaluation.passed is False


def test_any_failed_safeguard_triggers_revision_even_above_threshold() -> None:
    script = make_script()
    scenes = [
        scene.model_copy(
            update={
                "narration": scene.narration + " Never give up."
                if scene.scene_number == len(script.scenes)
                else scene.narration
            }
        )
        for scene in script.scenes
    ]
    script = script.model_copy(
        update={"scenes": scenes, "narration": " ".join(scene.narration for scene in scenes)}
    )

    evaluation = evaluate_script(script)

    assert evaluation.quality_score >= evaluation.threshold
    assert evaluation.originality_safeguards is False
    assert evaluation.passed is False


def test_quality_threshold_must_be_in_range() -> None:
    with pytest.raises(ValueError, match="between 0 and 100"):
        evaluate_script(make_script(), threshold=100.1)


def test_studies_show_in_narration_fails_unsupported_factual_assertions() -> None:
    script = make_script()
    narration = script.narration + " Studies show that everyone succeeds with this habit."
    # Preserve canonical scene text while placing the claim in narration for evaluation.
    script = script.model_copy(update={"narration": narration})

    evaluation = evaluate_script(script)

    assert evaluation.unsupported_factual_assertions is False


def test_pure_hindi_script_fails_language_consistency() -> None:
    script = make_script()
    narration = " ".join("विद्यार्थी रोज़ अपनी किताब खोलकर थोड़ा पढ़ता है" for _ in range(18))
    script = script.model_copy(update={"narration": narration})
    roman_hindi = script.model_copy(
        update={
            "narration": (
                "Aaj subah Rohan ghar par baitha. Usne dhyan se padhna shuru kiya, "
                "par awaaz sunke ruk gaya."
            )
        }
    )

    assert evaluate_script(script).language_consistency is False
    assert evaluate_script(roman_hindi).language_consistency is False


def test_language_consistency_requires_minimum_hindi_and_english_word_shares() -> None:
    script = make_script()
    at_threshold = script.model_copy(
        update={
            "narration": "aaj roz subah page focus Aarav Meera Kabir Riya Neha"
        }
    )
    below_hindi_threshold = script.model_copy(
        update={
            "narration": "aaj roz page focus Aarav Meera Kabir Riya Neha Noor"
        }
    )

    assert evaluate_script(at_threshold).language_consistency
    assert not evaluate_script(below_hindi_threshold).language_consistency


def test_fifty_word_script_fails_duration_feasibility() -> None:
    script = make_script()
    words = script.narration.split()[:50]
    segment_size = len(words) // len(script.scenes)
    segments = [
        " ".join(words[index * segment_size : (index + 1) * segment_size])
        for index in range(len(script.scenes) - 1)
    ]
    segments.append(" ".join(words[(len(script.scenes) - 1) * segment_size :]))
    scenes = [
        scene.model_copy(update={"narration": segment})
        for scene, segment in zip(script.scenes, segments, strict=True)
    ]
    script = script.model_copy(
        update={"narration": " ".join(words), "scenes": scenes}
    )

    evaluation = evaluate_script(script)

    assert len(script.narration.split()) == 50
    assert evaluation.duration_feasibility is False


def test_script_quality_threshold_uses_only_environment_value(monkeypatch) -> None:
    monkeypatch.setenv("MIN_SCRIPT_QUALITY_SCORE", "82.5")
    monkeypatch.setenv("MOTIVATIONOS_QUALITY_THRESHOLD", "4")

    assert get_quality_threshold() == 82.5
    assert get_daily_pipeline_settings()["min_script_quality_score"] == 82.5

    monkeypatch.delenv("MIN_SCRIPT_QUALITY_SCORE")
    assert get_quality_threshold() == 75


def test_legacy_script_scores_are_normalized_to_one_hundred_scale() -> None:
    evaluation = ScriptEvaluation(
        quality_score=8,
        threshold=7.5,
        passed=True,
        language_consistency=True,
        story_coherence=True,
        originality_safeguards=True,
        duration_feasibility=True,
        repetition=True,
        motivational_message=True,
        scene_completeness=True,
        caption_scene_alignment=True,
        structural_labels_absent=True,
        unsupported_factual_assertions=True,
        unsupported_guarantees=True,
    )

    assert evaluation.quality_score == 80
    assert evaluation.threshold == 75


def test_record_three_failure_patterns_are_flagged() -> None:
    base = make_script()
    segments = [
        "Hey everyone, it's time to ace those exams! In the heart of exam season, staying consistent can make all the difference. Meet Priya, a student who faced a big challenge. Hook\n\n",
        "In the heart of exam season, staying consistent can make all the difference. Problem\n\n",
        "Priya was overwhelmed with her studies and started to lose focus. She felt like giving up. Conflict\n\n",
        "Priya was overwhelmed with her studies and started to lose focus. She felt like giving up. Turning point\n\n",
        "But then, her teacher reminded her to stay consistent and not to rush. Priya decided to take it one day at a time. Resolution\n\n",
        "With renewed focus, Priya managed to ace her exams. She proved to herself that consistency is key. Closing message\n\n",
        "So, stay consistent, and you’ll ace those exams too!",
    ]
    captions = [
        "In the heart of exam season, staying consistent can make all the difference.",
        "Priya was overwhelmed with her studies and started to lose focus.",
        "But then, her teacher reminded her to stay consistent and not to rush.",
        "Priya decided to take it one day at a time.",
        "With renewed focus, Priya managed to ace her exams.",
        "She proved to herself that consistency is key.",
        "So, stay consistent, and you’ll ace those exams too!",
    ]
    scenes = [
        scene.model_copy(update={"narration": segment, "caption": caption})
        for scene, segment, caption in zip(base.scenes + [base.scenes[-1]] * 2, segments, captions, strict=True)
    ]
    script = base.model_copy(
        update={"narration": "".join(segments), "scenes": scenes, "target_duration_seconds": 35}
    )

    evaluation = evaluate_script(script)

    assert evaluation.language_consistency is False
    assert evaluation.duration_feasibility is False
    assert evaluation.repetition is False
    assert evaluation.caption_scene_alignment is False
    assert evaluation.structural_labels_absent is False
    assert evaluation.unsupported_guarantees is False
    assert evaluation.passed is False


def test_speaking_rate_is_configurable_and_changes_duration_validation(monkeypatch) -> None:
    script = make_script()
    assert evaluate_script(script, speaking_rate_wps=2.5).duration_feasibility
    assert not evaluate_script(script, speaking_rate_wps=1.5).duration_feasibility

    monkeypatch.setenv("MOTIVATIONOS_SPEAKING_RATE_WPS", "2.1")
    assert get_speaking_rate_wps() == 2.1
    monkeypatch.setenv("MOTIVATIONOS_SPEAKING_RATE_WPS", "7")
    with pytest.raises(ValueError, match="between 1 and 5"):
        get_speaking_rate_wps()


def test_caption_alignment_supports_devanagari_hindi() -> None:
    script = make_script()
    first_scene = script.scenes[0].model_copy(
        update={
            "narration": "विद्यार्थी रोज़ पढ़ाई शुरू करता है।",
            "caption": "विद्यार्थी रोज़ पढ़ता है।",
        }
    )
    scenes = [first_scene, *script.scenes[1:]]
    script = script.model_copy(
        update={
            "narration": "".join(scene.narration for scene in scenes),
            "scenes": scenes,
        }
    )

    assert evaluate_script(script).caption_scene_alignment