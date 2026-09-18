"""V0.11c final local assembly: HeyGen studio avatar + B-roll + captions.

LOCAL-ONLY. No API calls. No new asset generation. No prod/provider edits.
No script/narration/voice/B-roll/avatar/background/caption text/timing changes.

Base A/V source: output/v11_heygen_studio_avatar.mp4 (720x1280, 25fps,
~48.696s, native HeyGen studio background, no captions).
B-roll (approved V0.7, video-only, 5s each):
  S3: output/v07_broll_s3.mp4 -> 12.05-17.05
  S5: output/v07_broll_s5.mp4 -> 24.65-29.65
Captions: exact 18 V0.9 cards/timings from output/v09_captions_log.json
(verbatim vs scripts/v09_studio_captions.py CARDS and output/v07L2_script.json
full_script). Final card end clamped to V0.11 duration (as in V0.11b).

Timeline (validated V0.7 assembly, collapsed to 5 segments):
  Avatar 0.00-12.05 / B-roll S3 12.05-17.05 / Avatar 17.05-24.65 /
  B-roll S5 24.65-29.65 / Avatar 29.65-48.696.

Audio: ONLY 0:a from V0.11 source (bit-exact copy). B-roll audio never
mapped. Narration continuous across transitions.

Video: V0.11 avatar passed through without scale/pad/crop except a
technically-necessary conform filter (fps=25 + scale/pad no-op + yuv420p +
setdar) so 25fps avatar and 30fps B-roll concat cleanly. B-roll full-frame
720x1280 in exact windows. Hard cuts, no extra transitions.

Captions: FINAL layer, overlaid after avatar/B-roll assembly with V0.9
render_card() geometry scaled x2/3 to 720x1280 (same as V0.11b). NOT burned
into source before assembly; v11_studio_captions.mp4 is NOT used as base.

Outputs: output/v11c_studio_broll_captions.mp4 +
         output/v11c_studio_broll_captions_log.json.

Usage:
    PYTHONPATH=. uv run python scripts/v11c_studio_broll_captions.py
"""

import json
import os
import subprocess
import sys
import textwrap

from PIL import Image, ImageDraw, ImageFont

SOURCE = "output/v11_heygen_studio_avatar.mp4"
BROLL_S3 = "output/v07_broll_s3.mp4"
BROLL_S5 = "output/v07_broll_s5.mp4"
TIMELINE = "output/v07b_assembly_timeline.json"
V09_LOG = "output/v09_captions_log.json"
SCRIPT_JSON = "output/v07L2_script.json"
OUTPUT = "output/v11c_studio_broll_captions.mp4"
LOG_PATH = "output/v11c_studio_broll_captions_log.json"
CAP_DIR = "/tmp/v11c_caps"

WIDTH = 720
HEIGHT = 1280
FPS = 25  # match V0.11 source; B-roll conformed via fps filter
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"
FONT_SIZE = 31  # 46 x 720/1080 (same as V0.11b / scaled V0.9)
MAX_LINE = 38  # unchanged from V0.9
BOTTOM = 147  # 220 x 720/1080 (same as V0.11b)

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

# Frozen V0.7 B-roll windows (must match timeline file exactly).
BROLL_WINDOWS = [(BROLL_S3, 12.05, 17.05), (BROLL_S5, 24.65, 29.65)]


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def norm() -> str:
    # Technically-necessary conform only: fps match + no-op scale/pad +
    # pix_fmt + DAR. No creative alteration of framing/background.
    return (
        f"fps={FPS},"
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        "format=yuv420p,setdar=9/16"
    )


def render_card(text: str, path: str) -> None:
    # Identical to V0.11b (scaled V0.9): U+2011 fallback is render-only.
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
    for path in (SOURCE, BROLL_S3, BROLL_S5, TIMELINE, V09_LOG, SCRIPT_JSON):
        if not os.path.exists(path):
            print(f"ERROR: missing {path} -- STOPPING, not recreating", file=sys.stderr)
            return 2

    # --- Gate: timeline must contain the exact frozen B-roll windows ---
    tl = json.load(open(TIMELINE))
    tl_broll = sorted(
        (w["clip"], w["start"], w["end"]) for w in tl["windows"] if w["kind"] == "BROLL"
    )
    assert tl_broll == sorted(BROLL_WINDOWS), \
        f"timeline B-roll drift: {tl_broll} != {sorted(BROLL_WINDOWS)}"
    print(f"timeline: OK ({TIMELINE}, B-roll 12.05-17.05 + 24.65-29.65)")

    # --- Gate: caption text fidelity (verbatim, no rewrites) ---
    full = json.load(open(SCRIPT_JSON))["full_script"]
    assert " ".join(full.split()) == " ".join(" ".join(CARDS).split()), \
        "caption cards do not match approved script verbatim -- STOPPING"
    print("script fidelity: OK (cards == full_script verbatim)")

    v09 = json.load(open(V09_LOG))
    v09_segs = v09["segments"]
    assert len(v09_segs) == 18, f"expected 18 v09 segments, got {len(v09_segs)}"
    for c, s in zip(CARDS, v09_segs):
        assert c == s["text"], f"card text drift vs v09 log: {c[:40]!r} != {s['text'][:40]!r}"
    print("v09 reuse: OK (18 texts match v09 log verbatim)")

    # --- Probe source + B-roll ---
    src_info = probe(SOURCE)
    src_dur = float(src_info["format"]["duration"])
    src_v = next(s for s in src_info["streams"] if s["codec_type"] == "video")
    src_a = next(s for s in src_info["streams"] if s["codec_type"] == "audio")
    assert (src_v.get("width"), src_v.get("height")) == (WIDTH, HEIGHT), \
        f"source dims changed: {src_v.get('width')}x{src_v.get('height')}"
    print(f"source: {SOURCE} {src_dur:.3f}s "
          f"{src_v.get('width')}x{src_v.get('height')} {src_v.get('r_frame_rate')} "
          f"{src_v.get('codec_name')}/{src_a.get('codec_name')} "
          f"{src_a.get('sample_rate')}Hz ch{src_a.get('channels')}")

    for clip in (BROLL_S3, BROLL_S5):
        info = probe(clip)
        kinds = sorted(s.get("codec_type") for s in info["streams"])
        dur = float(info["format"]["duration"])
        v = next(s for s in info["streams"] if s["codec_type"] == "video")
        assert "audio" not in kinds, f"{clip} must be silent -- STOPPING"
        assert abs(dur - 5.0) <= 0.15, f"{clip} != 5.0s ({dur}) -- STOPPING"
        assert (v.get("width"), v.get("height")) == (WIDTH, HEIGHT), \
            f"{clip} dims changed -- STOPPING"
        print(f"broll: {clip} {dur:.3f}s {v.get('width')}x{v.get('height')} silent OK")

    # --- Captions: exact V0.9 windows; clamp only final end to source ---
    segs = [{"text": s["text"], "start": s["start"], "end": s["end"]} for s in v09_segs]
    overhang = segs[-1]["end"] - src_dur
    if overhang > 0:
        segs[-1]["end"] = round(src_dur, 3)
        print(f"final card end clamped 48.72 -> {segs[-1]['end']} "
              f"(overhang {overhang * 1000:.0f}ms, all other windows identical)")
    else:
        print("no final clamp needed")

    os.makedirs(CAP_DIR, exist_ok=True)
    cap_paths = []
    for i, s in enumerate(segs):
        p = os.path.join(CAP_DIR, f"cap{i:02d}.png")
        render_card(s["text"], p)
        cap_paths.append(p)
    print(f"rendered {len(cap_paths)} caption cards at {WIDTH}x{HEIGHT}")

    # --- Build 5-segment video base (avatar/B-roll), then captions last ---
    n = norm()
    av_end = round(src_dur, 3)
    parts = [
        f"[0:v]trim=start=0:end=12.05,setpts=PTS-STARTPTS,{n}[s0]",
        f"[1:v]trim=start=0:end=5.0,setpts=PTS-STARTPTS,{n}[s1]",
        f"[0:v]trim=start=17.05:end=24.65,setpts=PTS-STARTPTS,{n}[s2]",
        f"[2:v]trim=start=0:end=5.0,setpts=PTS-STARTPTS,{n}[s3]",
        f"[0:v]trim=start=29.65:end={av_end},setpts=PTS-STARTPTS,{n}[s4]",
        "[s0][s1][s2][s3][s4]concat=n=5:v=1:a=0[vbase]",
    ]
    cur = "vbase"
    for i, s in enumerate(segs):
        nxt = f"cap{i}"
        parts.append(
            f"[{cur}][{3 + i}:v]overlay=0:0:"
            f"enable='between(t,{s['start']},{s['end']})'[{nxt}]")
        cur = nxt

    cmd = ["ffmpeg", "-y", "-i", SOURCE, "-i", BROLL_S3, "-i", BROLL_S5]
    for p in cap_paths:
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t",
                f"{src_dur + 1:.2f}", "-i", p]
    cmd += ["-filter_complex", ";".join(parts),
            "-map", f"[{cur}]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "copy",
            "-movflags", "+faststart", "-shortest", OUTPUT]
    print("assembling avatar/B-roll base +", len(segs), "caption overlays (audio copy)...")
    subprocess.run(cmd, check=True)

    # --- Validate output gates ---
    info = probe(OUTPUT)
    streams = info.get("streams", [])
    kinds = sorted(s.get("codec_type") for s in streams)
    v = next(s for s in streams if s["codec_type"] == "video")
    a = next(s for s in streams if s["codec_type"] == "audio")
    dur = float(info["format"]["duration"])
    texts_match = all(c == s["text"] for c, s in zip(CARDS, segs))
    timings_ok = all(
        s["start"] == o["start"] and (s["end"] == o["end"] or i == len(segs) - 1)
        for i, (s, o) in enumerate(zip(segs, v09_segs))
    )
    report = {
        "source": SOURCE,
        "source_duration": src_dur,
        "source_resolution": f"{src_v.get('width')}x{src_v.get('height')}",
        "source_fps": src_v.get("r_frame_rate"),
        "broll": [
            {"clip": BROLL_S3, "start": 12.05, "end": 17.05},
            {"clip": BROLL_S5, "start": 24.65, "end": 29.65},
        ],
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
        "audio_source": "0:a from v11_heygen_studio_avatar.mp4 (copy, no B-roll audio)",
        "caption_count": len(segs),
        "texts_match_v09": texts_match,
        "timings_match_v09_except_final_clamp": timings_ok,
        "final_clamp_s": round(overhang, 3) if overhang > 0 else 0.0,
        "segments": segs,
        "network_calls": 0,
        "network_note": "stdlib+Pillow+ffmpeg only; no requests/openai/heygen/higgsfield/elevenlabs; no .env load",
    }
    ok = (v.get("codec_name") == "h264" and a.get("codec_name") == "aac"
          and v.get("width") == WIDTH and v.get("height") == HEIGHT
          and v.get("pix_fmt") == "yuv420p"
          and abs(dur - src_dur) <= 0.05
          and len(segs) == 18 and texts_match and timings_ok)
    report["pass"] = ok
    with open(LOG_PATH, "w") as f:
        json.dump(report, f, indent=2)
    print(json.dumps({k: report[k] for k in
                      ("streams", "video_codec", "audio_codec",
                       "resolution", "fps", "pix_fmt", "duration",
                       "duration_delta_vs_source", "sample_rate",
                       "audio_channels", "caption_count",
                       "texts_match_v09",
                       "timings_match_v09_except_final_clamp", "pass")}, indent=2))
    print(f"V0.11c ASSEMBLY VALIDATION: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
