from app.database.db import create_production, update_production
from app.youtube.auto_publish import AutoPublishService
from app.youtube.repository import approve_production, get_upload, update_upload_progress


def test_auto_publish_is_disabled_by_default_and_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_APPROVE_SCRIPTS", "false")
    monkeypatch.setenv("AUTO_APPROVE_PRODUCTIONS", "false")
    monkeypatch.setenv("YOUTUBE_PRIVACY_STATUS", "public")

    result = AutoPublishService(tmp_path / "auto.sqlite3").run()

    assert result["status"] == "disabled"
    assert result["uploads_attempted"] == 0
    assert result["uploads_succeeded"] == 0


def test_auto_publish_uploads_only_a_preapproved_public_production(tmp_path, monkeypatch):
    database = tmp_path / "auto.sqlite3"
    production_id = "public-production"
    create_production(production_id, 1, "Topic", tmp_path / "production", database_path=database)
    update_production(production_id, "completed", {"video": {}}, database_path=database)
    approve_production(
        production_id,
        "public",
        {"title": "Short", "self_declared_made_for_kids": False},
        database_path=database,
        approved_channel_id="UC-channel",
        authenticated_channel_id_at_approval="UC-channel",
    )
    for name, value in {
        "AUTO_PUBLISH_ENABLED": "true",
        "AUTO_APPROVE_SCRIPTS": "false",
        "AUTO_APPROVE_PRODUCTIONS": "false",
        "YOUTUBE_PRIVACY_STATUS": "public",
        "MAX_UPLOADS_PER_DAY": "5",
    }.items():
        monkeypatch.setenv(name, value)

    class Publisher:
        def upload(self, item_id):
            return update_upload_progress(
                item_id,
                status="uploaded",
                video_id="abcdefghijk",
                visibility="public",
                channel_id="UC-channel",
                database_path=database,
            )

    service = AutoPublishService(database, publisher=Publisher())
    service._create_researched_script = lambda result, settings: None

    result = service.run()

    assert result["uploads_attempted"] == 1
    assert result["uploads_succeeded"] == 1
    assert result["public_video_urls"] == ["https://www.youtube.com/watch?v=abcdefghijk"]
    assert result["quota_usage"]["quota_units"] == 1600
    assert get_upload(production_id, database)["visibility"] == "public"
