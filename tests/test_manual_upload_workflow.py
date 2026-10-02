"""Manual YouTube upload workflow coverage."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.agents.quality_agent import evaluate_script
from app.agents.script_agent import MockScriptGenerator
from app.api import create_app
from app.database.db import create_production, get_production_by_id, review_script, save_script, update_production
from app.main import main
from app.reporting.daily_report import DailyReportService
from app.schemas.script_schema import ProductionMetadata, ScriptRequest
from app.video import production_service as production_module
from app.video.production_service import ProductionService
from app.youtube.analytics import YouTubeAnalyticsService
from app.youtube.auto_match import YouTubeAutoMatcher
from app.youtube.manual_upload import export_upload_package
from app.youtube.repository import get_analytics, initialize_youtube_database, link_manual_upload

VIDEO_ID = "AbCdEfGhIjK"


def _approved_script(database: Path) -> int:
    script = MockScriptGenerator().generate(ScriptRequest(topic="discipline"))
    evaluation = evaluate_script(script)
    saved = save_script(script, evaluation=evaluation, database_path=database)
    return review_script(saved["id"], "approved", "workflow-test", database)["id"]


def _ready_production(database: Path, output: Path, production_id: str = "manual-production") -> str:
    metadata = {
        "youtube_metadata": {
            "title": "A Small Step Changes Everything",
            "description": "A short story about consistent effort.",
            "tags": ["motivation", "discipline"],
        }
    }
    create_production(production_id, 1, "discipline", output, metadata, database)
    update_production(production_id, "ready_for_manual_upload", metadata, database_path=database)
    output.mkdir(parents=True, exist_ok=True)
    (output / "final.mp4").write_bytes(b"video-fixture")
    return production_id


@dataclass
class _AudioQA:
    technical_status: str = "passed"
    findings: tuple = ()


def test_successful_production_enters_ready_for_manual_upload(tmp_path, monkeypatch):
    database = tmp_path / "production.sqlite3"
    script_id = _approved_script(database)
    monkeypatch.setenv("IMAGE_PROVIDER", "manual")
    monkeypatch.setattr(
        production_module,
        "generate_metadata",
        lambda script, llm: ProductionMetadata(
            title="A Small Step Changes Everything",
            description="A short story about consistent effort.",
            hashtags=["#motivation"],
        ),
    )
    monkeypatch.setattr(production_module, "run_audio_quality_assurance", lambda *args, **kwargs: _AudioQA())
    monkeypatch.setattr(
        production_module,
        "generate_srt",
        lambda *args, **kwargs: SimpleNamespace(
            path=str(tmp_path / "captions.srt"), timing_method="fixture", phrase_count=1,
            safe_margin_note="fixture",
        ),
    )

    class Assembler:
        def ensure_available(self):
            pass

        def assemble(self, scenes, visuals, audio, subtitles, output_path):
            Path(output_path).write_bytes(b"final-video")
            return {"path": str(output_path), "width": 1080, "height": 1920}

    class TTS:
        def synthesize(self, script, output_path):
            return SimpleNamespace(
                path=str(tmp_path / "narration.mp3"), provider="fixture", voice="fixture",
                duration_seconds=35.0, codec="mp3", word_timings=None, alignment_source=None,
            )

    monkeypatch.setattr(production_module, "run_video_quality_assurance", lambda path: {
        "technical_status": "passed", "findings": [],
    })
    service = ProductionService(
        database, tmp_path / "productions", llm_provider=SimpleNamespace(
            provider_name="fixture", model="fixture"
        ), tts_provider=TTS(), asset_manager=SimpleNamespace(acquire=lambda *args: []),
        assembler=Assembler(),
    )
    result = service.produce_script_record(script_id)

    assert result["status"] == "ready_for_manual_upload", result.get("error")
    assert (Path(result["output_dir"]) / "final.mp4").is_file()
    assert json.loads((Path(result["output_dir"]) / "metadata.json").read_text())["status"] == "ready_for_manual_upload"


def test_export_upload_package_writes_required_files_and_cli_prints_path(tmp_path, monkeypatch, capsys):
    database = tmp_path / "package.sqlite3"
    output = tmp_path / "productions" / "package-production"
    production_id = _ready_production(database, output, "package-production")
    monkeypatch.setenv("MOTIVATIONOS_DB_PATH", str(database))
    monkeypatch.setattr("sys.argv", ["app.main", "--export-upload-package", production_id])

    assert main() == 0
    package = output / "upload_package"
    assert {"final.mp4", "title.txt", "description.txt", "tags.txt", "README.txt"}.issubset(
        {path.name for path in package.iterdir()}
    )
    assert (package / "final.mp4").read_bytes() == b"video-fixture"
    assert "A Small Step" in (package / "title.txt").read_text()
    assert "motivation, discipline" in (package / "tags.txt").read_text()
    assert str(package) in capsys.readouterr().out


def test_dashboard_url_links_production_as_public_manual_upload(tmp_path, monkeypatch):
    database = tmp_path / "link.sqlite3"
    _ready_production(database, tmp_path / "production")
    monkeypatch.setenv("MOTIVATIONOS_ADMIN_TOKEN", "manual-upload-test")
    client = TestClient(create_app(database))

    response = client.post(
        "/api/productions/manual-production/youtube-link",
        headers={"Authorization": "Bearer manual-upload-test"},
        json={"url": f"https://www.youtube.com/watch?v={VIDEO_ID}"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "published"
    assert response.json()["video_id"] == VIDEO_ID
    stored = get_production_by_id("manual-production", database)
    assert stored["status"] == "published"
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT video_id, status, visibility FROM youtube_uploads WHERE production_id = ?",
            ("manual-production",),
        ).fetchone()
    assert row == (VIDEO_ID, "manual", "public")


def test_auto_match_links_exact_title_and_analytics_collects_manual_video(tmp_path):
    database = tmp_path / "match.sqlite3"
    _ready_production(database, tmp_path / "production", "match-production")

    class Request:
        def __init__(self, result):
            self.result = result

        def execute(self, **kwargs):
            return self.result

    class YouTube:
        def channels(self):
            return SimpleNamespace(list=lambda **kwargs: Request({
                "items": [{"contentDetails": {"relatedPlaylists": {"uploads": "uploads-id"}}}]
            }))

        def playlistItems(self):
            return SimpleNamespace(list=lambda **kwargs: Request({
                "items": [{"snippet": {
                    "title": "A Small Step Changes Everything",
                    "resourceId": {"videoId": VIDEO_ID},
                }}]
            }))

    matcher = YouTubeAutoMatcher(
        database,
        oauth=SimpleNamespace(authorized_credentials=lambda: object()),
        api_builder=lambda credentials: YouTube(),
    )
    assert matcher.run()["matched"][0]["video_id"] == VIDEO_ID

    class Reports:
        def query(self, **kwargs):
            if "filters" in kwargs:
                assert kwargs["filters"] == f"video=={VIDEO_ID}"
            return Request({
                "columnHeaders": [
                    {"name": "day"}, {"name": "views"}, {"name": "likes"},
                    {"name": "comments"}, {"name": "estimatedMinutesWatched"},
                    {"name": "averageViewDuration"}, {"name": "averageViewPercentage"},
                    {"name": "subscribersGained"},
                ],
                "rows": [["2026-09-30", 12, 2, 1, 3, 15, 50, 1]],
            })

    analytics = YouTubeAnalyticsService(
        database,
        oauth=SimpleNamespace(authorized_credentials=lambda: object()),
        api_builder=lambda credentials: SimpleNamespace(reports=lambda: Reports()),
    )
    reports = analytics.collect_uploaded_videos("2026-09-30", "2026-09-30")

    assert reports[0]["video_id"] == VIDEO_ID
    assert reports[0]["retrieved"] is True
    assert get_analytics(video_id=VIDEO_ID, database_path=database)
    daily = DailyReportService(
        database, tmp_path / "reports", analytics=analytics
    ).run()
    assert daily["report"]["analytics"]["videos"][0]["video_id"] == VIDEO_ID
    assert daily["report"]["uploads_succeeded"] == 1
    assert f"https://www.youtube.com/watch?v={VIDEO_ID}" in daily["report"]["public_video_urls"]


def test_old_upload_table_migrates_without_losing_rows(tmp_path):
    database = tmp_path / "migration.sqlite3"
    _ready_production(database, tmp_path / "production", "migration-production")
    initialize_youtube_database(database)
    with sqlite3.connect(database) as connection:
        connection.execute("DROP TABLE youtube_uploads")
        connection.execute("""CREATE TABLE youtube_uploads (
            production_id TEXT PRIMARY KEY REFERENCES productions(id), channel_id TEXT,
            video_id TEXT UNIQUE, video_url TEXT,
            status TEXT NOT NULL CHECK (status IN ('awaiting_review', 'approved', 'uploading',
                'uploaded_private', 'uploaded', 'failed', 'cancelled')),
            visibility TEXT NOT NULL CHECK (visibility IN ('private', 'unlisted', 'public')),
            resumable_uri TEXT, attempt_count INTEGER NOT NULL DEFAULT 0, lease_until TEXT,
            api_response_json TEXT, error TEXT, retry_after TEXT, uploaded_at TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""")
        connection.execute(
            "INSERT INTO youtube_uploads (production_id, status, visibility, created_at, updated_at) VALUES (?, 'approved', 'public', 'now', 'now')",
            ("migration-production",),
        )

    initialize_youtube_database(database)
    with sqlite3.connect(database) as connection:
        preserved = connection.execute(
            "SELECT status FROM youtube_uploads WHERE production_id = ?",
            ("migration-production",),
        ).fetchone()
        assert preserved == ("approved",)
        connection.execute("DELETE FROM youtube_uploads WHERE production_id = ?", ("migration-production",))
    link_manual_upload("migration-production", VIDEO_ID, database)
    with sqlite3.connect(database) as connection:
        row = connection.execute(
            "SELECT status, video_id FROM youtube_uploads WHERE production_id = ?",
            ("migration-production",),
        ).fetchone()
    assert row == ("manual", VIDEO_ID)