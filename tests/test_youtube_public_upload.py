import pytest

from app.youtube.publisher import YouTubePublishError, YouTubePublisher


def test_public_upload_response_must_remain_public():
    approval = {
        "visibility": "public",
        "approved_channel_id": "UC123",
        "metadata": {"self_declared_made_for_kids": False},
    }
    response = {
        "id": "abcdefghijk",
        "snippet": {"channelId": "UC123"},
        "status": {"privacyStatus": "private", "selfDeclaredMadeForKids": False},
    }

    with pytest.raises(YouTubePublishError, match="required audit"):
        YouTubePublisher._validate_upload_result(response, approval)


def test_public_upload_response_with_public_visibility_is_accepted():
    approval = {
        "visibility": "public",
        "approved_channel_id": "UC123",
        "metadata": {"self_declared_made_for_kids": False},
    }
    response = {
        "id": "abcdefghijk",
        "snippet": {"channelId": "UC123"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }

    assert YouTubePublisher._validate_upload_result(response, approval) == "abcdefghijk"