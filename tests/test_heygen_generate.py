"""HeyGenAvatarProvider.generate() with mocked HTTP (no real API calls)."""

import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from providers.heygen import HeyGenAvatarProvider


def _make_provider(**overrides):
    env = {
        "HEYGEN_API_KEY": "test-heygen-key",
        "HEYGEN_AVATAR_ID": "test-avatar-id",
    }
    env.update(overrides)
    with patch.dict(os.environ, env, clear=False):
        return HeyGenAvatarProvider()


def _audio(tmp_path):
    path = tmp_path / "voice.mp3"
    path.write_bytes(b"fake-audio")
    return str(path)


def _bg(tmp_path):
    from PIL import Image

    path = tmp_path / "studio_background.png"
    Image.new("RGB", (72, 128), (20, 40, 80)).save(path)
    return str(path)


def test_generate_happy_path_returns_output(tmp_path):
    provider = _make_provider()
    audio = _audio(tmp_path)
    bg = _bg(tmp_path)
    provider.background_image_path = bg
    out = str(tmp_path / "avatar.mp4")

    with (
        patch.object(provider, "upload_audio", return_value="ast_1") as up_audio,
        patch.object(provider, "upload_image", return_value="img_1") as up_img,
        patch.object(
            provider, "create_video", return_value={"data": {"video_id": "vid_1"}}
        ) as create,
        patch.object(
            provider,
            "wait_for_result",
            return_value={"data": {"status": "completed", "video_url": "http://x/v.mp4"}},
        ) as wait,
        patch.object(provider, "download_video", return_value=out) as dl,
    ):
        result = provider.generate(audio, out)

    assert result == out
    up_audio.assert_called_once_with(audio)
    # Canonical bg is never uploaded directly: generate() uploads a
    # cover-normalized 720x1280 variant and removes the temp file afterwards.
    assert up_img.call_count == 1
    (uploaded_path,) = up_img.call_args[0]
    assert uploaded_path != bg
    assert not os.path.exists(uploaded_path)
    assert os.path.exists(bg)
    assert create.call_args[0] == ("ast_1",)
    assert create.call_args[1].get("background_asset_id") == "img_1"
    wait.assert_called_once()
    dl.assert_called_once_with("http://x/v.mp4", out)


def test_create_video_background_payload_shape():
    """Studio-bg payload carries fit/background/remove_background; plain does not."""
    provider = _make_provider()
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": {"video_id": "vid_1"}}
    mock_response.raise_for_status = MagicMock()
    with patch("providers.heygen.requests.post", return_value=mock_response) as mock_post:
        provider.create_video("ast_1", background_asset_id="img_9")
    bg_payload = mock_post.call_args[1]["json"]
    assert bg_payload["fit"] == "cover"
    assert bg_payload["background"] == {"type": "image", "asset_id": "img_9"}
    assert bg_payload["remove_background"] is True
    assert bg_payload["engine"] == {"type": "avatar_iv"}
    assert bg_payload["aspect_ratio"] == "9:16"
    assert bg_payload["resolution"] == "720p"

    with patch("providers.heygen.requests.post", return_value=mock_response) as mock_post:
        provider.create_video("ast_1")
    plain_payload = mock_post.call_args[1]["json"]
    assert "background" not in plain_payload
    assert "remove_background" not in plain_payload
    assert "fit" not in plain_payload


def test_generate_missing_audio_raises(tmp_path):
    provider = _make_provider()
    with pytest.raises(FileNotFoundError):
        provider.generate(str(tmp_path / "nope.mp3"), str(tmp_path / "o.mp4"))


def test_generate_missing_background_raises(tmp_path):
    provider = _make_provider()
    provider.background_image_path = str(tmp_path / "missing_bg.png")
    with pytest.raises(FileNotFoundError, match="studio background"):
        provider.generate(_audio(tmp_path), str(tmp_path / "o.mp4"))


def test_generate_completed_without_url_raises(tmp_path):
    provider = _make_provider()
    provider.background_image_path = _bg(tmp_path)
    with (
        patch.object(provider, "upload_audio", return_value="a"),
        patch.object(provider, "upload_image", return_value="i"),
        patch.object(
            provider, "create_video", return_value={"data": {"video_id": "v"}}
        ),
        patch.object(
            provider, "wait_for_result", return_value={"data": {"status": "completed"}}
        ),
    ):
        with pytest.raises(RuntimeError, match="no video_url"):
            provider.generate(_audio(tmp_path), str(tmp_path / "o.mp4"))


def test_generate_failure_status_raises(tmp_path):
    provider = _make_provider()
    provider.background_image_path = _bg(tmp_path)
    with (
        patch.object(provider, "upload_audio", return_value="a"),
        patch.object(provider, "upload_image", return_value="i"),
        patch.object(
            provider, "create_video", return_value={"data": {"video_id": "v"}}
        ),
        patch.object(
            provider,
            "wait_for_result",
            side_effect=RuntimeError("HeyGen generation failed with status 'failed'"),
        ),
    ):
        with pytest.raises(RuntimeError, match="failed"):
            provider.generate(_audio(tmp_path), str(tmp_path / "o.mp4"))


def test_generate_timeout_propagates(tmp_path):
    provider = _make_provider()
    provider.background_image_path = _bg(tmp_path)
    with (
        patch.object(provider, "upload_audio", return_value="a"),
        patch.object(provider, "upload_image", return_value="i"),
        patch.object(
            provider, "create_video", return_value={"data": {"video_id": "v"}}
        ),
        patch.object(
            provider, "wait_for_result", side_effect=TimeoutError("timed out")
        ),
    ):
        with pytest.raises(TimeoutError):
            provider.generate(_audio(tmp_path), str(tmp_path / "o.mp4"))


def test_generate_http_error_propagates(tmp_path):
    provider = _make_provider()
    provider.background_image_path = _bg(tmp_path)
    with patch.object(
        provider, "upload_audio", side_effect=requests.HTTPError("bad")
    ):
        with pytest.raises(requests.HTTPError):
            provider.generate(_audio(tmp_path), str(tmp_path / "o.mp4"))


def test_upload_image_missing_file_raises(tmp_path):
    provider = _make_provider()
    with pytest.raises(FileNotFoundError):
        provider.upload_image(str(tmp_path / "nope.png"))
