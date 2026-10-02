"""CLI diagnostics and explicit script approval tests."""

import pytest

from app import main as cli
from app.agents.quality_agent import evaluate_script
from app.agents.script_agent import MockScriptGenerator
from app.config import get_quality_threshold
from app.database.db import get_script_by_id, review_script, save_script
from app.graph.workflow import build_workflow
from app.schemas.script_schema import ProductionMetadata, ScriptRequest
from app.video.production_service import ProductionService


class _Workflow:
    def __init__(self, state):
        self.state = state

    def invoke(self, input_state):
        return self.state


def _saved_script(database_path, *, passing=True):
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    evaluation = evaluate_script(script)
    if not passing:
        evaluation = evaluation.model_copy(
            update={"language_consistency": False, "passed": False}
        )
    return save_script(script, evaluation=evaluation, database_path=database_path)


def test_produce_script_prints_workflow_error_when_result_is_missing(
    monkeypatch, capsys
):
    monkeypatch.setattr(
        cli, "build_workflow", lambda **kwargs: _Workflow(
            {"errors": ["video_production_node: Approved script does not have a passing quality evaluation."]}
        )
    )

    exit_code = cli.produce_script(5, "unused.sqlite3")

    output = capsys.readouterr().err
    assert exit_code == 1
    assert "Approved script does not have a passing quality evaluation" in output
    assert "Unable to start production" not in output


def test_failed_production_prints_actionable_tts_configuration(capsys):
    exit_code = cli.print_production(
        {
            "id": "production-id",
            "script_id": 5,
            "status": "failed",
            "output_dir": "/tmp/production",
            "error": "TTSError: MOTIVATIONOS_TTS_PROVIDER must explicitly select a TTS provider.",
        }
    )

    assert exit_code == 1
    assert "MOTIVATIONOS_TTS_PROVIDER=edge-tts" in capsys.readouterr().err


def test_script_quality_threshold_uses_min_script_quality_score(monkeypatch):
    monkeypatch.setenv("MIN_SCRIPT_QUALITY_SCORE", "5.0")
    monkeypatch.delenv("MOTIVATIONOS_QUALITY_THRESHOLD", raising=False)

    assert get_quality_threshold() == 5.0


def test_approve_script_cli_refuses_a_failed_evaluation(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "scripts.sqlite3"
    record = _saved_script(database_path, passing=False)
    monkeypatch.setattr(cli, "get_database_path", lambda: database_path)
    monkeypatch.setattr("sys.argv", ["app.main", "--approve-script", str(record["id"]), "--reviewer", "reviewer"])

    exit_code = cli.main()

    assert exit_code == 1
    assert "must pass quality evaluation" in capsys.readouterr().err
    assert get_script_by_id(record["id"], database_path)["status"] == "needs_review"


def test_approve_script_cli_records_review_audit_fields(tmp_path, monkeypatch, capsys):
    database_path = tmp_path / "scripts.sqlite3"
    record = _saved_script(database_path)
    monkeypatch.setattr(cli, "get_database_path", lambda: database_path)
    monkeypatch.setattr("sys.argv", ["app.main", "--approve-script", str(record["id"]), "--reviewer", "Parshav"])

    exit_code = cli.main()

    reviewed = get_script_by_id(record["id"], database_path)
    assert exit_code == 0
    assert reviewed["status"] == "approved"
    assert reviewed["reviewed_at"]
    assert reviewed["reviewed_by"] == "Parshav"
    assert "Status: approved" in capsys.readouterr().out


class _MetadataProvider:
    provider_name = "test metadata provider"
    model = "test-model"

    def generate_structured(self, schema, system_prompt, user_prompt, *, max_tokens):
        return ProductionMetadata(
            title="A small promise",
            description="An original story.",
            hashtags=["#Discipline"],
        )


class _FailingTTS:
    def synthesize(self, script, output_path):
        raise RuntimeError("intentional downstream TTS stop")


class _AvailableAssembler:
    def ensure_available(self):
        return None


def test_passing_approved_script_advances_past_approval_gate(tmp_path):
    database_path = tmp_path / "scripts.sqlite3"
    record = _saved_script(database_path)
    review_script(record["id"], "approved", "reviewer", database_path)
    service = ProductionService(
        database_path=database_path,
        output_root=tmp_path / "productions",
        llm_provider=_MetadataProvider(),
        tts_provider=_FailingTTS(),
        assembler=_AvailableAssembler(),
    )

    result = service.produce_script_record(record["id"])

    assert result["status"] == "failed"
    assert "intentional downstream TTS stop" in result["error"]


def test_debug_mode_reraises_video_workflow_exceptions(tmp_path):
    class ExplodingService:
        def produce_script_record(self, script_id):
            raise ValueError("diagnostic exception")

    workflow = build_workflow(
        database_path=str(tmp_path / "debug.sqlite3"),
        production_service=ExplodingService(),
    )

    with pytest.raises(ValueError, match="diagnostic exception"):
        workflow.invoke({"production_script_id": 5, "debug": True})