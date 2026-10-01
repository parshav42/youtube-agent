"""Offline release QA and authenticated local production review checks."""

from app.video.quality import run_video_quality_assurance


def test_video_qa_missing_and_invalid_media_fail_closed(tmp_path):
    missing = run_video_quality_assurance(tmp_path / "missing.mp4")
    invalid_path = tmp_path / "invalid.mp4"
    invalid_path.write_bytes(b"not media")
    invalid = run_video_quality_assurance(invalid_path)
    assert missing["technical_status"] == "failed"
    assert "missing" in missing["findings"][0]
    assert invalid["technical_status"] == "failed"

