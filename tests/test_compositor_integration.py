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


def _make_green_avatar(path: str):
    # Deterministic green-screen fixture: solid green + navy torso block +
    # head block, with audio. No network, no providers.
    _run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", "color=size=720x1280:rate=30:color=0x00FF00:duration=8",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
            "-vf",
            (
                "drawbox=x=180:y=400:w=360:h=640:color=navy:t=fill,"
                "drawbox=x=290:y=220:w=140:h=160:color=sienna:t=fill"
            ),
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", path,
        ]
    )


def _cover_bg_pixels(bg_path: str):
    import math

    from PIL import Image

    img = Image.open(bg_path).convert("RGB")
    w, h = img.size
    factor = max(1080 / w, 1920 / h)
    scaled = img.resize(
        (max(1, math.ceil(w * factor)), max(1, math.ceil(h * factor))),
        Image.LANCZOS,
    )
    left = (scaled.width - 1080) // 2
    top = (scaled.height - 1920) // 2
    return scaled.crop((left, top, left + 1080, top + 1920))


def _close(a, b, tol=36):
    return all(abs(x - y) <= tol for x, y in zip(a[:3], b[:3]))


def test_studio_green_composite_matches_acceptance_targets(tmp_path):
    """Local studio composite: green avatar -> BG + presenter + desk + caps.

    Uses ONLY the canonical production background
    (assets/backgrounds/studio_background.png) — never reference-image.png
    — plus a synthetic green-screen avatar. No paid provider calls.
    """
    import os as _os

    from PIL import Image

    from pipeline.models import Scene, Storyboard
    from video.compositor import (
        STUDIO_AVATAR_WIDTH_PX,
        STUDIO_DESK_TOP_PX,
        compose_final,
        probe_media,
    )

    repo_root = _os.path.join(_os.path.dirname(__file__), "..")
    bg_path = _os.path.abspath(
        _os.path.join(repo_root, "assets", "backgrounds", "studio_background.png")
    )
    assert _os.path.exists(bg_path), f"canonical BG missing: {bg_path}"
    assert 380 <= STUDIO_AVATAR_WIDTH_PX <= 490

    avatar = str(tmp_path / "avatar_green.mp4")
    voice = str(tmp_path / "voice.mp3")
    _make_green_avatar(avatar)
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
                scene_id=1, start=0.0, end=4.0, narration="Hook line here.",
                key_claim="hook", visual_type="avatar", avatar_required=True,
            ),
            Scene(
                scene_id=2, start=4.0, end=8.0, narration="Second point here.",
                key_claim="point", visual_type="avatar", avatar_required=True,
            ),
        ],
        total_duration=8.0,
    )
    out = str(tmp_path / "final_studio.mp4")
    result = compose_final(
        audio_path=voice,
        avatar_path=avatar,
        broll={},
        storyboard=board,
        output_path=out,
        job_dir=str(tmp_path),
        script_text="Hook line here. Second point here.",
        studio_background_path=bg_path,
        avatar_mode="green",
    )
    assert result == out
    info = probe_media(out)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert video["codec_name"] == "h264"
    assert audio["codec_name"] == "aac"
    assert (video["width"], video["height"]) == (1080, 1920)
    assert abs(float(info["format"]["duration"]) - 8.0) <= 1.0
    assert (tmp_path / "desk_foreground.png").exists()

    frame = str(tmp_path / "frame.png")
    _run(
        [
            "ffmpeg", "-y", "-ss", "2", "-i", out,
            "-frames:v", "1", frame,
        ]
    )
    bg = _cover_bg_pixels(bg_path)
    with Image.open(frame).convert("RGB") as fr:
        # 1. Studio visible above presenter (branding zone).
        assert _close(fr.load()[540, 100], bg.load()[540, 100])
        # 2. Studio visible left + right of presenter.
        assert _close(fr.load()[100, 800], bg.load()[100, 800])
        assert _close(fr.load()[980, 800], bg.load()[980, 800])
        # 3. Presenter materially present, centered, smaller than full-bleed
        # (sampled inside the synthetic torso block: source box
        # x=180-540,y=400-1040 mapped through the overlay at 320,475
        # with scale 439/720).
        assert not _close(fr.load()[540, 950], bg.load()[540, 950])
        # 4. Desk foreground occludes lower body (matches canonical desk).
        assert _close(
            fr.load()[540, STUDIO_DESK_TOP_PX + 200],
            bg.load()[540, STUDIO_DESK_TOP_PX + 200],
        )
    captions = sorted((tmp_path / "captions").glob("*.png"))
    assert len(captions) >= 1
    print(json.dumps({
        "resolution": "1080x1920",
        "studio": True,
        "desk_top_px": STUDIO_DESK_TOP_PX,
        "avatar_width_px": STUDIO_AVATAR_WIDTH_PX,
    }))
