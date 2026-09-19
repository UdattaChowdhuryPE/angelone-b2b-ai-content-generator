from unittest.mock import MagicMock, patch

import os

import pytest
import requests

from providers.heygen import HeyGenAvatarProvider


def _make_provider():
    with patch.dict(
        os.environ,
        {"HEYGEN_API_KEY": "test-heygen-key", "HEYGEN_AVATAR_ID": "test-avatar-id"},
        clear=False,
    ):
        return HeyGenAvatarProvider()


def test_missing_keys_raise():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="HEYGEN_API_KEY"):
            HeyGenAvatarProvider(api_key=None, avatar_id="a")
        with pytest.raises(ValueError, match="HEYGEN_AVATAR_ID"):
            HeyGenAvatarProvider(api_key="k", avatar_id=None)


def test_upload_audio_posts_multipart_and_returns_asset_id(tmp_path):
    provider = _make_provider()
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"fake-audio")

    mock_response = MagicMock()
    mock_response.json.return_value = {"data": {"asset_id": "ast_123"}}
    mock_response.raise_for_status = MagicMock()

    with patch("providers.heygen.requests.post", return_value=mock_response) as mock_post:
        asset_id = provider.upload_audio(str(audio))

    assert asset_id == "ast_123"
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.heygen.com/v3/assets"
    assert kwargs["headers"] == {"x-api-key": "test-heygen-key"}
    assert "file" in kwargs["files"]


def test_create_video_posts_avatar_audio_asset():
    provider = _make_provider()
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": {"video_id": "vid_1", "status": "waiting"}}
    mock_response.raise_for_status = MagicMock()

    with patch("providers.heygen.requests.post", return_value=mock_response) as mock_post:
        body = provider.create_video("ast_123")

    assert body["data"]["video_id"] == "vid_1"
    args, kwargs = mock_post.call_args
    assert args[0] == "https://api.heygen.com/v3/videos"
    payload = kwargs["json"]
    assert payload["type"] == "avatar"
    assert payload["avatar_id"] == "test-avatar-id"
    assert payload["audio_asset_id"] == "ast_123"
    assert payload["aspect_ratio"] == "9:16"
    assert payload["resolution"] == "720p"
    assert payload["engine"] == {"type": "avatar_iv"}


def test_wait_for_result_polls_until_completed():
    provider = _make_provider()
    waiting = {"data": {"video_id": "vid_1", "status": "processing"}}
    done = {"data": {"video_id": "vid_1", "status": "completed", "video_url": "http://x/v.mp4"}}
    with patch.object(provider, "get_status", side_effect=[waiting, done]) as mock_get:
        with patch("providers.heygen.time.sleep", return_value=None):
            result = provider.wait_for_result("vid_1", poll_interval=0, timeout=60)
    assert result["data"]["status"] == "completed"
    assert mock_get.call_count == 2


def test_wait_for_result_failed_raises():
    provider = _make_provider()
    failed = {"data": {"video_id": "vid_1", "status": "failed"}}
    with patch.object(provider, "get_status", return_value=failed):
        with patch("providers.heygen.time.sleep", return_value=None):
            with pytest.raises(RuntimeError, match="failed"):
                provider.wait_for_result("vid_1", poll_interval=0, timeout=60)


def test_wait_for_result_error_raises():
    provider = _make_provider()
    error = {"data": {"video_id": "vid_1", "status": "error"}}
    with patch.object(provider, "get_status", return_value=error):
        with patch("providers.heygen.time.sleep", return_value=None):
            with pytest.raises(RuntimeError, match="error"):
                provider.wait_for_result("vid_1", poll_interval=0, timeout=60)


def test_wait_for_result_cancelled_raises():
    provider = _make_provider()
    cancelled = {"data": {"video_id": "vid_1", "status": "cancelled"}}
    with patch.object(provider, "get_status", return_value=cancelled):
        with patch("providers.heygen.time.sleep", return_value=None):
            with pytest.raises(RuntimeError, match="cancelled"):
                provider.wait_for_result("vid_1", poll_interval=0, timeout=60)


def test_http_error_propagates(tmp_path):
    provider = _make_provider()
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"x")

    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("bad")
    with patch("providers.heygen.requests.post", return_value=mock_response):
        with pytest.raises(requests.HTTPError):
            provider.upload_audio(str(audio))
