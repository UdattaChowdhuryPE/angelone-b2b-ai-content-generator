"""HeyGen green-screen future-render path (mocked HTTP, no paid calls)."""

import os
from unittest.mock import MagicMock, patch

import pytest

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


def test_green_background_payload_shape():
    bg = HeyGenAvatarProvider.green_background()
    assert bg == {"type": "color", "value": "#00FF00"}
    with pytest.raises(ValueError):
        HeyGenAvatarProvider.green_background("not-a-color")
    with pytest.raises(ValueError):
        HeyGenAvatarProvider.green_background("#FFF")


def test_create_video_green_payload_mattes_onto_color():
    """Green payload carries color background WITH remove_background.

    Regression cover for the first paid probe: background color sent
    WITHOUT remove_background was silently ignored by the server
    (near-white render), repeating the V0.9 image-background lesson.
    """
    provider = _make_provider()
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": {"video_id": "vid_g"}}
    mock_response.raise_for_status = MagicMock()
    green = HeyGenAvatarProvider.green_background()
    with patch("providers.heygen.requests.post", return_value=mock_response) as mock_post:
        provider.create_video("ast_1", background=green)
    payload = mock_post.call_args[1]["json"]
    assert payload["fit"] == "cover"
    assert payload["background"] == green
    assert payload["remove_background"] is True  # required, else white
    assert payload["engine"] == {"type": "avatar_iv"}


def test_create_video_rejects_both_background_forms():
    provider = _make_provider()
    with pytest.raises(ValueError, match="never both"):
        provider.create_video(
            "ast_1",
            background_asset_id="img_1",
            background={"type": "color", "value": "#00FF00"},
        )
    with pytest.raises(ValueError, match="must be"):
        provider.create_video("ast_1", background={"type": "image"})


def test_generate_green_happy_path_no_image_upload(tmp_path):
    provider = _make_provider()
    audio = _audio(tmp_path)
    out = str(tmp_path / "avatar_green.mp4")
    with (
        patch.object(provider, "upload_audio", return_value="ast_g") as up_audio,
        patch.object(provider, "upload_image") as up_img,
        patch.object(
            provider,
            "create_video",
            return_value={"data": {"video_id": "vid_g"}},
        ) as create,
        patch.object(
            provider,
            "wait_for_result",
            return_value={"data": {"status": "completed", "video_url": "http://x/g.mp4"}},
        ),
        patch.object(provider, "download_video", return_value=out) as dl,
    ):
        result = provider.generate_green(audio, out)
    assert result == out
    up_audio.assert_called_once_with(audio)
    up_img.assert_not_called()  # green path uploads audio only
    assert create.call_args[0] == ("ast_g",)
    assert create.call_args[1].get("background") == {
        "type": "color",
        "value": "#00FF00",
    }
    dl.assert_called_once_with("http://x/g.mp4", out)


def test_generate_green_missing_audio_raises(tmp_path):
    provider = _make_provider()
    with pytest.raises(FileNotFoundError):
        provider.generate_green(str(tmp_path / "nope.mp3"), str(tmp_path / "o.mp4"))
