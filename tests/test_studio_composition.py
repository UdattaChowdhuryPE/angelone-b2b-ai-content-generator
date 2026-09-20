"""Studio (green-screen) composition: mocked subprocess, no ffmpeg required.

Covers the future-render path only; legacy baked-background behavior is
pinned by tests/test_compositor.py and must stay unchanged.
"""

import inspect
import os
from unittest.mock import patch

import pytest

from video import compositor
from video.compositor import (
    FINAL_HEIGHT,
    FINAL_WIDTH,
    STUDIO_AVATAR_TOP_PX,
    STUDIO_AVATAR_WIDTH_PX,
    STUDIO_AVATAR_X_PX,
    STUDIO_DESK_FEATHER_PX,
    STUDIO_DESK_HEIGHT_PX,
    STUDIO_DESK_TOP_FRAC,
    STUDIO_DESK_TOP_PX,
    CompositorError,
    build_desk_foreground,
    compose_final,
)
from tests.fakes import make_storyboard


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


def _bg_png(path, size=(270, 480)):
    from PIL import Image

    img = Image.new("RGB", size, (20, 40, 90))
    # Distinct desk-band color so the strip crop is measurable.
    desk = Image.new("RGB", (size[0], size[1] // 3), (200, 120, 40))
    img.paste(desk, (0, size[1] - desk.height))
    img.save(str(path))
    return str(path)


def _run_compose(tmp_path, bg_path=None, avatar_mode="auto", board=None):
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

    def fake_run(cmd, **kwargs):
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

    kwargs = {}
    if bg_path is not None:
        kwargs["studio_background_path"] = bg_path
    with (
        patch.object(compositor, "probe_media", side_effect=fake_probe),
        patch.object(compositor.subprocess, "run", side_effect=fake_run),
        patch.object(compositor, "render_all_cards", side_effect=fake_render),
    ):
        result = compose_final(
            audio_path=str(audio),
            avatar_path=str(avatar),
            broll={},
            storyboard=board,
            output_path=out,
            job_dir=str(tmp_path),
            script_text="Hook. Point one.",
            avatar_mode=avatar_mode,
            **kwargs,
        )
    return result, captured


def test_geometry_config_block_meets_acceptance_targets():
    # Presenter width band 380-490px in the 1080px frame (size frozen).
    assert 380 <= STUDIO_AVATAR_WIDTH_PX <= 490
    # Centered horizontally.
    assert STUDIO_AVATAR_X_PX == (FINAL_WIDTH - STUDIO_AVATAR_WIDTH_PX) // 2
    # Head begins around 24-27% of frame height (reference: seated
    # presenter, shoulders + upper torso visible above the desk).
    assert 455 <= STUDIO_AVATAR_TOP_PX <= 500
    assert 0.24 * FINAL_HEIGHT <= STUDIO_AVATAR_TOP_PX <= 0.27 * FINAL_HEIGHT
    # Desk top begins around 51-53% of frame height (reference tabletop).
    assert 0.51 <= STUDIO_DESK_TOP_FRAC <= 0.532
    assert STUDIO_DESK_TOP_PX == int(round(FINAL_HEIGHT * STUDIO_DESK_TOP_FRAC))
    assert STUDIO_DESK_TOP_PX == 1020
    assert STUDIO_DESK_HEIGHT_PX == FINAL_HEIGHT - STUDIO_DESK_TOP_PX
    # Overlay bottom must extend below the desk line (occlusion overlap)
    # so the lower torso disappears naturally behind the desk.
    bottom = STUDIO_AVATAR_TOP_PX + 781  # STUDIO_AVATAR_HEIGHT_PX
    assert bottom > STUDIO_DESK_TOP_PX
    assert bottom - STUDIO_DESK_TOP_PX >= 200
    assert STUDIO_DESK_FEATHER_PX >= 8


def test_legacy_path_has_no_studio_layers(tmp_path):
    out, captured = _run_compose(tmp_path)
    assert out.endswith("final.mp4")
    joined = " ".join(captured["cmd"])
    assert "chromakey" not in joined
    assert "desk_foreground" not in joined
    assert "split=" not in joined
    assert "crop=1080:1920" in joined  # legacy cover preserved
    assert not (tmp_path / "desk_foreground.png").exists()


def test_legacy_forced_even_with_bg(tmp_path):
    bg = _bg_png(tmp_path / "bg.png")
    out, captured = _run_compose(tmp_path, bg_path=bg, avatar_mode="legacy")
    joined = " ".join(captured["cmd"])
    assert "chromakey" not in joined
    assert "desk_foreground" not in joined


def test_studio_path_graph_order_and_inputs(tmp_path):
    bg = _bg_png(tmp_path / "bg.png")
    out, captured = _run_compose(tmp_path, bg_path=bg, avatar_mode="green")
    assert out.endswith("final.mp4")
    cmd = captured["cmd"]
    joined = " ".join(cmd)
    # Keyed presenter at configured geometry.
    assert "chromakey=0x00FF00" in joined
    # Despill immediately after chromakey, before yuva420p (fringe cleanup;
    # exact chain order is pinned by test_key_chain_order_and_frozen_key_values).
    assert "despill=type=green:mix=0.5" in joined
    assert joined.index("chromakey") < joined.index("despill")
    assert f"scale={STUDIO_AVATAR_WIDTH_PX}:" in joined
    assert f"overlay={STUDIO_AVATAR_X_PX}:{STUDIO_AVATAR_TOP_PX}" in joined
    # Desk foreground after avatar, before captions.
    assert f"overlay=0:{STUDIO_DESK_TOP_PX}" in joined
    assert "desk_foreground.png" in joined
    ak = joined.index("chromakey")
    desk = joined.index(f"overlay=0:{STUDIO_DESK_TOP_PX}")
    cap = joined.index("overlay=0:0:enable='between(t,")
    assert ak < desk < cap
    # BG + desk are looped image inputs; avatar audio still mapped.
    assert "-loop" in cmd
    assert "0:a" in cmd
    assert "+faststart" in joined and "libx264" in joined
    # Desk foreground artifact built with feathered alpha.
    desk_path = tmp_path / "desk_foreground.png"
    assert desk_path.exists()
    from PIL import Image

    with Image.open(desk_path) as d:
        assert d.size == (FINAL_WIDTH, STUDIO_DESK_HEIGHT_PX)
        assert d.mode == "RGBA"
        top_alpha = d.load()[d.width // 2, 0][3]
        bottom_alpha = d.load()[d.width // 2, d.height - 1][3]
        assert top_alpha < 255  # feathered edge, no hard rectangle
        assert bottom_alpha == 255


def test_key_chain_order_and_frozen_key_values():
    """Pin the approved tuning: key values frozen, despill after key."""
    from video.compositor import (
        STUDIO_CHROMAKEY_BLEND,
        STUDIO_CHROMAKEY_SIMILARITY,
        STUDIO_DESPILL,
        _studio_chromakey_filter,
    )

    assert (STUDIO_CHROMAKEY_SIMILARITY, STUDIO_CHROMAKEY_BLEND) == (0.12, 0.15)
    assert STUDIO_DESPILL == "despill=type=green:mix=0.5"
    chain = _studio_chromakey_filter()
    assert chain == (
        "fps=30,scale=439:781,"
        "chromakey=0x00FF00:0.12:0.15,"
        "despill=type=green:mix=0.5,"
        "format=yuva420p"
    )


def test_studio_green_mode_requires_bg(tmp_path):
    with pytest.raises(CompositorError, match="studio_background_path"):
        _run_compose(tmp_path, bg_path=None, avatar_mode="green")
    with pytest.raises(CompositorError, match="studio_background_path"):
        _run_compose(
            tmp_path,
            bg_path=str(tmp_path / "missing.png"),
            avatar_mode="green",
        )


def test_avatar_mode_invalid_rejected(tmp_path):
    with pytest.raises(CompositorError, match="avatar_mode"):
        _run_compose(tmp_path, avatar_mode="bogus")


def test_desk_foreground_aligns_and_preserves_source(tmp_path):
    bg = _bg_png(tmp_path / "bg.png", size=(540, 960))
    with open(bg, "rb") as f:
        before = f.read()
    out = str(tmp_path / "desk.png")
    assert build_desk_foreground(bg, out) == out
    with open(bg, "rb") as f:
        assert f.read() == before  # canonical read-only
    from PIL import Image

    with Image.open(out) as d:
        assert d.size == (FINAL_WIDTH, STUDIO_DESK_HEIGHT_PX)


def test_reference_image_never_used_as_background():
    for mod in ("video.compositor", "providers.heygen"):
        src = inspect.getsource(__import__(mod, fromlist=["x"]))
        assert "reference-image" not in src
        assert "reference_image" not in src
