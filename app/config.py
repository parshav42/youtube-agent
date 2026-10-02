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
    """Return the configured script quality threshold."""
    value = os.getenv(
        "MIN_SCRIPT_QUALITY_SCORE",
        os.getenv("MOTIVATIONOS_QUALITY_THRESHOLD", "7.5"),
    )
    try:
        threshold = float(value)
    except ValueError as error:
        raise ValueError("MIN_SCRIPT_QUALITY_SCORE must be a number from 0 to 10.") from error
    if not 0 <= threshold <= 10:
        raise ValueError("MIN_SCRIPT_QUALITY_SCORE must be between 0 and 10.")
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


def get_image_provider_name() -> str:
    """Return the selected image provider, defaulting to manual local assets."""
    provider = os.getenv("IMAGE_PROVIDER", "manual").strip().lower()
    if provider not in {"manual", "pollinations"}:
        raise ValueError("IMAGE_PROVIDER must be 'pollinations' or 'manual'.")
    return provider


def get_pollinations_model() -> str:
    """Return the Pollinations image model name."""
    model = os.getenv("POLLINATIONS_MODEL", "flux").strip()
    if not model:
        raise ValueError("POLLINATIONS_MODEL must not be empty.")
    return model


def get_pollinations_api_key() -> str | None:
    """Return an optional Pollinations API key without exposing it."""
    key = os.getenv("POLLINATIONS_API_KEY", "").strip()
    return key or None


def get_pollinations_width() -> int:
    """Return the requested Pollinations image width."""
    return _get_int("POLLINATIONS_WIDTH", 1080, 320, 4096)


def get_pollinations_height() -> int:
    """Return the requested Pollinations image height."""
    return _get_int("POLLINATIONS_HEIGHT", 1920, 320, 4096)


def get_pollinations_timeout_seconds() -> int:
    """Return the Pollinations request timeout in seconds."""
    return _get_int("POLLINATIONS_TIMEOUT_SECONDS", 120, 1, 600)


def get_youtube_visibility() -> str:
    """Return the configured default visibility; publishing still needs explicit approval."""
    visibility = os.getenv("MOTIVATIONOS_YOUTUBE_VISIBILITY", "private").strip().lower()
    if visibility not in {"private", "unlisted", "public"}:
        raise ValueError("MOTIVATIONOS_YOUTUBE_VISIBILITY must be private, unlisted, or public.")
    return visibility


def get_auto_publish_settings() -> dict[str, object]:
    """Parse fail-closed automatic publishing policy and quota configuration."""
    enabled = _get_bool("AUTO_PUBLISH_ENABLED", False)
    visibility = os.getenv("YOUTUBE_PRIVACY_STATUS", "public").strip().lower()
    if visibility not in {"private", "unlisted", "public"}:
        raise ValueError("YOUTUBE_PRIVACY_STATUS must be private, unlisted, or public.")
    if enabled and visibility != "public":
        raise ValueError("Automatic publishing is public-only; set YOUTUBE_PRIVACY_STATUS=public.")
    category_id = os.getenv("YOUTUBE_CATEGORY_ID", "22").strip()
    if not category_id.isdigit() or len(category_id) > 3:
        raise ValueError("YOUTUBE_CATEGORY_ID must contain 1 to 3 digits.")
    return {
        "enabled": enabled,
        "cron": os.getenv("AUTO_PUBLISH_CRON", "0 9 * * *").strip(),
        "privacy_status": visibility,
        "auto_approve_scripts": _get_bool("AUTO_APPROVE_SCRIPTS", False),
        "auto_approve_productions": _get_bool("AUTO_APPROVE_PRODUCTIONS", False),
        "min_script_quality_score": _get_score("MIN_SCRIPT_QUALITY_SCORE", 7.5),
        "min_audio_quality_score": _get_score("MIN_AUDIO_QUALITY_SCORE", 8.0),
        "min_video_quality_score": _get_score("MIN_VIDEO_QUALITY_SCORE", 8.0),
        "max_uploads_per_day": _get_int("MAX_UPLOADS_PER_DAY", 5, 1, 100),
        "insert_quota_units": 1600,
        "daily_quota_units": _get_int("YOUTUBE_DAILY_QUOTA_UNITS", 10000, 1600, 1000000),
        "category_id": category_id,
        "playlist_id": os.getenv("YOUTUBE_PLAYLIST_ID", "").strip(),
        "notify_subscribers": _get_bool("YOUTUBE_NOTIFY_SUBSCRIBERS", True),
        "made_for_kids": _get_bool("YOUTUBE_MADE_FOR_KIDS", False),
    }


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name, str(default)).strip().lower()
    if value not in {"true", "false", "1", "0", "yes", "no"}:
        raise ValueError(f"{name} must be a boolean value.")
    return value in {"true", "1", "yes"}


def _get_score(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be a number between 0 and 10.") from error
    if not 0 <= value <= 10:
        raise ValueError(f"{name} must be a number between 0 and 10.")
    return value


def _get_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as error:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}.") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}.")
    return value


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


def get_llm_provider() -> str:
    """Return the configured LLM backend, defaulting to Sarvam."""
    provider = os.getenv("LLM_PROVIDER", "sarvam").strip().lower()
    if provider not in {"huggingface", "sarvam"}:
        raise ValueError("LLM_PROVIDER must be 'huggingface' or 'sarvam'.")
    return provider


def get_sarvam_settings() -> dict[str, str]:
    """Return Sarvam connection settings, including its locally configured key."""
    return {
        "api_key": os.getenv("SARVAM_API_KEY", "").strip(),
        "base_url": os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai/v1").strip(),
        "model": os.getenv("SARVAM_MODEL_NAME", "sarvam-105b").strip(),
    }