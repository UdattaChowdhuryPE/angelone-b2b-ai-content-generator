"""Production compositor with mocked subprocess (no ffmpeg required)."""

import json
import os
import subprocess
from unittest.mock import patch

import pytest

from video import compositor
from video.compositor import (
    CompositorError,
    Mp4ValidationError,
    compose_final,
    validate_mp4,
)
from tests.fakes import make_storyboard


def _avatar_probe(duration=20.0, audio=True):
    streams = [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 720,
            "height": 1280,
        },
        {"codec_type": "audio", "codec_name": "aac"},
    ] if audio else [
        {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 720,
            "height": 1280,
        }
    ]
    return {"format": {"duration": str(duration)}, "streams": streams}


def _final_probe(width=1080, height=1920, duration=20.0):
    return {
        "format": {"duration": str(duration)},
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": width,
                "height": height,
                "pix_fmt": "yuv420p",
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }


def _board_with_broll():
    board = make_storyboard()
    board.scenes[1].broll_required = True
    board.scenes[1].visual_prompt = "clip two"
    return board


def _run_compose(tmp_path, board, broll_files, avatar_audio=True):
    avatar = tmp_path / "avatar.mp4"
    avatar.write_bytes(b"avatarbytes")
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"audiobytes")
    out = str(tmp_path / "final.mp4")
    captured = {}

    def fake_probe(path):
        if str(path).endswith("avatar.mp4"):
            return _avatar_probe(audio=avatar_audio)
        return _final_probe()

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        # Simulate ffmpeg writing the partial output (last arg).
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
            broll=broll_files,
            storyboard=board,
            output_path=out,
            job_dir=str(tmp_path),
            script_text="Hook. Point one.",
        )
    return result, captured


def test_compose_cmd_shape_1080x1920_captions_faststart(tmp_path):
    broll_clip = tmp_path / "b.mp4"
    broll_clip.write_bytes(b"broll")
    board = _board_with_broll()
    out, captured = _run_compose(tmp_path, board, {2: str(broll_clip)})
    assert out.endswith("final.mp4")
    assert os.path.exists(out)
    assert not os.path.exists(out + ".partial")  # atomically renamed
    cmd = captured["cmd"]
    assert cmd[0] == "ffmpeg"
    joined = " ".join(cmd)
    assert "scale=1080:1920" in joined
    assert "pad=1080:1920" in joined  # B-roll conform path only
    assert "color=black" in joined  # conform padding never renders white
    assert "white" not in joined.lower()  # studio path never pads white
    assert "crop=1080:1920" in joined  # avatar studio path uses cover
    assert "force_original_aspect_ratio=increase" in joined
    assert "overlay=0:0:enable='between(t," in joined
    assert "+faststart" in joined
    assert "libx264" in joined
    assert "-f" in cmd and "mp4" in cmd  # pinned muxer for .partial output
    assert "-map" in cmd
    assert "0:a" in cmd  # avatar audio mapped, B-roll never mapped
    assert "aac" in joined
    # Caption PNGs are looped inputs after avatar + broll.
    assert "-loop" in cmd and "1" in cmd
    # Caption segments tile the storyboard duration verbatim.
    segments = captured["segments"]
    assert segments[0]["start"] == 0.0
    assert segments[-1]["end"] == board.total_duration


def test_compose_falls_back_to_voice_audio(tmp_path):
    board = make_storyboard()
    out, captured = _run_compose(tmp_path, board, {}, avatar_audio=False)
    assert os.path.exists(out)
    cmd = captured["cmd"]
    ai = cmd.index("-i")
    # voice.mp3 added as fallback audio input; mapped instead of 0:a.
    assert str(tmp_path / "voice.mp3") in cmd
    map_idx = cmd.index("-map")
    assert cmd[map_idx + 3] != "0:a"


def test_compose_missing_avatar_raises(tmp_path):
    with pytest.raises(CompositorError, match="Avatar video missing"):
        compose_final(
            audio_path="a",
            avatar_path=str(tmp_path / "nope.mp4"),
            broll={},
            storyboard=make_storyboard(),
            output_path=str(tmp_path / "final.mp4"),
        )


def test_compose_ffmpeg_failure_raises_and_cleans_partial(tmp_path):
    avatar = tmp_path / "avatar.mp4"
    avatar.write_bytes(b"x")
    with (
        patch.object(compositor, "probe_media", return_value=_avatar_probe()),
        patch.object(
            compositor.subprocess,
            "run",
            side_effect=subprocess.CalledProcessError(1, "ffmpeg"),
        ),
        patch.object(compositor, "render_all_cards", return_value=[]),
    ):
        with pytest.raises(CompositorError, match="FFmpeg assembly failed"):
            compose_final(
                audio_path="a",
                avatar_path=str(avatar),
                broll={},
                storyboard=make_storyboard(),
                output_path=str(tmp_path / "final.mp4"),
                job_dir=str(tmp_path),
            )
    assert not os.path.exists(str(tmp_path / "final.mp4.partial"))
    assert not os.path.exists(str(tmp_path / "final.mp4"))


def test_compose_wrong_resolution_fails_validation(tmp_path):
    avatar = tmp_path / "avatar.mp4"
    avatar.write_bytes(b"x")

    def fake_run(cmd, **kwargs):
        with open(cmd[-1], "wb") as f:
            f.write(b"v")

    bad_probe = _final_probe(width=720, height=1280)
    with (
        patch.object(
            compositor,
            "probe_media",
            side_effect=[_avatar_probe(), bad_probe],
        ),
        patch.object(compositor.subprocess, "run", side_effect=fake_run),
        patch.object(compositor, "render_all_cards", return_value=[]),
    ):
        with pytest.raises(Mp4ValidationError, match="1080x1920"):
            compose_final(
                audio_path="a",
                avatar_path=str(avatar),
                broll={},
                storyboard=make_storyboard(),
                output_path=str(tmp_path / "final.mp4"),
                job_dir=str(tmp_path),
                script_text="Hook. Point one.",
            )


def test_validate_mp4_ok():
    with patch.object(compositor, "probe_media", return_value=_final_probe()):
        with patch("os.path.exists", return_value=True), patch(
            "os.path.getsize", return_value=100
        ):
            info = validate_mp4("final.mp4", expected_duration=20.0)
    assert info["resolution"] == "1080x1920"
    assert info["video_codec"] == "h264"
    assert info["audio_codec"] == "aac"


def test_validate_mp4_rejects():
    import tempfile

    with patch.object(
        compositor, "probe_media", return_value=_final_probe(720, 1280)
    ):
        with patch("os.path.exists", return_value=True), patch(
            "os.path.getsize", return_value=100
        ):
            with pytest.raises(Mp4ValidationError, match="1080x1920"):
                validate_mp4("x.mp4")

    no_audio = _final_probe()
    no_audio["streams"] = [no_audio["streams"][0]]
    with patch.object(compositor, "probe_media", return_value=no_audio):
        with patch("os.path.exists", return_value=True), patch(
            "os.path.getsize", return_value=100
        ):
            with pytest.raises(Mp4ValidationError, match="audio"):
                validate_mp4("x.mp4")

    with patch("os.path.exists", return_value=False):
        with pytest.raises(Mp4ValidationError, match="missing"):
            validate_mp4("gone.mp4")

    with tempfile.NamedTemporaryFile(suffix=".mp4") as tmp:
        with pytest.raises(Mp4ValidationError, match="empty"):
            validate_mp4(tmp.name)
