"""Integration tests for script generation, revision, and persistence."""

import json
from pathlib import Path

from app.agents.script_agent import MockScriptGenerator, build_script_generator
from app.database.db import get_recent_scripts, get_script_by_id, save_research
from app.graph.workflow import MAX_REVISION_ATTEMPTS, build_workflow
from app.providers.huggingface_llm import HuggingFaceConfigurationError
from app.schemas.script_schema import MotivationalScript, ScriptRequest


class EnglishOnlyGenerator(MockScriptGenerator):
    """Test provider that fails the language check while retaining valid structure."""

    def __init__(self, recover: bool) -> None:
        self.calls = 0
        self.recover = recover
        self.is_mock = True
        self.provider_name = "test generator"

    def generate(self, request: ScriptRequest, feedback: str = "") -> MotivationalScript:
        self.calls += 1
        script = super().generate(request, feedback)
        if self.recover and self.calls > 1:
            return script
        segments = [
            "Each morning, a student silenced the alarm and postponed starting. ",
            "The exam was close, while the untouched book waited on the desk. ",
            "That evening the phone lit up, offering the familiar escape again. ",
            "Instead, the student opened one page and studied for ten minutes. ",
            "Next morning was ordinary; keeping one small promise made the difference.",
        ]
        scenes = [
            scene.model_copy(update={"narration": segment})
            for scene, segment in zip(script.scenes, segments)
        ]
        return MotivationalScript.model_validate(
            script.model_copy(
                update={
                    "narration": "".join(segments),
                    "opening_hook": segments[0],
                    "scenes": scenes,
                }
            )
        )


def test_script_graph_runs_saves_sqlite_and_writes_json(tmp_path) -> None:
    database_path = tmp_path / "motivationos.sqlite3"
    json_directory = tmp_path / "data" / "scripts"
    workflow = build_workflow(
        database_path=str(database_path),
        json_output_directory=json_directory,
        script_generator=MockScriptGenerator(),
    )

    result = workflow.invoke(
        {"script_request": ScriptRequest(topic="discipline", target_duration_seconds=35)}
    )

    script = result["script"]
    record = result["script_record"]
    assert result["errors"] == []
    assert result["evaluation"].passed
    assert result["revision_attempts"] == 0
    assert len(script.scenes) in range(4, 8)
    assert sum(scene.duration_seconds for scene in script.scenes) == 35
    assert record["status"] == "needs_review"
    assert record["reviewed_at"] is None
    assert record["reviewed_by"] is None
    assert record["evaluation"] == result["evaluation"].model_dump(mode="json")
    assert get_script_by_id(record["id"], database_path)["script"] == script.model_dump(mode="json")
    assert get_recent_scripts(database_path)[0]["id"] == record["id"]
    json_content = Path(result["script_json_path"]).read_text(encoding="utf-8")
    assert json.loads(json_content) == script.model_dump(mode="json")


def test_revision_recovers_after_first_quality_failure(tmp_path) -> None:
    generator = EnglishOnlyGenerator(recover=True)
    workflow = build_workflow(
        database_path=str(tmp_path / "revision.sqlite3"), script_generator=generator
    )

    result = workflow.invoke({"script_request": {"topic": "consistency"}})

    assert generator.calls == 2
    assert result["revision_attempts"] == 1
    assert result["evaluation"].passed
    assert any("Revision 1" in note for note in result["script"].revision_notes)


def test_revision_attempts_are_bounded_and_failed_script_is_marked_for_review(tmp_path) -> None:
    generator = EnglishOnlyGenerator(recover=False)
    database_path = tmp_path / "bounded.sqlite3"
    workflow = build_workflow(database_path=str(database_path), script_generator=generator)

    result = workflow.invoke({"script_request": {"topic": "consistency"}})

    assert generator.calls == MAX_REVISION_ATTEMPTS + 1
    assert result["revision_attempts"] == MAX_REVISION_ATTEMPTS
    assert result["evaluation"].passed is False
    assert result["script_record"]["status"] == "needs_review"


def test_missing_hf_credentials_do_not_silently_select_a_mock(monkeypatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "huggingface")
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HF_MODEL", raising=False)

    try:
        build_script_generator()
    except HuggingFaceConfigurationError as error:
        assert "HF_TOKEN" in str(error)
        assert "not fall back" in str(error)
    else:
        raise AssertionError("Missing credentials must not select another model or mock.")


def test_test_fixture_requires_explicit_injection() -> None:
    provider = MockScriptGenerator()

    script = provider.generate(ScriptRequest(topic="focus"))

    assert provider.is_mock
    assert "test fixture" in provider.provider_name
    assert "not a live model response" in script.revision_notes[0]


def test_script_persistence_is_additive_to_existing_research(tmp_path) -> None:
    database_path = tmp_path / "shared.sqlite3"
    research_record = save_research("discipline", [{"topic": "sample"}], database_path)
    workflow = build_workflow(database_path=str(database_path), script_generator=MockScriptGenerator())
    result = workflow.invoke({"script_request": {"topic": "discipline"}})

    assert result["script_record"]["id"] > 0
    assert research_record["niche"] == "discipline"
    assert get_script_by_id(result["script_record"]["id"], database_path) is not None


def test_explicit_human_review_flag_never_auto_approves_script(tmp_path) -> None:
    workflow = build_workflow(
        database_path=str(tmp_path / "human-review.sqlite3"),
        script_generator=MockScriptGenerator(),
    )

    result = workflow.invoke(
        {"script_request": {"topic": "discipline"}, "require_human_review": True}
    )

    assert result["evaluation"].passed
    assert result["script_record"]["status"] == "needs_review"
    assert result["script_record"]["evaluation"]["passed"] is True