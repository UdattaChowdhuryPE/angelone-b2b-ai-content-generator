"""avatar_mode="full_scene": production default for complete-scene Photo Avatars.

Covers (mocked subprocess/HTTP — no paid calls, no ffmpeg required):
  A. HeyGen payload: no background keys for the full_scene render.
  B. Full_scene compositor: no chromakey/despill/desk/BG/presenter geometry.
  C. Normalization: whole-video cover to 1080x1920.
  D. Captions remain the topmost layer.
  E. B-roll: full_scene -> B-roll -> full_scene scene cuts.
  F. Legacy regression is covered by the untouched existing suite.

The legacy green-screen implementation is preserved byte-for-byte;
this module only pins the new explicit mode.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from providers.heygen import FullSceneAvatarProvider, HeyGenAvatarProvider
from video import compositor
from video.compositor import CompositorError, compose_final
from tests.fakes import make_storyboard


def _make_provider(**overrides):
    env = {
        "HEYGEN_API_KEY": "test-heygen-key",
        "HEYGEN_AVATAR_ID": "test-photo-avatar-id",
    }
    env.update(overrides)
    with patch.dict(os.environ, env, clear=False):
        return FullSceneAvatarProvider()


def _audio(tmp_path):
    path = tmp_path / "voice.mp3"
    path.write_bytes(b"fake-audio")
    return str(path)


# --- A. HeyGen payload ------------------------------------------------------

def test_full_scene_payload_has_no_background_keys(tmp_path):
    """generate_full_scene uploads audio only; create has no BG keys."""
    provider = _make_provider()
    audio = _audio(tmp_path)
    out = str(tmp_path / "avatar.mp4")
    with (
        patch.object(provider, "upload_audio", return_value="ast_1") as up_audio,
        patch.object(provider, "upload_image") as up_img,
        patch.object(
            provider, "create_video", return_value={"data": {"video_id": "vid_1"}}
        ) as create,
        patch.object(
            provider,
            "wait_for_result",
            return_value={"data": {"status": "completed", "video_url": "http://x/v.mp4"}},
        ),
        patch.object(provider, "download_video", return_value=out) as dl,
    ):
        assert provider.generate(audio, out) == out
    up_audio.assert_called_once_with(audio)
    up_img.assert_not_called()  # never upload a background for full_scene
    assert create.call_count == 1
    assert create.call_args[0] == ("ast_1",)
    assert create.call_args[1] == {}  # no background kwargs at all
    dl.assert_called_once_with("http://x/v.mp4", out)


def test_full_scene_create_payload_shape():
    """Wire-level payload: avatar IV 9:16 720p, background keys absent."""
    provider = _make_provider()
    mock_response = MagicMock()
    mock_response.json.return_value = {"data": {"video_id": "vid_1"}}
    mock_response.raise_for_status = MagicMock()
    with patch("providers.heygen.requests.post", return_value=mock_response) as mock_post:
        provider.create_video("ast_1")
    payload = mock_post.call_args[1]["json"]
    assert payload["avatar_id"] == "test-photo-avatar-id"
    assert payload["audio_asset_id"] == "ast_1"
    assert payload["aspect_ratio"] == "9:16"
    assert payload["resolution"] == "720p"
    assert payload["engine"] == {"type": "avatar_iv"}
    assert "background" not in payload
    assert "background_asset_id" not in payload
    assert "remove_background" not in payload
    assert "fit" not in payload


def test_full_scene_provider_is_heygen_subclass():
    assert issubclass(FullSceneAvatarProvider, HeyGenAvatarProvider)
    assert HeyGenAvatarProvider.DEFAULT_AVATAR_MODE == "full_scene"


def test_full_scene_missing_audio_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        _make_provider().generate(str(tmp_path / "nope.mp3"), str(tmp_path / "o.mp4"))


# --- B/C/D/E. Compositor (mocked ffmpeg) ------------------------------------

def _avatar_probe(duration=20.0, audio=True):
    streams = [
        {"codec_type": "video", "codec_name": "h264", "width": 720, "height": 1280},
    ]
    if audio:
        streams.append({"codec_type": "audio", "codec_name": "aac"})
    return {"format": {"duration": str(duration)}, "streams": streams}


def _final_probe(duration=20.0):
    return {
        "format": {"duration": str(duration)},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1080,
                "height": 1920,
                "pix_fmt": "yuv420p",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }


def _run_full_scene(tmp_path, board=None, broll=None, **kwargs):
    board = board or make_storyboard()
    avatar = tmp_path / "avatar.mp4"
    avatar.write_bytes(b"avatarbytes")
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"audiobytes")
    out = str(tmp_path / "final.mp4")
    captured = {}

    def fake_probe(path):
        if str(path).endswith("avatar.mp4"):
            return _avatar_probe()
        return _final_probe()

    def fake_run(cmd, **kw):
        # Two-pass assembly issues one ffmpeg argv per pass; assertions
        # read the concatenated argv so shape checks keep working.
        captured.setdefault("cmds", []).append(list(cmd))
        captured["cmd"] = [a for c in captured["cmds"] for a in c]
        with open(cmd[-1], "wb") as f:
            f.write(b"videobytes")
        return None

    def fake_render(segments, cap_dir, font_path=None):
        os.makedirs(cap_dir, exist_ok=True)
        paths = []
        for i in range(len(segments)):
            p = os.path.join(cap_dir, f"cap{i:02d}.png")
            with open(p, "wb") as f:
                f.write(b"png")
            paths.append(p)
        captured["segments"] = segments
        return paths

    with (
        patch.object(compositor, "probe_media", side_effect=fake_probe),
        patch.object(compositor.subprocess, "run", side_effect=fake_run),
        patch.object(compositor, "render_all_cards", side_effect=fake_render),
    ):
        result = compose_final(
            audio_path=str(audio),
            avatar_path=str(avatar),
            broll={} if broll is None else broll,
            storyboard=board,
            output_path=out,
            job_dir=str(tmp_path),
            script_text="Hook. Point one. Takeaway.",
            avatar_mode="full_scene",
            **kwargs,
        )
    return result, captured


def test_full_scene_has_no_chromakey_desk_or_bg(tmp_path):
    """B: the filter graph must contain none of the green-screen stages."""
    out, captured = _run_full_scene(tmp_path)
    assert out.endswith("final.mp4")
    joined = " ".join(captured["cmd"])
    assert "chromakey" not in joined
    assert "despill" not in joined
    assert "desk_foreground" not in joined
    assert "split=" not in joined  # no BG/desk fan-out branches
    assert "yuva420p" not in joined  # keyed transparency never emitted
    assert "overlay=320:475" not in joined  # no presenter positioning
    assert "overlay=0:1020" not in joined  # no desk strip overlay
    assert not (tmp_path / "desk_foreground.png").exists()


def test_full_scene_normalizes_whole_video(tmp_path):
    """C: proportional cover of the entire frame to exactly 1080x1920."""
    _, captured = _run_full_scene(tmp_path)
    joined = " ".join(captured["cmd"])
    assert "crop=1080:1920" in joined
    assert "scale=1080:1920" in joined
    assert "+faststart" in joined and "libx264" in joined


def test_full_scene_captions_last(tmp_path):
    """D: caption overlays are applied after scene assembly."""
    _, captured = _run_full_scene(tmp_path)
    joined = " ".join(captured["cmd"])
    assert "overlay=0:0:enable='between(t," in joined
    assert captured["segments"]  # verbatim cards were rendered


def test_full_scene_broll_scene_cuts(tmp_path):
    """E: full_scene -> B-roll -> full_scene via ordinary cuts."""
    from pipeline.models import Scene, Storyboard

    board = Storyboard(
        scenes=[
            Scene(scene_id=1, start=0.0, end=8.0, narration="Hook.",
                  key_claim="c1", visual_type="presenter"),
            Scene(scene_id=2, start=8.0, end=20.0, narration="Point one.",
                  key_claim="c2", visual_type="clip",
                  visual_prompt="market chart clip",
                  broll_required=True),
        ],
        total_duration=20.0,
    )
    clip = tmp_path / "broll2.mp4"
    clip.write_bytes(b"clipbytes")
    out, captured = _run_full_scene(tmp_path, board=board, broll={2: str(clip)})
    assert out.endswith("final.mp4")
    joined = " ".join(captured["cmd"])
    assert "chromakey" not in joined
    assert "concat=n=2" in joined  # one avatar scene + one B-roll cut
    assert str(clip) in captured["cmd"]
    assert not (tmp_path / "desk_foreground.png").exists()


def test_full_scene_rejects_background_path(tmp_path):
    """Passing a studio background with full_scene fails loudly."""
    with pytest.raises(CompositorError, match="studio_background_path"):
        _run_full_scene(
            tmp_path,
            studio_background_path=str(tmp_path / "bg.png"),
        )


def test_full_scene_never_reads_studio_geometry(tmp_path, monkeypatch):
    """STUDIO_* geometry constants are legacy-only; full_scene ignores them."""
    import video.compositor as comp

    for name in (
        "STUDIO_AVATAR_WIDTH_PX",
        "STUDIO_AVATAR_HEIGHT_PX",
        "STUDIO_AVATAR_X_PX",
        "STUDIO_AVATAR_TOP_PX",
        "STUDIO_DESK_TOP_PX",
        "STUDIO_DESK_HEIGHT_PX",
    ):
        monkeypatch.setattr(comp, name, -9999)
    out, _ = _run_full_scene(tmp_path)
    assert out.endswith("final.mp4")
