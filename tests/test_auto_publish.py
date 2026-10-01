from app.youtube.auto_publish import AutoPublishService


def test_auto_publish_is_disabled_by_default_and_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTO_PUBLISH_ENABLED", "false")
    monkeypatch.setenv("AUTO_APPROVE_SCRIPTS", "false")
    monkeypatch.setenv("AUTO_APPROVE_PRODUCTIONS", "false")
    monkeypatch.setenv("YOUTUBE_PRIVACY_STATUS", "public")

    result = AutoPublishService(tmp_path / "auto.sqlite3").run()

    assert result["status"] == "disabled"
    assert result["uploads_attempted"] == 0
    assert result["uploads_succeeded"] == 0