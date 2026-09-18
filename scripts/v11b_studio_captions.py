"""V0.11b captions over HeyGen studio avatar (isolated, LOCAL-ONLY, no API calls).

Overlays the exact approved V0.9 caption cards onto
output/v11_heygen_studio_avatar.mp4 (720x1280, 25fps, ~48.696s).

Reuse (verbatim, no rewrites):
- 18 card texts from scripts/v09_studio_captions.py CARDS, asserted equal
  to output/v07L2_script.json full_script AND to
  output/v09_captions_log.json segments text.
- Exact timing windows from output/v09_captions_log.json segments
  (0.0 -> 48.72). Only adaptation: final card end clamped to the V0.11
  source duration (48.696s; 24ms / ~0.6-frame overhang), everything else
  byte-identical.

Visual treatment: V0.9 render_card() geometry scaled x2/3 from 1080x1920
to the 720x1280 canvas (font 46->31, bottom 220->147, padding/radius
scaled; same wrap width, colors, centering, lower-third, U+2011 fallback).

Video path is identity (no scale/pad/resample, -r 25 to match source);
audio is bit-exact (-c:a copy, -map 0:a). No network imports.

Output: output/v11_studio_captions.mp4 + output/v11_studio_captions_log.json.

Usage:
    PYTHONPATH=. uv run python scripts/v11b_studio_captions.py
"""

import json
import os
import subprocess
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFont

SOURCE = "output/v11_heygen_studio_avatar.mp4"
V09_LOG = "output/v09_captions_log.json"
SCRIPT_JSON = "output/v07L2_script.json"
OUTPUT = "output/v11_studio_captions.mp4"
LOG_PATH = "output/v11_studio_captions_log.json"
CAP_DIR = "/tmp/v11b_caps"

WIDTH = 720
HEIGHT = 1280
FPS = 25  # match V0.11 source; no resample drift
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_SIZE = 31  # 46 x 720/1080, rounded
MAX_LINE = 38  # chars per caption line (unchanged from V0.9)
BOTTOM = 147  # 220 x 720/1080, rounded (lower-third, clear of the face)

# Exact V0.9 CARDS (verbatim; asserted against script + v09 log below).
CARDS = [
    "Market volatility can feel unsettling,",
    "so how should investors think about it?",
    "First, know that market volatility is normal,",
    "and short‑term price movements are difficult to predict.",
    "For investors with a long‑term horizon,",
    "it's important to avoid making decisions",
    "purely because of short‑term market movements.",
    "Instead, follow your planned investment approach",
    "rather than reacting emotionally to every market move.",
    "That doesn't mean ignoring change;",
    "it means evaluating choices based on your plan, not on short‑term noise.",
    "The appropriate approach for any investor",
    "depends on their goals, investment horizon, and risk tolerance.",
    "Keep those three factors in mind",
    "when reviewing your portfolio and making decisions.",
    "In short: expect volatility, don't chase short‑term moves,",
    "stick to your plan, and let your goals,",
    "timeline, and tolerance guide you.",
]


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def render_card(text: str, path: str) -> None:
    # Glyph fallback (render-only, card text/log untouched): Arial Bold has
    # no U+2011 NON-BREAKING HYPHEN, so rasterize visually-identical hyphen.
    text = text.replace("‑", "-")
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    lines = textwrap.wrap(text, width=MAX_LINE)
    assert 1 <= len(lines) <= 2, f"card needs re-split ({len(lines)} lines): {text[:60]}"
    img = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    widths, heights = [], []
    for ln in lines:
        bb = d.textbbox((0, 0), ln, font=font)
        widths.append(bb[2] - bb[0])
        heights.append(bb[3] - bb[1])
    # V0.9 metrics scaled x2/3: pad 72->48, line gap 14->9, box pad 44->29,
    # radius 28->19, text top pad 22->15.
    line_h = max(heights) + 9
    block_w = max(widths) + 48
    block_h = line_h * len(lines) + 29
    cx = WIDTH // 2
    bottom = HEIGHT - BOTTOM
    x0, y0 = cx - block_w // 2, bottom - block_h
    x1, y1 = cx + block_w // 2, bottom
    d.rounded_rectangle([x0, y0, x1, y1], radius=19, fill=(0, 0, 0, 150))
    y = y0 + 15
    for ln, w in zip(lines, widths):
        d.text((cx - w / 2, y), ln, font=font, fill=(255, 255, 255, 255))
        y += line_h
    img.save(path)


def main() -> int:
    for path in (SOURCE, V09_LOG, SCRIPT_JSON):
        if not os.path.exists(path):
            print(f"ERROR: missing {path} -- STOPPING, not recreating", file=sys.stderr)
            return 2
    full = json.load(open(SCRIPT_JSON))["full_script"]
    norm_cards = " ".join(CARDS)
    assert " ".join(full.split()) == " ".join(norm_cards.split()), \
        "caption cards do not match approved script verbatim -- STOPPING"
    print("script fidelity: OK (cards == full_script verbatim)")

    v09 = json.load(open(V09_LOG))
    v09_segs = v09["segments"]
    assert len(v09_segs) == 18, f"expected 18 v09 segments, got {len(v09_segs)}"
    for c, s in zip(CARDS, v09_segs):
        assert c == s["text"], f"card text drift vs v09 log: {c[:40]!r} != {s['text'][:40]!r}"
    print("v09 timing reuse: OK (18 texts match v09 log verbatim)")

    src_info = probe(SOURCE)
    src_dur = float(src_info["format"]["duration"])
    src_v = next(s for s in src_info["streams"] if s["codec_type"] == "video")
    src_a = next(s for s in src_info["streams"] if s["codec_type"] == "audio")
    assert (src_v.get("width"), src_v.get("height")) == (WIDTH, HEIGHT), \
        f"source dims changed: {src_v.get('width')}x{src_v.get('height')}"
    print(f"source: {SOURCE} {src_dur:.3f}s "
          f"{src_v.get('width')}x{src_v.get('height')} {src_v.get('r_frame_rate')}")

    # Reuse exact V0.9 windows; clamp only the final end to source duration.
    segs = [{"text": s["text"], "start": s["start"], "end": s["end"]} for s in v09_segs]
    overhang = segs[-1]["end"] - src_dur
    if overhang > 0:
        segs[-1]["end"] = round(src_dur, 3)
        print(f"final card end clamped 48.72 -> {segs[-1]['end']} "
              f"(overhang {overhang * 1000:.0f}ms, all other windows identical)")

    os.makedirs(CAP_DIR, exist_ok=True)
    cap_paths = []
    for i, s in enumerate(segs):
        p = os.path.join(CAP_DIR, f"cap{i:02d}.png")
        render_card(s["text"], p)
        cap_paths.append(p)
    print(f"rendered {len(cap_paths)} caption cards at {WIDTH}x{HEIGHT}")

    # Identity video path + overlay chain; bit-exact audio copy.
    parts = ["[0:v]format=yuv420p[vbase]"]
    cur = "vbase"
    for i, s in enumerate(segs):
        nxt = f"cap{i}"
        parts.append(
            f"[{cur}][{1 + i}:v]overlay=0:0:"
            f"enable='between(t,{s['start']},{s['end']})'[{nxt}]")
        cur = nxt

    cmd = ["ffmpeg", "-y", "-i", SOURCE]
    for p in cap_paths:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t",
                f"{src_dur + 1:.2f}", "-i", p]
    cmd += ["-filter_complex", ";".join(parts),
            "-map", f"[{cur}]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "copy",
            "-movflags", "+faststart", "-shortest", OUTPUT]
    print("overlaying", len(segs), "caption cards (audio copy)...")
    subprocess.run(cmd, check=True)

    info = probe(OUTPUT)
    streams = info.get("streams", [])
    kinds = sorted(s.get("codec_type") for s in streams)
    v = next(s for s in streams if s["codec_type"] == "video")
    a = next(s for s in streams if s["codec_type"] == "audio")
    dur = float(info["format"]["duration"])
    texts_match = all(c == s["text"] for c, s in zip(CARDS, segs))
    report = {
        "source": SOURCE,
        "source_duration": src_dur,
        "source_resolution": f"{src_v.get('width')}x{src_v.get('height')}",
        "source_fps": src_v.get("r_frame_rate"),
        "streams": kinds,
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name"),
        "resolution": f"{v.get('width')}x{v.get('height')}",
        "fps": v.get("r_frame_rate"),
        "pix_fmt": v.get("pix_fmt"),
        "duration": dur,
        "duration_delta_vs_source": round(dur - src_dur, 3),
        "sample_rate": a.get("sample_rate"),
        "audio_channels": a.get("channels"),
        "caption_count": len(segs),
        "texts_match_v09": texts_match,
        "timings_match_v09_except_final_clamp": True,
        "final_clamp_s": round(overhang, 3) if overhang > 0 else 0.0,
        "segments": segs,
        "network_calls": 0,
        "network_note": "stdlib+Pillow+ffmpeg only; no requests/openai/heygen/higgsfield/elevenlabs; no .env load",
    }
    ok = (v.get("codec_name") == "h264" and a.get("codec_name") == "aac"
          and v.get("width") == WIDTH and v.get("height") == HEIGHT
          and abs(dur - src_dur) <= 0.05
          and len(segs) == 18 and texts_match)
    report["pass"] = ok
    with open(LOG_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps({k: report[k] for k in
                      ("streams", "video_codec", "audio_codec",
                       "resolution", "fps", "duration",
                       "duration_delta_vs_source", "caption_count",
                       "texts_match_v09", "pass")}, indent=2))
    print(f"CAPTION VALIDATION: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
