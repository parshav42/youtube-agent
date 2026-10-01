"""Application configuration loaded from the environment and an optional .env file."""

import os
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_NICHE = "Hindi English discipline and mindset"
DEFAULT_TOPIC = "Discipline and consistency"

load_dotenv(PROJECT_ROOT / ".env")


def get_niche(override: str | None = None) -> str:
    """Return the requested niche, environment value, or documented default."""
    niche = override if override is not None else os.getenv("MOTIVATIONOS_NICHE", DEFAULT_NICHE)
    niche = niche.strip()
    if not niche:
        raise ValueError("The research niche must not be empty.")
    return niche


def get_database_path() -> Path:
    """Resolve the SQLite path, keeping relative paths inside the project."""
    configured_path = os.getenv("MOTIVATIONOS_DB_PATH", "data/motivationos.sqlite3")
    path = Path(configured_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_topic(override: str | None = None) -> str:
    """Return the requested script topic, configured topic, or default."""
    topic = override if override is not None else os.getenv("MOTIVATIONOS_TOPIC", DEFAULT_TOPIC)
    topic = topic.strip()
    if not topic:
        raise ValueError("The script topic must not be empty.")
    return topic


def get_quality_threshold() -> float:
    """Return the configured heuristic quality threshold."""
    try:
        threshold = float(os.getenv("MOTIVATIONOS_QUALITY_THRESHOLD", "7.5"))
    except ValueError as error:
        raise ValueError("MOTIVATIONOS_QUALITY_THRESHOLD must be a number from 0 to 10.") from error
    if not 0 <= threshold <= 10:
        raise ValueError("MOTIVATIONOS_QUALITY_THRESHOLD must be between 0 and 10.")
    return threshold


def get_speaking_rate_wps() -> float:
    """Return the configured expected narration rate in words per second."""
    try:
        rate = float(os.getenv("MOTIVATIONOS_SPEAKING_RATE_WPS", "2.5"))
    except ValueError as error:
        raise ValueError("MOTIVATIONOS_SPEAKING_RATE_WPS must be a number from 1 to 5.") from error
    if not 1 <= rate <= 5:
        raise ValueError("MOTIVATIONOS_SPEAKING_RATE_WPS must be between 1 and 5.")
    return rate


def get_production_output_path() -> Path:
    """Resolve the production output directory."""
    configured_path = os.getenv("MOTIVATIONOS_OUTPUT_DIR", "data/productions")
    path = Path(configured_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_licensed_asset_path() -> Path:
    """Resolve the configured library of user-supplied licensed assets."""
    configured_path = os.getenv("MOTIVATIONOS_LICENSED_ASSET_DIR", "data/licensed-assets")
    path = Path(configured_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_youtube_visibility() -> str:
    """Return the configured default visibility; publishing still needs explicit approval."""
    visibility = os.getenv("MOTIVATIONOS_YOUTUBE_VISIBILITY", "private").strip().lower()
    if visibility not in {"private", "unlisted", "public"}:
        raise ValueError("MOTIVATIONOS_YOUTUBE_VISIBILITY must be private, unlisted, or public.")
    return visibility


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} must be true or false.")


def get_auto_publish_settings() -> dict[str, object]:
    """Validate and return publishing controls without exposing secrets."""
    try:
        script_score = float(os.getenv("MIN_SCRIPT_QUALITY_SCORE", "8.0"))
        audio_score = float(os.getenv("MIN_AUDIO_QUALITY_SCORE", "8.0"))
        video_score = float(os.getenv("MIN_VIDEO_QUALITY_SCORE", "8.0"))
        max_uploads = int(os.getenv("MAX_UPLOADS_PER_DAY", "5"))
        category_id = os.getenv("YOUTUBE_CATEGORY_ID", "22").strip()
    except ValueError as error:
        raise ValueError("Auto-publish thresholds, upload limit, and category must be valid numbers.") from error
    for name, score in (
        ("MIN_SCRIPT_QUALITY_SCORE", script_score),
        ("MIN_AUDIO_QUALITY_SCORE", audio_score),
        ("MIN_VIDEO_QUALITY_SCORE", video_score),
    ):
        if not 0 <= score <= 10:
            raise ValueError(f"{name} must be between 0 and 10.")
    if max_uploads < 1:
        raise ValueError("MAX_UPLOADS_PER_DAY must be at least 1.")
    if not category_id.isdigit() or len(category_id) > 3:
        raise ValueError("YOUTUBE_CATEGORY_ID must be a YouTube category ID.")
    privacy_status = os.getenv("YOUTUBE_PRIVACY_STATUS", "public").strip().lower()
    if privacy_status != "public":
        raise ValueError("YOUTUBE_PRIVACY_STATUS must be 'public' for automatic publishing.")
    return {
        "enabled": _get_bool("AUTO_PUBLISH_ENABLED", False),
        "auto_approve_scripts": _get_bool("AUTO_APPROVE_SCRIPTS", False),
        "auto_approve_productions": _get_bool("AUTO_APPROVE_PRODUCTIONS", False),
        "privacy_status": privacy_status,
        "max_uploads_per_day": max_uploads,
        "daily_quota_units": 10_000,
        "insert_quota_units": 1_600,
        "min_script_quality_score": script_score,
        "min_audio_quality_score": audio_score,
        "min_video_quality_score": video_score,
        "category_id": category_id,
        "playlist_id": os.getenv("YOUTUBE_PLAYLIST_ID", "").strip() or None,
        "notify_subscribers": _get_bool("YOUTUBE_NOTIFY_SUBSCRIBERS", False),
        "made_for_kids": _get_bool("YOUTUBE_MADE_FOR_KIDS", False),
        "cron": os.getenv("AUTO_PUBLISH_CRON", "0 9 * * *").strip(),
    }


def get_youtube_notify_subscribers() -> bool:
    """Return the upload notification preference without validating auto mode."""
    return _get_bool("YOUTUBE_NOTIFY_SUBSCRIBERS", False)


def get_youtube_callback_uri() -> str:
    """Return the exact OAuth callback registered in the Google Cloud project."""
    callback_uri = os.getenv(
        "GOOGLE_REDIRECT_URI", "http://127.0.0.1:8000/api/youtube/auth/callback"
    ).strip()
    if not callback_uri.startswith("http://127.0.0.1:") and not callback_uri.startswith(
        "http://localhost:"
    ):
        raise ValueError("Google OAuth callback must use a localhost HTTP redirect URI.")
    return callback_uri


def get_report_output_path() -> Path:
    """Resolve the local daily-report directory."""
    configured_path = os.getenv("MOTIVATIONOS_REPORT_DIR", "data/reports")
    path = Path(configured_path).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def get_hf_settings() -> dict[str, str]:
    """Return non-secret Hugging Face model/provider settings."""
    return {
        "model": os.getenv("HF_MODEL", "").strip(),
        "provider": os.getenv("HF_PROVIDER", "auto").strip() or "auto",
    }