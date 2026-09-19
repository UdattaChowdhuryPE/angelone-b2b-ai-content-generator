"""Real FFmpeg integration: assemble + validate a captioned 1080x1920 MP4.

Opt-in by availability: skipped when ffmpeg/ffprobe are missing so the
normal unit suite never requires them.
"""

import json
import os
import shutil
import subprocess

import pytest

ffmpeg_available = shutil.which("ffmpeg") is not None and shutil.which(
    "ffprobe"
) is not None

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not ffmpeg_available, reason="ffmpeg/ffprobe not available"
    ),
]


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def _make_avatar(path: str):
    _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=720x1280:rate=30:duration=8",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", path,
        ]
    )


def _make_broll(path: str):
    _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=size=720x1280:rate=30:color=navy:duration=5",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", path,
        ]
    )


def test_real_compose_produces_captioned_1080x1920(tmp_path):
    from pipeline.models import Scene, Storyboard
    from video.compositor import compose_final, probe_media

    avatar = str(tmp_path / "avatar.mp4")
    broll_clip = str(tmp_path / "broll.mp4")
    voice = str(tmp_path / "voice.mp3")
    _make_avatar(avatar)
    _make_broll(broll_clip)
    _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
            "-c:a", "libmp3lame", voice,
        ]
    )

    board = Storyboard(
        scenes=[
            Scene(
                scene_id=1, start=0.0, end=3.0, narration="Hook line here.",
                key_claim="hook", visual_type="avatar", avatar_required=True,
            ),
            Scene(
                scene_id=2, start=3.0, end=8.0, narration="Second point here.",
                key_claim="point", visual_type="footage",
                visual_prompt="clip", broll_required=True,
            ),
        ],
        total_duration=8.0,
    )
    out = str(tmp_path / "final.mp4")
    result = compose_final(
        audio_path=voice,
        avatar_path=avatar,
        broll={2: broll_clip},
        storyboard=board,
        output_path=out,
        job_dir=str(tmp_path),
        script_text="Hook line here. Second point here.",
    )
    assert result == out
    info = probe_media(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert video["codec_name"] == "h264"
    assert audio["codec_name"] == "aac"
    assert (video["width"], video["height"]) == (1080, 1920)
    duration = float(info["format"]["duration"])
    assert abs(duration - 8.0) <= 1.0
    captions = sorted((tmp_path / "captions").glob("*.png"))
    assert len(captions) >= 1
    report = {
        "resolution": f"{video['width']}x{video['height']}",
        "duration": duration,
        "caption_pngs": len(captions),
    }
    print(json.dumps(report))
