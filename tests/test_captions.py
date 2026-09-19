"""Caption splitting/timing/rendering (no network, no ffmpeg)."""

import os

import pytest

from video import captions
from video.captions import render_card, split_cards, time_cards


def test_split_cards_verbatim_and_bounded():
    script = (
        "Market volatility can feel unsettling, so how should investors "
        "think about it? First, know that market volatility is normal, and "
        "short-term price movements are difficult to predict. Stick to plan."
    )
    cards = split_cards(script)
    assert len(cards) >= 2
    assert " ".join(" ".join(cards).split()) == " ".join(script.split())
    import textwrap

    for card in cards:
        assert len(textwrap.wrap(card, width=captions.MAX_LINE)) <= 2


def test_split_cards_empty_rejected():
    with pytest.raises(ValueError):
        split_cards("   ")


def test_time_cards_tile_duration_proportionally():
    cards = ["Short.", "A much longer caption card here."]
    segs = time_cards(cards, 10.0)
    assert segs[0]["start"] == 0.0
    assert segs[-1]["end"] == 10.0
    assert segs[0]["end"] == segs[1]["start"]
    assert (segs[1]["end"] - segs[1]["start"]) > (
        segs[0]["end"] - segs[0]["start"]
    )
    assert all(s["text"] for s in segs)


def test_time_cards_invalid():
    with pytest.raises(ValueError):
        time_cards([], 10.0)
    with pytest.raises(ValueError):
        time_cards(["x"], 0)


def test_render_card_png_dimensions(tmp_path):
    out = str(tmp_path / "cap00.png")
    render_card("Market volatility can feel unsettling,", out)
    assert os.path.exists(out)
    from PIL import Image

    with Image.open(out) as img:
        assert img.size == (1080, 1920)
        assert img.mode == "RGBA"


def test_render_card_rejects_unwrappable():
    with pytest.raises(ValueError):
        render_card("x " * 200, "/tmp/never_written_cap.png")
