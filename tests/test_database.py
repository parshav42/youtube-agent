"""Tests for persistent SQLite research history."""

import pytest
import sqlite3
from pydantic import ValidationError

from app.agents.script_agent import MockScriptGenerator
from app.database.db import (
    get_latest_research,
    get_research_history,
    get_recent_scripts,
    get_script_by_id,
    initialize_database,
    save_research,
    save_script,
)
from app.schemas.script_schema import ScriptRequest


def test_database_persists_latest_record_and_history(tmp_path) -> None:
    database_path = tmp_path / "research.sqlite3"
    first = save_research("discipline", [{"topic": "Daily practice", "score": 8.2}], database_path)
    second = save_research("focus", [{"topic": "Protect attention", "score": 7.9}], database_path)

    assert get_latest_research(database_path) == second
    history = get_research_history(database_path)
    assert [record["id"] for record in history] == [second["id"], first["id"]]
    assert history[0]["results"] == [{"topic": "Protect attention", "score": 7.9}]


def test_database_history_limit_must_be_positive(tmp_path) -> None:
    with pytest.raises(ValueError, match="at least 1"):
        get_research_history(tmp_path / "research.sqlite3", limit=0)


def test_database_rejects_empty_niche(tmp_path) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        save_research("  ", [], tmp_path / "research.sqlite3")


def test_latest_record_is_none_before_any_research(tmp_path) -> None:
    assert get_latest_research(tmp_path / "empty.sqlite3") is None


def test_save_script_rejects_invalid_mapping_before_persisting(tmp_path) -> None:
    database_path = tmp_path / "invalid-script.sqlite3"
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    malformed_script = script.model_dump(mode="json")
    malformed_script["scenes"][0]["narration"] += " Extra words outside the canonical narration."

    with pytest.raises(ValidationError):
        save_script(malformed_script, status="completed", database_path=database_path)

    assert get_recent_scripts(database_path) == []


def test_script_evaluation_breakdown_is_saved_and_old_rows_migrate(tmp_path) -> None:
    database_path = tmp_path / "scripts.sqlite3"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """CREATE TABLE scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                language TEXT NOT NULL,
                script_json TEXT NOT NULL,
                quality_score REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            "INSERT INTO scripts VALUES (?, ?, ?, ?, ?, ?, ?)",
            (1, "old", "Hinglish", "{}", 3.75, "needs_review", "2026-01-01T00:00:00Z"),
        )

    initialize_database(database_path)
    old_record = get_script_by_id(1, database_path)
    assert old_record["evaluation"] is None
    assert old_record["reviewed_at"] is None
    assert old_record["reviewed_by"] is None
    with sqlite3.connect(database_path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(scripts)")}
    assert {"reviewed_at", "reviewed_by"} <= columns

    evaluation = {
        "quality_score": 3.75,
        "passed": False,
        "language_consistency": False,
        "duration_feasibility": False,
    }
    script = MockScriptGenerator().generate(ScriptRequest(topic="new"))
    new_record = save_script(
        script,
        status="needs_review",
        database_path=database_path,
        evaluation=evaluation,
    )

    assert new_record["evaluation"] == {
        **evaluation,
        "quality_score": 37.5,
    }