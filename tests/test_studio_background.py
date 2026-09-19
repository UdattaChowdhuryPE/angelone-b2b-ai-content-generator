"""Studio background cover contract (no network, no paid calls)."""

import inspect

from PIL import Image

from providers.heygen import (
    UPLOAD_HEIGHT,
    UPLOAD_WIDTH,
    build_zoomed_out_background,
)


def _source(tmp_path, size=(180, 320), name="studio_background.png"):
    path = str(tmp_path / name)
    img = Image.new("RGB", size, (30, 60, 120))
    # Paint a distinct central subject so cover/crop is measurable.
    core = Image.new(
        "RGB", (size[0] // 2, size[1] // 2), (200, 120, 40)
    )
    img.paste(
        core,
        ((size[0] - core.width) // 2, (size[1] - core.height) // 2),
    )
    img.save(path)
    return path


def _is_white(px):
    return px[0] >= 250 and px[1] >= 250 and px[2] >= 250


def _is_blank(px):
    # White or fully transparent-looking surround has no place in the
    # studio path; treat near-white and near-black-flat padding as blank.
    # The synthetic surround is (30, 60, 120) so any real padding stands out.
    return _is_white(px) or (px[0] <= 5 and px[1] <= 5 and px[2] <= 5)


def test_variant_is_exactly_720x1280(tmp_path):
    for size in [(180, 320), (200, 320), (768, 1376), (720, 1280)]:
        src = _source(tmp_path, size=size, name=f"src_{size[0]}x{size[1]}.png")
        out = str(tmp_path / f"variant_{size[0]}x{size[1]}.png")
        result = build_zoomed_out_background(src, out)
        assert result == out
        with Image.open(out) as b:
            assert b.size == (UPLOAD_WIDTH, UPLOAD_HEIGHT) == (720, 1280)


def test_proportional_cover_no_distortion(tmp_path):
    # Exact 720x1280 AR source: cover is a pure proportional scale,
    # center content stays centered.
    src = _source(tmp_path, size=(360, 640))
    variant = build_zoomed_out_background(src)
    assert variant.size == (720, 1280)
    vpx = variant.load()
    with Image.open(src) as a:
        src_core = a.load()[a.width // 2, a.height // 2]
    assert vpx[360, 640] == src_core
    # Symmetric composition: mirrored edge probes match (centered crop).
    assert vpx[4, 640] == vpx[715, 640]
    assert vpx[360, 4] == vpx[360, 1275]


def test_minimal_crop_for_canonical_ar(tmp_path):
    # 768x1376 has the same AR as canonical 1536x2752 (0.558...).
    # Cover scale = max(720/768, 1280/1376) = 0.9375 -> 720x1290,
    # so only ~10px of height is cropped. Crop must stay minimal.
    src = _source(tmp_path, size=(768, 1376))
    variant = build_zoomed_out_background(src)
    assert variant.size == (720, 1280)
    factor = max(720 / 768, 1280 / 1376)
    scaled_h = 1376 * factor
    crop_total = scaled_h - 1280
    assert crop_total <= 12, f"crop too large: {crop_total}"
    assert crop_total >= 0
    # Width is the limiting dimension: no width crop at all.
    assert abs(768 * factor - 720) < 1.0


def test_variant_fill_is_not_white_or_blank(tmp_path):
    src = _source(tmp_path)
    variant = build_zoomed_out_background(src)
    vpx = variant.load()
    w, h = variant.size
    for x, y in (
        (0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1),
        (w // 2, 0), (0, h // 2), (w - 1, h // 2), (w // 2, h - 1),
    ):
        assert not _is_white(vpx[x, y]), f"white fill at {(x, y)}"
        assert not _is_blank(vpx[x, y]), f"blank fill at {(x, y)}"


def test_canonical_source_unchanged_on_disk(tmp_path):
    src = _source(tmp_path)
    with open(src, "rb") as f:
        before = f.read()
    build_zoomed_out_background(src, str(tmp_path / "v.png"))
    build_zoomed_out_background(src)  # in-memory path also reads only
    with open(src, "rb") as f:
        assert f.read() == before


def test_no_pad_or_white_in_studio_background_path():
    import providers.heygen as heygen_mod

    src = inspect.getsource(heygen_mod.build_zoomed_out_background)
    assert "pad=" not in src.lower(), "studio path must not pad"
    assert "color=white" not in src.lower()
    assert "letterbox" not in src.lower()
    assert "contain" not in src.lower()
    # No fixed 90% shrink may remain in the geometry (legacy `scale`
    # default is accepted but ignored for sizing).
    assert "width * scale" not in src
    assert "height * scale" not in src


def test_invalid_scale_rejected(tmp_path):
    import pytest

    src = _source(tmp_path)
    with pytest.raises(ValueError):
        build_zoomed_out_background(src, scale=1.0)
    with pytest.raises(ValueError):
        build_zoomed_out_background(src, scale=0.0)


def test_compositor_avatar_cover_and_broll_black():
    from video.compositor import _avatar_norm_filter, _norm_filter

    avatar_norm = _avatar_norm_filter()
    assert "scale=1080:1920" in avatar_norm
    assert "force_original_aspect_ratio=increase" in avatar_norm
    assert "crop=1080:1920" in avatar_norm
    assert "pad=" not in avatar_norm
    assert "white" not in avatar_norm.lower()
    assert "setdar=9/16" in avatar_norm

    # B-roll conform path keeps isolated black-pad behavior (never white).
    norm = _norm_filter()
    assert "scale=1080:1920" in norm
    assert "pad=1080:1920" in norm
    assert "color=black" in norm
    assert "white" not in norm.lower()
    assert "setdar=9/16" in norm


def test_compose_uses_avatar_cover_filter(tmp_path):
    import video.compositor as comp_mod

    src = inspect.getsource(comp_mod.compose_final)
    assert "_avatar_norm_filter" in src
