import pytest

from app.database.db import create_production, update_production
from app.youtube.repository import (
    QuotaBudgetExceeded,
    approve_production,
    claim_upload,
    get_quota_usage,
    initialize_youtube_database,
    reserve_upload_quota,
)


def _approved_production(database, root, production_id):
    create_production(production_id, 1, "Topic", root, database_path=database)
    update_production(production_id, "completed", {}, database_path=database)
    approve_production(
        production_id,
        "public",
        {"title": "Short", "self_declared_made_for_kids": False},
        database_path=database,
        approved_channel_id="UC-channel",
        authenticated_channel_id_at_approval="UC-channel",
    )


def test_quota_reservation_is_idempotent_and_enforces_daily_limit(tmp_path):
    database = tmp_path / "idempotency.sqlite3"
    initialize_youtube_database(database)
    _approved_production(database, tmp_path / "production-one", "production-one")
    _approved_production(database, tmp_path / "production-two", "production-two")

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
    usage = get_quota_usage(database_path=database)
    assert usage["uploads"] == 1
    assert usage["quota_units"] == 1600


def test_active_upload_claim_cannot_be_acquired_twice(tmp_path):
    database = tmp_path / "claims.sqlite3"
    _approved_production(database, tmp_path / "production", "single-production")

    claim_upload("single-production", database)

    with pytest.raises(ValueError, match="already in progress|state"):
        claim_upload("single-production", database)
