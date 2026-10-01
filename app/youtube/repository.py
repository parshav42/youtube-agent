"""Additive SQLite persistence for YouTube operations."""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.database.db import _connection, initialize_database


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def initialize_youtube_database(database_path: str | Path | None = None) -> None:
    """Create YouTube tables without altering existing application records."""
    initialize_database(database_path)
    with _connection(database_path) as connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS youtube_oauth_states (
                state_hash TEXT PRIMARY KEY,
                encrypted_verifier TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS youtube_credentials (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                encrypted_token TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS publishing_approvals (
                production_id TEXT PRIMARY KEY REFERENCES productions(id),
                state TEXT NOT NULL CHECK (state IN (
                    'awaiting_review', 'approved', 'uploading',
                    'uploaded_private', 'uploaded', 'failed', 'cancelled'
                )),
                visibility TEXT NOT NULL CHECK (visibility IN ('private', 'unlisted', 'public')),
                approved_at TEXT,
                approved_channel_id TEXT,
                authenticated_channel_id_at_approval TEXT,
                metadata_json TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        approval_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(publishing_approvals)")
        }
        if "approved_channel_id" not in approval_columns:
            connection.execute(
                "ALTER TABLE publishing_approvals ADD COLUMN approved_channel_id TEXT"
            )
        if "authenticated_channel_id_at_approval" not in approval_columns:
            connection.execute(
                "ALTER TABLE publishing_approvals ADD COLUMN authenticated_channel_id_at_approval TEXT"
            )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS youtube_channel_identity_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                production_id TEXT NOT NULL,
                phase TEXT NOT NULL CHECK (phase IN ('approval', 'upload')),
                outcome TEXT NOT NULL CHECK (outcome IN ('verified', 'rejected')),
                reason TEXT NOT NULL,
                approved_channel_id TEXT,
                authenticated_channel_id TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS youtube_uploads (
                production_id TEXT PRIMARY KEY REFERENCES productions(id),
                video_id TEXT UNIQUE,
                status TEXT NOT NULL CHECK (status IN (
                    'awaiting_review', 'approved', 'uploading',
                    'uploaded_private', 'uploaded', 'failed', 'cancelled'
                )),
                visibility TEXT NOT NULL CHECK (visibility IN ('private', 'unlisted', 'public')),
                resumable_uri TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                lease_until TEXT,
                api_response_json TEXT,
                error TEXT,
                uploaded_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS video_analytics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL,
                metric_date TEXT NOT NULL,
                metric_name TEXT NOT NULL,
                metric_value REAL NOT NULL,
                retrieved_at TEXT NOT NULL,
                source TEXT NOT NULL,
                UNIQUE (video_id, metric_date, metric_name, source)
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS report_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date TEXT NOT NULL UNIQUE,
                report_path TEXT NOT NULL,
                report_json TEXT NOT NULL,
                email_status TEXT NOT NULL,
                emailed_at TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        connection.execute(
            """CREATE TABLE IF NOT EXISTS job_executions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_name TEXT NOT NULL,
                run_date TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                started_at TEXT,
                finished_at TEXT,
                error TEXT,
                UNIQUE (job_name, run_date)
            )"""
        )


def create_oauth_state(
    state_hash: str,
    encrypted_verifier: str,
    expires_at: str,
    database_path: str | Path | None = None,
) -> None:
    initialize_youtube_database(database_path)
    with _connection(database_path) as connection:
        connection.execute(
            "DELETE FROM youtube_oauth_states WHERE expires_at <= ?", (_now(),)
        )
        connection.execute(
            """INSERT INTO youtube_oauth_states
            (state_hash, encrypted_verifier, created_at, expires_at) VALUES (?, ?, ?, ?)""",
            (state_hash, encrypted_verifier, _now(), expires_at),
        )


def consume_oauth_state(
    state_hash: str,
    database_path: str | Path | None = None,
) -> str | None:
    """Consume one unexpired state value, making callback replay impossible."""
    initialize_youtube_database(database_path)
    now = _now()
    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT encrypted_verifier, expires_at FROM youtube_oauth_states WHERE state_hash = ?",
            (state_hash,),
        ).fetchone()
        connection.execute("DELETE FROM youtube_oauth_states WHERE expires_at <= ?", (now,))
        if row is None or row["expires_at"] <= now:
            return None
        connection.execute(
            "DELETE FROM youtube_oauth_states WHERE state_hash = ?", (state_hash,)
        )
    return row["encrypted_verifier"]


def save_encrypted_credentials(
    encrypted_token: str,
    database_path: str | Path | None = None,
) -> None:
    initialize_youtube_database(database_path)
    with _connection(database_path) as connection:
        connection.execute(
            """INSERT INTO youtube_credentials (id, encrypted_token, status, error, updated_at)
            VALUES (1, ?, 'connected', NULL, ?)
            ON CONFLICT(id) DO UPDATE SET encrypted_token = excluded.encrypted_token,
                status = 'connected', error = NULL, updated_at = excluded.updated_at""",
            (encrypted_token, _now()),
        )


def get_encrypted_credentials(database_path: str | Path | None = None) -> dict[str, Any] | None:
    initialize_youtube_database(database_path)
    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT encrypted_token, status, error, updated_at FROM youtube_credentials WHERE id = 1"
        ).fetchone()
    return dict(row) if row else None


def set_credential_status(
    status: str,
    error: str | None = None,
    database_path: str | Path | None = None,
) -> None:
    initialize_youtube_database(database_path)
    with _connection(database_path) as connection:
        connection.execute(
            "UPDATE youtube_credentials SET status = ?, error = ?, updated_at = ? WHERE id = 1",
            (status, error, _now()),
        )


def get_approval(
    production_id: str,
    database_path: str | Path | None = None,
) -> dict[str, Any] | None:
    initialize_youtube_database(database_path)
    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM publishing_approvals WHERE production_id = ?", (production_id,)
        ).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["metadata"] = json.loads(record.pop("metadata_json"))
    return record


def approve_production(
    production_id: str,
    visibility: str,
    metadata: dict[str, Any],
    database_path: str | Path | None = None,
    *,
    approved_channel_id: str | None = None,
    authenticated_channel_id_at_approval: str | None = None,
) -> dict[str, Any]:
    if visibility not in {"private", "unlisted", "public"}:
        raise ValueError("Visibility must be private, unlisted, or public.")
    initialize_youtube_database(database_path)
    now = _now()
    metadata_json = json.dumps(metadata, ensure_ascii=False)
    with _connection(database_path) as connection:
        production = connection.execute(
            "SELECT status FROM productions WHERE id = ?", (production_id,)
        ).fetchone()
        if production is None:
            raise ValueError(f"Production {production_id} was not found.")
        if production["status"] != "completed":
            raise ValueError("Only completed video productions can be approved for publishing.")
        existing = connection.execute(
            "SELECT state FROM publishing_approvals WHERE production_id = ?", (production_id,)
        ).fetchone()
        if existing and existing["state"] != "awaiting_review":
            raise ValueError(f"Production cannot be approved from state {existing['state']!r}.")
        connection.execute(
            """INSERT INTO publishing_approvals
            (production_id, state, visibility, approved_at, approved_channel_id,
                authenticated_channel_id_at_approval, metadata_json, error, created_at, updated_at)
            VALUES (?, 'approved', ?, ?, ?, ?, ?, NULL, ?, ?)
            ON CONFLICT(production_id) DO UPDATE SET state = 'approved', visibility = excluded.visibility,
                approved_at = excluded.approved_at, metadata_json = excluded.metadata_json,
                approved_channel_id = excluded.approved_channel_id,
                authenticated_channel_id_at_approval = excluded.authenticated_channel_id_at_approval,
                error = NULL, updated_at = excluded.updated_at""",
            (
                production_id,
                visibility,
                now,
                approved_channel_id,
                authenticated_channel_id_at_approval,
                metadata_json,
                now,
                now,
            ),
        )
        connection.execute(
            """INSERT INTO youtube_uploads
            (production_id, status, visibility, attempt_count, created_at, updated_at)
            VALUES (?, 'approved', ?, 0, ?, ?)
            ON CONFLICT(production_id) DO UPDATE SET status = 'approved', visibility = excluded.visibility,
                error = NULL, updated_at = excluded.updated_at""",
            (production_id, visibility, now, now),
        )
    approval = get_approval(production_id, database_path)
    if approval is None:
        raise RuntimeError("Publishing approval could not be read after saving.")
    return approval


def record_channel_identity_audit(
    production_id: str,
    *,
    phase: str,
    outcome: str,
    reason: str,
    approved_channel_id: str | None = None,
    authenticated_channel_id: str | None = None,
    database_path: str | Path | None = None,
) -> None:
    """Record channel verification using IDs and fixed, non-sensitive reason codes only."""
    if phase not in {"approval", "upload"} or outcome not in {"verified", "rejected"}:
        raise ValueError("Invalid channel identity audit event.")
    initialize_youtube_database(database_path)
    with _connection(database_path) as connection:
        connection.execute(
            """INSERT INTO youtube_channel_identity_audit
            (production_id, phase, outcome, reason, approved_channel_id,
                authenticated_channel_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                production_id,
                phase,
                outcome,
                reason,
                approved_channel_id,
                authenticated_channel_id,
                _now(),
            ),
        )


def get_channel_identity_audit(
    production_id: str,
    database_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    initialize_youtube_database(database_path)
    with _connection(database_path) as connection:
        rows = connection.execute(
            """SELECT phase, outcome, reason, approved_channel_id,
                authenticated_channel_id, created_at
            FROM youtube_channel_identity_audit WHERE production_id = ? ORDER BY id""",
            (production_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_upload(
    production_id: str,
    database_path: str | Path | None = None,
) -> dict[str, Any] | None:
    initialize_youtube_database(database_path)
    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM youtube_uploads WHERE production_id = ?", (production_id,)
        ).fetchone()
    if row is None:
        return None
    record = dict(row)
    if record["api_response_json"]:
        record["api_response"] = json.loads(record.pop("api_response_json"))
    else:
        record.pop("api_response_json")
        record["api_response"] = None
    return record


def cancel_publishing(
    production_id: str,
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    initialize_youtube_database(database_path)
    now = _now()
    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT state FROM publishing_approvals WHERE production_id = ?", (production_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Publishing review was not found.")
        if row["state"] not in {"awaiting_review", "approved", "failed"}:
            raise ValueError(f"Publishing cannot be cancelled from state {row['state']!r}.")
        connection.execute(
            """UPDATE publishing_approvals SET state = 'cancelled', error = NULL, updated_at = ?
            WHERE production_id = ?""",
            (now, production_id),
        )
        connection.execute(
            """UPDATE youtube_uploads SET status = 'cancelled', lease_until = NULL,
                error = NULL, updated_at = ? WHERE production_id = ?""",
            (now, production_id),
        )
    result = get_approval(production_id, database_path)
    if result is None:
        raise RuntimeError("Cancelled publishing record could not be read.")
    return result


def request_review(
    production_id: str,
    visibility: str,
    metadata: dict[str, Any],
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    if visibility not in {"private", "unlisted", "public"}:
        raise ValueError("Visibility must be private, unlisted, or public.")
    initialize_youtube_database(database_path)
    now = _now()
    with _connection(database_path) as connection:
        production = connection.execute(
            "SELECT status FROM productions WHERE id = ?", (production_id,)
        ).fetchone()
        if production is None:
            raise ValueError(f"Production {production_id} was not found.")
        if production["status"] != "completed":
            raise ValueError("Only completed video productions can enter publishing review.")
        existing = connection.execute(
            "SELECT state FROM publishing_approvals WHERE production_id = ?", (production_id,)
        ).fetchone()
        if existing and existing["state"] != "awaiting_review":
            raise ValueError(f"Production already has publishing state {existing['state']!r}.")
        connection.execute(
            """INSERT INTO publishing_approvals
            (production_id, state, visibility, approved_at, metadata_json, error, created_at, updated_at)
            VALUES (?, 'awaiting_review', ?, NULL, ?, NULL, ?, ?)
            ON CONFLICT(production_id) DO NOTHING""",
            (production_id, visibility, json.dumps(metadata, ensure_ascii=False), now, now),
        )
        connection.execute(
            """INSERT INTO youtube_uploads
            (production_id, status, visibility, attempt_count, created_at, updated_at)
            VALUES (?, 'awaiting_review', ?, 0, ?, ?)
            ON CONFLICT(production_id) DO NOTHING""",
            (production_id, visibility, now, now),
        )
    approval = get_approval(production_id, database_path)
    if approval is None:
        raise RuntimeError("Publishing review could not be read after saving.")
    return approval


def claim_upload(
    production_id: str,
    database_path: str | Path | None = None,
    lease_seconds: int = 600,
) -> dict[str, Any]:
    """Atomically claim a first upload or resume an existing resumable session."""
    initialize_youtube_database(database_path)
    now = datetime.now(timezone.utc)
    now_text = now.isoformat()
    lease_until = (now.timestamp() + lease_seconds)
    lease_text = datetime.fromtimestamp(lease_until, timezone.utc).isoformat()
    with _connection(database_path) as connection:
        approval = connection.execute(
            "SELECT state FROM publishing_approvals WHERE production_id = ?", (production_id,)
        ).fetchone()
        row = connection.execute(
            "SELECT * FROM youtube_uploads WHERE production_id = ?", (production_id,)
        ).fetchone()
        if row is None:
            raise ValueError("Publishing review has not been created for this production.")
        retryable = row["status"] == "failed" and bool(row["resumable_uri"])
        stale_upload = (
            row["status"] == "uploading"
            and row["lease_until"] is not None
            and row["lease_until"] <= now_text
            and bool(row["resumable_uri"])
        )
        if row["status"] != "approved" and not retryable and not stale_upload:
            if row["status"] == "awaiting_review":
                raise ValueError("A valid human publishing approval is required before upload.")
            if row["status"] == "failed":
                raise ValueError(
                    "Upload failed without a resumable session; automatic re-upload is blocked to prevent duplicates."
                )
            raise ValueError(f"Production cannot upload from state {row['status']!r}.")
        approval_authorized = approval is not None and (
            approval["state"] == "approved"
            or (retryable and approval["state"] == "failed")
            or (stale_upload and approval["state"] == "uploading")
        )
        if not approval_authorized:
            raise ValueError("A valid human publishing approval is required before upload.")
        cursor = connection.execute(
            """UPDATE youtube_uploads SET status = 'uploading', attempt_count = attempt_count + 1,
                lease_until = ?, error = NULL, updated_at = ?
            WHERE production_id = ? AND status = ?""",
            (lease_text, now_text, production_id, row["status"]),
        )
        if cursor.rowcount != 1:
            raise ValueError("Another upload attempt is already in progress.")
        connection.execute(
            """UPDATE publishing_approvals SET state = 'uploading', error = NULL, updated_at = ?
            WHERE production_id = ? AND state IN ('approved', 'failed', 'uploading')""",
            (now_text, production_id),
        )
    claimed = get_upload(production_id, database_path)
    if claimed is None:
        raise RuntimeError("The upload claim could not be read after saving.")
    return claimed


def update_upload_progress(
    production_id: str,
    *,
    status: str,
    resumable_uri: str | None = None,
    video_id: str | None = None,
    api_response: dict[str, Any] | None = None,
    error: str | None = None,
    visibility: str | None = None,
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    if status not in {"awaiting_review", "approved", "uploading", "uploaded_private", "uploaded", "failed", "cancelled"}:
        raise ValueError("Invalid YouTube upload status.")
    now = _now()
    uploaded_at = now if status in {"uploaded_private", "uploaded"} else None
    encoded_response = json.dumps(api_response, ensure_ascii=False) if api_response is not None else None
    with _connection(database_path) as connection:
        if resumable_uri is None:
            connection.execute(
                """UPDATE youtube_uploads SET status = ?, video_id = COALESCE(?, video_id),
                    api_response_json = COALESCE(?, api_response_json), error = ?, lease_until = NULL,
                    uploaded_at = COALESCE(?, uploaded_at), updated_at = ?
                WHERE production_id = ?""",
                (status, video_id, encoded_response, error, uploaded_at, now, production_id),
            )
        else:
            lease_text = datetime.fromtimestamp(
                datetime.now(timezone.utc).timestamp() + 600, timezone.utc
            ).isoformat()
            connection.execute(
                """UPDATE youtube_uploads SET status = ?, resumable_uri = ?,
                    video_id = COALESCE(?, video_id), api_response_json = COALESCE(?, api_response_json),
                    error = ?, lease_until = ?, uploaded_at = COALESCE(?, uploaded_at), updated_at = ?
                WHERE production_id = ?""",
                (status, resumable_uri, video_id, encoded_response, error, lease_text, uploaded_at, now, production_id),
            )
        connection.execute(
            """UPDATE publishing_approvals SET state = ?, visibility = COALESCE(?, visibility),
                error = ?, updated_at = ? WHERE production_id = ?""",
            (status, visibility, error, now, production_id),
        )
    result = get_upload(production_id, database_path)
    if result is None:
        raise ValueError(f"Upload record not found for production {production_id}.")
    return result


def get_productions_for_report(
    start_at: str,
    end_at: str,
    database_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    initialize_youtube_database(database_path)
    with _connection(database_path) as connection:
        rows = connection.execute(
            """SELECT p.id, p.script_id, p.topic, p.status AS production_status,
                p.output_dir, p.metadata_json, p.error AS production_error, p.created_at,
                u.status AS upload_status, u.video_id, u.visibility, u.error AS upload_error
            FROM productions p LEFT JOIN youtube_uploads u ON u.production_id = p.id
            WHERE p.created_at >= ? AND p.created_at < ? ORDER BY p.created_at""",
            (start_at, end_at),
        ).fetchall()
    records = []
    for row in rows:
        item = dict(row)
        item["metadata"] = json.loads(item.pop("metadata_json"))
        records.append(item)
    return records


def save_analytics(
    rows: list[dict[str, Any]],
    database_path: str | Path | None = None,
) -> None:
    initialize_youtube_database(database_path)
    retrieved_at = _now()
    with _connection(database_path) as connection:
        connection.executemany(
            """INSERT INTO video_analytics
            (video_id, metric_date, metric_name, metric_value, retrieved_at, source)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id, metric_date, metric_name, source) DO UPDATE SET
                metric_value = excluded.metric_value, retrieved_at = excluded.retrieved_at""",
            [
                (row["video_id"], row["metric_date"], row["metric_name"], row["metric_value"], retrieved_at, row["source"])
                for row in rows
            ],
        )


def get_analytics(
    *,
    video_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    database_path: str | Path | None = None,
) -> list[dict[str, Any]]:
    initialize_youtube_database(database_path)
    clauses = []
    parameters: list[Any] = []
    if video_id:
        clauses.append("video_id = ?")
        parameters.append(video_id)
    if start_date:
        clauses.append("metric_date >= ?")
        parameters.append(start_date)
    if end_date:
        clauses.append("metric_date <= ?")
        parameters.append(end_date)
    query = "SELECT video_id, metric_date, metric_name, metric_value, retrieved_at, source FROM video_analytics"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY metric_date, video_id, metric_name"
    with _connection(database_path) as connection:
        rows = connection.execute(query, parameters).fetchall()
    return [dict(row) for row in rows]


def get_report_history(
    report_date: str,
    database_path: str | Path | None = None,
) -> dict[str, Any] | None:
    initialize_youtube_database(database_path)
    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM report_history WHERE report_date = ?", (report_date,)
        ).fetchone()
    if row is None:
        return None
    record = dict(row)
    record["report"] = json.loads(record.pop("report_json"))
    return record


def save_report(
    report_date: str,
    report_path: str,
    report: dict[str, Any],
    email_status: str,
    error: str | None = None,
    database_path: str | Path | None = None,
) -> dict[str, Any]:
    initialize_youtube_database(database_path)
    now = _now()
    emailed_at = now if email_status == "sent" else None
    with _connection(database_path) as connection:
        connection.execute(
            """INSERT INTO report_history
            (report_date, report_path, report_json, email_status, emailed_at, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(report_date) DO UPDATE SET report_path = excluded.report_path,
                report_json = excluded.report_json,
                email_status = CASE WHEN report_history.email_status = 'sent'
                    THEN 'sent' ELSE excluded.email_status END,
                emailed_at = COALESCE(report_history.emailed_at, excluded.emailed_at),
                error = excluded.error, updated_at = excluded.updated_at""",
            (report_date, report_path, json.dumps(report, ensure_ascii=False), email_status, emailed_at, error, now, now),
        )
    result = get_report_history(report_date, database_path)
    if result is None:
        raise RuntimeError("Daily report could not be read after saving.")
    return result


def claim_job(
    job_name: str,
    run_date: str,
    database_path: str | Path | None = None,
    max_attempts: int = 3,
) -> bool:
    initialize_youtube_database(database_path)
    now = datetime.now(timezone.utc)
    now_text = now.isoformat()
    stale_before = datetime.fromtimestamp(now.timestamp() - 900, timezone.utc).isoformat()
    with _connection(database_path) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO job_executions (job_name, run_date, status) VALUES (?, ?, 'queued')",
            (job_name, run_date),
        )
        cursor = connection.execute(
            """UPDATE job_executions SET status = 'running', attempts = attempts + 1,
                started_at = ?, finished_at = NULL, error = NULL
            WHERE job_name = ? AND run_date = ? AND attempts < ?
                AND (status IN ('queued', 'failed') OR (status = 'running' AND started_at < ?))""",
            (now_text, job_name, run_date, max_attempts, stale_before),
        )
    return cursor.rowcount == 1


def finish_job(
    job_name: str,
    run_date: str,
    *,
    status: str,
    error: str | None = None,
    database_path: str | Path | None = None,
) -> None:
    if status not in {"completed", "failed"}:
        raise ValueError("Job status must be completed or failed.")
    with _connection(database_path) as connection:
        connection.execute(
            """UPDATE job_executions SET status = ?, finished_at = ?, error = ?
            WHERE job_name = ? AND run_date = ?""",
            (status, _now(), error, job_name, run_date),
        )


def get_job_execution(
    job_name: str,
    run_date: str,
    database_path: str | Path | None = None,
) -> dict[str, Any] | None:
    initialize_youtube_database(database_path)
    with _connection(database_path) as connection:
        row = connection.execute(
            "SELECT * FROM job_executions WHERE job_name = ? AND run_date = ?",
            (job_name, run_date),
        ).fetchone()
    return dict(row) if row else None
