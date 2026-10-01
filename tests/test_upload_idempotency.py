import pytest

from app.database.db import create_production
from app.youtube.repository import (
    QuotaBudgetExceeded,
    get_quota_usage,
    initialize_youtube_database,
    reserve_upload_quota,
)


def test_quota_reservation_is_idempotent_and_enforces_daily_limit(tmp_path):
    database = tmp_path / "idempotency.sqlite3"
    initialize_youtube_database(database)
    for production_id in ("production-one", "production-two"):
        create_production(production_id, 1, "Topic", tmp_path / production_id, database_path=database)

    first = reserve_upload_quota(
        "production-one", "UC-channel", max_uploads=1, database_path=database
    )
    repeated = reserve_upload_quota(
        "production-one", "UC-channel", max_uploads=1, database_path=database
    )
    assert first["id"] == repeated["id"]

    with pytest.raises(QuotaBudgetExceeded, match="MAX_UPLOADS_PER_DAY"):
        reserve_upload_quota(
            "production-two", "UC-channel", max_uploads=1, database_path=database
        )
    assert get_quota_usage(database_path=database)["uploads"] == 1