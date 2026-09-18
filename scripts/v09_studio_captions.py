"""V0.9 captions + final assembly (isolated, zero-cost, no API calls).

Base assembly: identical to V0.8e logic (frozen timeline
output/v07b_assembly_timeline.json, B-roll windows 12.05-17.05 and
24.65-29.65, continuous audio from avatar input, 1080x1920/30fps/H.264,
AAC 192k/48k, hard cuts) but with output/v09_avatar_studio.mp4 as the
avatar source.

Captions: exact V0.7L2 full_script split into short cards (no rewrites),
timing proportional to character count across measured narration
(segment-level timing; no whisper available). Rendered with Pillow
(Arial Bold, white on semi-transparent rounded box, lower-third) to
full-frame transparent PNGs, burned in via ffmpeg overlay+enable.
ffmpeg here has no drawtext/subtitles filter, so PNG overlay is used.

Output: output/v09_studio_captions.mp4 + output/v09_captions_log.json.

Usage:
    PYTHONPATH=. uv run python scripts/v09_studio_captions.py
"""

import json
import os
import subprocess
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFont

TIMELINE = "output/v07b_assembly_timeline.json"
AVATAR = "output/v09_avatar_studio.mp4"
SCRIPT_JSON = "output/v07L2_script.json"
OUTPUT = "output/v09_studio_captions.mp4"
LOG_PATH = "output/v09_captions_log.json"
CAP_DIR = "/tmp/v09_caps"

WIDTH = 1080
HEIGHT = 1920
FPS = 30
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_SIZE = 46
MAX_LINE = 38  # chars per caption line (1-2 lines per card)

# Manual split of the EXACT approved script into caption cards.
# Concatenated (whitespace-normalized) they must equal full_script.
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


def norm() -> str:
    return (
        f"fps={FPS},"
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        "format=yuv420p,setdar=9/16"
    )


def render_card(text: str, path: str) -> None:
    # Glyph fallback (render-only, card text/log untouched): Arial Bold has
    # no U+2011 NON-BREAKING HYPHEN (used 4x in approved script), so it
    # rasterizes as tofu. Substitute visually-identical ASCII hyphen.
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
    line_h = max(heights) + 14
    block_w = max(widths) + 72
    block_h = line_h * len(lines) + 44
    cx = WIDTH // 2
    bottom = HEIGHT - 220  # lower-third, clear of the face
    x0, y0 = cx - block_w // 2, bottom - block_h
    x1, y1 = cx + block_w // 2, bottom
    d.rounded_rectangle([x0, y0, x1, y1], radius=28, fill=(0, 0, 0, 150))
    y = y0 + 22
    for ln, w in zip(lines, widths):
        d.text((cx - w / 2, y), ln, font=font, fill=(255, 255, 255, 255))
        y += line_h
    img.save(path)


def main() -> int:
    for path in (TIMELINE, AVATAR, SCRIPT_JSON):
        if not os.path.exists(path):
            print(f"ERROR: missing {path}", file=sys.stderr)
            return 2
    full = json.load(open(SCRIPT_JSON))["full_script"]
    norm_cards = " ".join(CARDS)
    assert " ".join(full.split()) == " ".join(norm_cards.split()), \
        "caption cards do not match approved script verbatim"
    print("script fidelity: OK (cards == full_script verbatim)")

    tl = json.load(open(TIMELINE))
    wins = tl["windows"]
    total = float(tl["measured_narration"])  # 48.72
    chars = [len(c) for c in CARDS]
    segs, t = [], 0.0
    for i, c in enumerate(CARDS):
        dur = total * chars[i] / sum(chars)
        end = total if i == len(CARDS) - 1 else t + dur
        segs.append({"text": c, "start": round(t, 3), "end": round(end, 3)})
        t = end

    os.makedirs(CAP_DIR, exist_ok=True)
    cap_paths = []
    for i, s in enumerate(segs):
        p = os.path.join(CAP_DIR, f"cap{i:02d}.png")
        render_card(s["text"], p)
        cap_paths.append(p)
    print(f"rendered {len(cap_paths)} caption cards")

    avatar_dur = float(probe(AVATAR)["format"]["duration"])
    print(f"avatar: {AVATAR} {avatar_dur:.3f}s")
    clips = sorted({w["clip"]: w for w in wins if w["kind"] == "BROLL"}.items())
    for clip, _ in clips:
        info = probe(clip)
        kinds = sorted(s.get("codec_type") for s in info["streams"])
        assert "audio" not in kinds, f"{clip} must be silent"
    clip_index = {clip: i + 1 for i, (clip, _) in enumerate(clips)}

    n = norm()
    parts, labels = [], []
    for seg, w in enumerate(wins):
        s, e = w["start"], w["end"]
        if w["kind"] == "AVATAR":
            parts.append(
                f"[0:v]trim=start={s}:end={e},setpts=PTS-STARTPTS,{n}[s{seg}]")
        else:
            ci = clip_index[w["clip"]]
            parts.append(
                f"[{ci}:v]trim=start=0:end={e - s},"
                f"setpts=PTS-STARTPTS,{n}[s{seg}]")
        labels.append(f"[s{seg}]")
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vbase]")

    # caption overlays on the assembled base
    cur = "vbase"
    first_cap = 1 + len(clips)
    for i, s in enumerate(segs):
        nxt = f"cap{i}"
        parts.append(
            f"[{cur}][{first_cap + i}:v]overlay=0:0:"
            f"enable='between(t,{s['start']},{s['end']})'[{nxt}]")
        cur = nxt

    cmd = ["ffmpeg", "-y", "-i", AVATAR]
    for clip, _ in clips:
        cmd += ["-i", clip]
    for p in cap_paths:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t",
                f"{total + 1:.2f}", "-i", p]
    cmd += ["-filter_complex", ";".join(parts),
            "-map", f"[{cur}]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", "-shortest", OUTPUT]
    print("assembling base +", len(segs), "caption overlays...")
    subprocess.run(cmd, check=True)

    info = probe(OUTPUT)
    streams = info.get("streams", [])
    kinds = sorted(s.get("codec_type") for s in streams)
    v = next(s for s in streams if s["codec_type"] == "video")
    a = next(s for s in streams if s["codec_type"] == "audio")
    dur = float(info["format"]["duration"])
    report = {
        "streams": kinds,
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name"),
        "resolution": f"{v.get('width')}x{v.get('height')}",
        "fps": v.get("r_frame_rate"),
        "pix_fmt": v.get("pix_fmt"),
        "duration": dur,
        "avatar_duration": avatar_dur,
        "sample_rate": a.get("sample_rate"),
        "segments": segs,
        "windows": [(w["kind"], w["start"], w["end"]) for w in wins],
    }
    ok = (v.get("codec_name") == "h264" and a.get("codec_name") == "aac"
          and v.get("width") == WIDTH and v.get("height") == HEIGHT
          and abs(dur - avatar_dur) <= 0.30
          and abs(dur - 48.7) <= 0.5)
    report["pass"] = ok
    with open(LOG_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps({k: report[k] for k in
                      ("streams", "video_codec", "audio_codec",
                       "resolution", "duration", "pass")}, indent=2))
    print(f"ASSEMBLY VALIDATION: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
