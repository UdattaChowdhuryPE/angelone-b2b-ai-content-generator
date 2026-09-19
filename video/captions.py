"""Burned-in caption generation (ported from the proven V0.9 approach).

Cards are verbatim slices of the approved full_script (concatenated they
must equal it), rendered with Pillow as full-frame transparent PNGs
(white Arial Bold on a semi-transparent rounded box, lower-third) and
overlaid in FFmpeg with ``overlay + enable=between(t,start,end)``.

Canvas is ALWAYS 1080x1920 — never the 720x1280 V0.11 experiment size.
"""

import os
import re
import textwrap

from PIL import Image, ImageDraw, ImageFont

WIDTH = 1080
HEIGHT = 1920
FPS = 30
MAX_LINE = 38  # chars per caption line (1-2 lines per card)
MAX_CARD_CHARS = 80
FONT_SIZE = 46
BOTTOM_MARGIN = 220  # lower-third, clear of the face

MACOS_FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
LINUX_FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _load_font(font_path: str | None = None) -> ImageFont.FreeTypeFont:
    candidates = [p for p in (font_path, MACOS_FONT_PATH, LINUX_FONT_PATH) if p]
    for path in candidates:
        if os.path.exists(path):
            return ImageFont.truetype(path, FONT_SIZE)
    raise FileNotFoundError(
        "No caption font found. Tried: "
        + ", ".join(candidates)
        + ". Captions are mandatory — refusing to render without a font."
    )


def split_cards(full_script: str) -> list[str]:
    """Split narration into short verbatim cards (each <=2 wrapped lines)."""
    text = " ".join((full_script or "").split())
    if not text:
        raise ValueError("Cannot build captions from empty script.")
    sentences = re.split(r"(?<=[.!?])\s+", text)
    cards: list[str] = []
    current = ""
    for sentence in sentences:
        candidate = f"{current} {sentence}".strip()
        if len(candidate) <= MAX_CARD_CHARS or not current:
            current = candidate
        else:
            cards.append(current)
            current = sentence
    if current:
        cards.append(current)
    # Enforce the 2-line wrap rule by splitting oversized cards on words.
    final_cards: list[str] = []
    for card in cards:
        lines = textwrap.wrap(card, width=MAX_LINE)
        if len(lines) <= 2:
            final_cards.append(card)
            continue
        words = card.split()
        chunk, chunks = "", []
        for word in words:
            candidate = f"{chunk} {word}".strip()
            if len(candidate) <= MAX_CARD_CHARS and len(
                textwrap.wrap(candidate, width=MAX_LINE)
            ) <= 2:
                chunk = candidate
            else:
                if chunk:
                    chunks.append(chunk)
                chunk = word
        if chunk:
            chunks.append(chunk)
        final_cards.extend(chunks)
    assert " ".join(" ".join(final_cards).split()) == text, (
        "caption cards must equal the approved script verbatim"
    )
    return final_cards


def time_cards(cards: list[str], total_duration: float) -> list[dict]:
    """Allocate caption windows proportional to character count (V0.9 rule).

    Windows tile [0, total_duration] with no gaps; the final end is clamped
    to total_duration. Since storyboard scenes tile the same timeline,
    char-proportional windows inherently respect scene timing.
    """
    if not cards:
        raise ValueError("Cannot time zero caption cards.")
    if total_duration <= 0:
        raise ValueError("total_duration must be positive.")
    total_chars = sum(len(c) for c in cards)
    segments: list[dict] = []
    cursor = 0.0
    for i, card in enumerate(cards):
        duration = total_duration * len(card) / total_chars
        end = total_duration if i == len(cards) - 1 else cursor + duration
        segments.append(
            {"text": card, "start": round(cursor, 3), "end": round(end, 3)}
        )
        cursor = end
    return segments


def render_card(
    text: str,
    path: str,
    font_path: str | None = None,
) -> str:
    """Render one caption card to a full-frame transparent PNG."""
    # Glyph fallback (render-only): Arial Bold has no U+2011 NON-BREAKING
    # HYPHEN, so rasterize the visually-identical ASCII hyphen instead.
    text = text.replace("\u2011", "-")
    font = _load_font(font_path)
    lines = textwrap.wrap(text, width=MAX_LINE)
    if not 1 <= len(lines) <= 2:
        raise ValueError(
            f"card needs re-split ({len(lines)} lines): {text[:60]}"
        )
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    widths, heights = [], []
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font)
        widths.append(box[2] - box[0])
        heights.append(box[3] - box[1])
    line_h = max(heights) + 14
    block_w = max(widths) + 72
    block_h = line_h * len(lines) + 44
    center_x = WIDTH // 2
    bottom = HEIGHT - BOTTOM_MARGIN
    x0, y0 = center_x - block_w // 2, bottom - block_h
    x1, y1 = center_x + block_w // 2, bottom
    draw.rounded_rectangle([x0, y0, x1, y1], radius=28, fill=(0, 0, 0, 150))
    y = y0 + 22
    for line, width in zip(lines, widths):
        draw.text(
            (center_x - width / 2, y),
            line,
            font=font,
            fill=(255, 255, 255, 255),
        )
        y += line_h
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    img.save(path)
    return path


def render_all_cards(
    segments: list[dict],
    cap_dir: str,
    font_path: str | None = None,
) -> list[str]:
    """Render every caption segment; returns PNG paths in segment order."""
    os.makedirs(cap_dir, exist_ok=True)
    paths = []
    for i, segment in enumerate(segments):
        out = os.path.join(cap_dir, f"cap{i:02d}.png")
        render_card(segment["text"], out, font_path=font_path)
        paths.append(out)
    return paths
