"""SQLite persistence for research runs."""

import json
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from pydantic import BaseModel, ValidationError

from app.config import get_database_path
from app.schemas.script_schema import (
    MotivationalScript,
    ScriptEvaluation,
    normalize_legacy_script_score,
)

ResearchResult = BaseModel | Mapping[str, Any]


class ScriptReviewError(ValueError):
    """Raised when a script cannot enter the requested human-review state."""


class ScriptNotFoundError(ScriptReviewError):
    """Raised when a review references a missing script."""


def _resolve_path(database_path: str | Path | None) -> Path:
    """Choose the configured database path or the supplied override."""
    if database_path is None:
        return get_database_path()
    path = Path(database_path).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


@contextmanager
def _connection(database_path: str | Path | None) -> Iterator[sqlite3.Connection]:
    """Yield a transaction-capable connection and always close it."""
    path = _resolve_path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database(database_path: str | Path | None = None) -> None:
    """Create application tables when they do not already exist."""
    with _connection(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS research_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                niche TEXT NOT NULL,
                created_at TEXT NOT NULL,
                results_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS productions (
                id TEXT PRIMARY KEY,
                script_id INTEGER NOT NULL,
                topic TEXT NOT NULL,
                status TEXT NOT NULL,
                output_dir TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS scripts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                language TEXT NOT NULL,
                script_json TEXT NOT NULL,
                quality_score REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                evaluation_json TEXT,
                reviewed_at TEXT,
                reviewed_by TEXT,
                rejection_reason TEXT,
                rejection_stage TEXT
            )
            """
        )
        script_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(scripts)").fetchall()
        }
        if "evaluation_json" not in script_columns:
            connection.execute("ALTER TABLE scripts ADD COLUMN evaluation_json TEXT")
        if "reviewed_at" not in script_columns:
            connection.execute("ALTER TABLE scripts ADD COLUMN reviewed_at TEXT")

        if "reviewed_by" not in script_columns:
            connection.execute("ALTER TABLE scripts ADD COLUMN reviewed_by TEXT")
        if "rejection_reason" not in script_columns:
            connection.execute("ALTER TABLE scripts ADD COLUMN rejection_reason TEXT")
        if "rejection_stage" not in script_columns:
            connection.execute("ALTER TABLE scripts ADD COLUMN rejection_stage TEXT")


def _serialize_result(result: ResearchResult) -> dict[str, Any]:
    """Convert Pydantic models or mappings into JSON-compatible dictionaries."""
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if isinstance(result, Mapping):
        return dict(result)
    raise TypeError("Research results must be Pydantic models or mappings.")


def _decode_record(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Decode a database row into the public record shape."""
    if row is None:
        return None
    return {
        "id": row["id"],
        "niche": row["niche"],
        "created_at": row["created_at"],
        "results": json.loads(row["results_json"]),
    }


def save_research(
    niche: str,
    results: Sequence[ResearchResult],
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    """Persist a research run and return its decoded record."""
    if not isinstance(niche, str) or not niche.strip():
        raise ValueError("The research niche must be a non-empty string.")
    initialize_database(database_path)
    results_json = json.dumps(
        [_serialize_result(result) for result in results], ensure_ascii=False
    )
    created_at = datetime.now(timezone.utc).isoformat()
    with _connection(database_path) as connection:
        cursor = connection.execute(
            "INSERT INTO research_runs (niche, created_at, results_json) VALUES (?, ?, ?)",
            (niche.strip(), created_at, results_json),
        )
        row = connection.execute(
            "SELECT id, niche, created_at, results_json FROM research_runs WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    record = _decode_record(row)
    if record is None:
        raise RuntimeError("The saved research record could not be read back.")
    return record


def get_latest_research(database_path: str | Path | None = None) -> dict[str, Any] | None:
    """Return the most recently saved research run, if one exists."""
    initialize_database(database_path)
    with _connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT id, niche, created_at, results_json
            FROM research_runs
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    return _decode_record(row)


def get_research_history(
    database_path: str | Path | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """Return up to ``limit`` saved runs, newest first."""
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    initialize_database(database_path)
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, niche, created_at, results_json
            FROM research_runs
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [record for row in rows if (record := _decode_record(row)) is not None]


def _decode_script(row: sqlite3.Row | None) -> dict[str, Any] | None:
    """Decode a script row into its public record shape."""
    if row is None:
        return None
    script_data = json.loads(row["script_json"])
    if isinstance(script_data, dict) and "quality_score" in script_data:
        script_data["quality_score"] = normalize_legacy_script_score(
            script_data["quality_score"]
        )
    evaluation_data = (
        json.loads(row["evaluation_json"]) if row["evaluation_json"] else None
    )
    if isinstance(evaluation_data, dict):
        for field in ("quality_score", "threshold"):
            if field in evaluation_data:
                evaluation_data[field] = normalize_legacy_script_score(
                    evaluation_data[field]
                )
    return {
        "id": row["id"],
        "topic": row["topic"],
        "language": row["language"],
        "script": script_data,
        "quality_score": normalize_legacy_script_score(row["quality_score"]),
        "evaluation": evaluation_data,
        "status": row["status"],
        "created_at": row["created_at"],
        "reviewed_at": row["reviewed_at"],
        "reviewed_by": row["reviewed_by"],
        "rejection_reason": row["rejection_reason"],
        "rejection_stage": row["rejection_stage"],
    }


def save_script(
    script: BaseModel | Mapping[str, Any],
    status: str = "needs_review",
    database_path: str | Path | None = None,
    evaluation: BaseModel | Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist a validated script and optional structured evaluation report."""
    data = _serialize_result(script)
    validated_script = MotivationalScript.model_validate(data)
    data["quality_score"] = validated_script.quality_score
    topic = data.get("topic")
    language = data.get("language")
    quality_score = data.get("quality_score")
    if not isinstance(topic, str) or not topic.strip():
        raise ValueError("The script topic must be a non-empty string.")
    if not isinstance(language, str) or not language.strip():
        raise ValueError("The script language must be a non-empty string.")
    if status != "needs_review":
        raise ValueError("New scripts must start in 'needs_review' status.")
    if not isinstance(quality_score, (int, float)):
        raise ValueError("The script quality score must be numeric.")
    initialize_database(database_path)
    created_at = datetime.now(timezone.utc).isoformat()
    script_json = json.dumps(data, ensure_ascii=False)
    evaluation_data = _serialize_result(evaluation) if evaluation is not None else None
    if isinstance(evaluation_data, dict):
        for field in ("quality_score", "threshold"):
            if field in evaluation_data:
                evaluation_data[field] = normalize_legacy_script_score(
                    evaluation_data[field]
                )
    evaluation_json = (
        json.dumps(evaluation_data, ensure_ascii=False)
        if evaluation_data is not None
        else None
    )
    with _connection(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO scripts
            (topic, language, script_json, quality_score, status, created_at, evaluation_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic.strip(), language.strip(), script_json, quality_score,
                status.strip(), created_at, evaluation_json,
            ),
        )
        row = connection.execute(
            """
            SELECT id, topic, language, script_json, quality_score, status, created_at, evaluation_json, reviewed_at, reviewed_by, rejection_reason, rejection_stage
            FROM scripts WHERE id = ?
            """,
            (cursor.lastrowid,),
        ).fetchone()
    record = _decode_script(row)
    if record is None:
        raise RuntimeError("The saved script record could not be read back.")
    return record


def get_script_by_id(
    script_id: int, database_path: str | Path | None = None
) -> dict[str, Any] | None:
    """Retrieve one saved script by its database ID."""
    initialize_database(database_path)
    with _connection(database_path) as connection:
        row = connection.execute(
            """
            SELECT id, topic, language, script_json, quality_score, status, created_at, evaluation_json, reviewed_at, reviewed_by, rejection_reason, rejection_stage
            FROM scripts WHERE id = ?
            """,
            (script_id,),
        ).fetchone()
    return _decode_script(row)


def get_recent_scripts(
    database_path: str | Path | None = None, limit: int = 10
) -> list[dict[str, Any]]:
    """Return recent scripts, newest first."""
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    initialize_database(database_path)
    with _connection(database_path) as connection:
        rows = connection.execute(
            """
            SELECT id, topic, language, script_json, quality_score, status, created_at, evaluation_json, reviewed_at, reviewed_by, rejection_reason, rejection_stage
            FROM scripts ORDER BY created_at DESC, id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [record for row in rows if (record := _decode_script(row)) is not None]


def get_scripts_needing_review(
    database_path: str | Path | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    """Return scripts awaiting an explicit human decision, newest first."""
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    initialize_database(database_path)
    with _connection(database_path) as connection:
        rows = connection.execute(
            """SELECT id, topic, language, script_json, quality_score, status,
                created_at, evaluation_json, reviewed_at, reviewed_by, rejection_reason, rejection_stage
            FROM scripts
            WHERE status = 'needs_review'
                OR (status = 'completed' AND reviewed_at IS NULL AND reviewed_by IS NULL)
            ORDER BY created_at DESC, id DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [record for row in rows if (record := _decode_script(row)) is not None]


def review_script(
    script_id: int,
    decision: str,
    reviewer: str = "local_admin",
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    """Record one explicit human approval or rejection of a pending script."""
    if not isinstance(decision, str) or decision not in {"approved", "rejected"}:
        raise ScriptReviewError("Decision must be 'approved' or 'rejected'.")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ScriptReviewError("Reviewer must not be empty.")
    initialize_database(database_path)
    reviewed_at = datetime.now(timezone.utc).isoformat()
    with _connection(database_path) as connection:
        row = connection.execute("SELECT * FROM scripts WHERE id = ?", (script_id,)).fetchone()
        if row is None:
            raise ScriptNotFoundError(f"Script not found: {script_id}")
        current_status = row["status"]
        is_legacy_completed = (
            current_status == "completed"
            and row["reviewed_at"] is None
            and row["reviewed_by"] is None
        )
        if current_status != "needs_review" and not is_legacy_completed:
            raise ScriptReviewError(
                "Only scripts awaiting human review or unreviewed legacy scripts can be reviewed."
            )
        if decision == "approved":
            try:
                evaluation = ScriptEvaluation.model_validate(
                    json.loads(row["evaluation_json"])
                ) if row["evaluation_json"] else None
            except (json.JSONDecodeError, ValidationError) as error:
                raise ScriptReviewError(
                    "A valid passing quality evaluation is required before approval."
                ) from error
            if evaluation is None or not evaluation.passed or not all(
                value
                for name, value in evaluation.model_dump().items()
                if name not in {"quality_score", "threshold", "passed", "feedback"}
            ):
                raise ScriptReviewError(
                    "A script must pass quality evaluation before approval."
                )
        if is_legacy_completed:
            cursor = connection.execute(
                """
                UPDATE scripts
                SET status = ?, reviewed_at = ?, reviewed_by = ?
                WHERE id = ? AND status = 'completed'
                    AND reviewed_at IS NULL AND reviewed_by IS NULL
                """,
                (decision, reviewed_at, reviewer.strip(), script_id),
            )
        else:
            cursor = connection.execute(
                """
                UPDATE scripts
                SET status = ?, reviewed_at = ?, reviewed_by = ?
                WHERE id = ? AND status = 'needs_review'
                """,
                (decision, reviewed_at, reviewer.strip(), script_id),
            )
        if cursor.rowcount != 1:
            raise ScriptReviewError("Script review was already recorded by another request.")
        updated_row = connection.execute(
            "SELECT * FROM scripts WHERE id = ?", (script_id,)
        ).fetchone()
    record = _decode_script(updated_row)
    if record is None:
        raise RuntimeError("The reviewed script could not be read back.")
    return record


def delete_unreviewed_script(script_id: int, database_path: str | Path | None = None) -> None:
    """Remove a failed pipeline draft only while it is still unreviewed."""
    initialize_database(database_path)
    with _connection(database_path) as connection:
        cursor = connection.execute(
            "DELETE FROM scripts WHERE id = ? AND status = 'needs_review' AND reviewed_at IS NULL AND reviewed_by IS NULL",
            (script_id,),
        )
        if cursor.rowcount != 1:
            raise ScriptReviewError("Only an unreviewed draft can be removed after exhausted pipeline retries.")


def reject_script(script_id: int, reason: str, stage: str = "quality_gate",
                  database_path: str | Path | None = None) -> dict[str, Any]:
    """Preserve a failed draft and record the pipeline rejection diagnostic."""
    initialize_database(database_path)
    with _connection(database_path) as connection:
        cursor = connection.execute(
            "UPDATE scripts SET status = 'rejected', rejection_reason = ?, rejection_stage = ? "
            "WHERE id = ? AND status = 'needs_review' AND reviewed_at IS NULL AND reviewed_by IS NULL",
            (reason, stage, script_id),
        )
        if cursor.rowcount != 1:
            raise ScriptReviewError(f"Unable to mark unreviewed script {script_id} rejected.")
    record = get_script_by_id(script_id, database_path)
    if record is None:
        raise RuntimeError("The rejected script could not be read back.")
    return record


def get_rejected_scripts(database_path: str | Path | None = None, limit: int = 10) -> list[dict[str, Any]]:
    """Return rejected scripts newest first."""
    if limit < 1:
        raise ValueError("limit must be at least 1.")
    initialize_database(database_path)
    with _connection(database_path) as connection:
        rows = connection.execute(
            "SELECT id, topic, language, script_json, quality_score, status, created_at, "
            "evaluation_json, reviewed_at, reviewed_by, rejection_reason, rejection_stage "
            "FROM scripts WHERE status = 'rejected' ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [record for row in rows if (record := _decode_script(row)) is not None]



def _decode_production(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {
        "id": row["id"],
        "script_id": row["script_id"],
        "topic": row["topic"],
        "status": row["status"],
        "output_dir": row["output_dir"],
        "metadata": json.loads(row["metadata_json"]),
        "error": row["error"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def create_production(
    production_id: str,
    script_id: int,
    topic: str,
    output_dir: str | Path,
    metadata: Mapping[str, Any] | None = None,
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create a queued production record without changing existing script rows."""
    if not production_id.strip() or not topic.strip():
        raise ValueError("Production ID and topic must not be empty.")
    initialize_database(database_path)
    now = datetime.now(timezone.utc).isoformat()
    with _connection(database_path) as connection:
        connection.execute(
            """INSERT INTO productions
            (id, script_id, topic, status, output_dir, metadata_json, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
            (
                production_id,
                script_id,
                topic.strip(),
                "queued",
                str(Path(output_dir).resolve()),
                json.dumps(dict(metadata or {}), ensure_ascii=False),
                now,
                now,
            ),
        )
    record = get_production_by_id(production_id, database_path)
    if record is None:
        raise RuntimeError("The production record could not be read after creation.")
    return record


def update_production(
    production_id: str,
    status: str,
    metadata: Mapping[str, Any],
    error: str | None = None,
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    """Update a production status and complete diagnostic/intermediate metadata."""
    if not status.strip():
        raise ValueError("Production status must not be empty.")
    now = datetime.now(timezone.utc).isoformat()
    with _connection(database_path) as connection:
        cursor = connection.execute(
            """UPDATE productions SET status = ?, metadata_json = ?, error = ?, updated_at = ?
            WHERE id = ?""",
            (
                status.strip(),
                json.dumps(dict(metadata), ensure_ascii=False),
                error,
                now,
                production_id,
            ),
        )
        if cursor.rowcount != 1:
            raise ValueError(f"Production record not found: {production_id}")
    record = get_production_by_id(production_id, database_path)
    if record is None:
        raise RuntimeError("The production record could not be read after update.")
    return record


def get_production_by_id(
    production_id: str, database_path: str | Path | None = None
) -> dict[str, Any] | None:
    """Retrieve one production and decode its persisted metadata."""
    initialize_database(database_path)
    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM productions WHERE id = ?", (production_id,)
        ).fetchone()
    return _decode_production(row)


def get_productions(database_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Retrieve production records newest first."""
    initialize_database(database_path)
    with _connection(database_path) as connection:
        rows = connection.execute(
            "SELECT * FROM productions ORDER BY created_at DESC, id DESC"
        ).fetchall()
    return [record for row in rows if (record := _decode_production(row)) is not None]
