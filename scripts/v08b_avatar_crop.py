"""V0.8b zero-cost avatar center-crop experiment (isolated, no API calls).

Deterministic transform of the existing output/v07_avatar.mp4:
  crop=376:669:172:305 (active picture region) -> direct scale=1080:1920
  (approved: 0.08% AR deviation absorbed as stretch, zero bars).
Audio stream-copied untouched (timeline preserved by construction).

Usage:
    PYTHONPATH=. uv run python scripts/v08b_avatar_crop.py
"""

import json
import subprocess
import sys

SRC = "output/v07_avatar.mp4"
OUT = "output/v08b_avatar_crop.mp4"

# Frozen V0.8b parameters. Do not edit without recording the change.
CROP_W, CROP_H, CROP_X, CROP_Y = 376, 669, 172, 305
WIDTH, HEIGHT, FPS = 1080, 1920, 30


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def main() -> int:
    src = probe(SRC)
    sv = next(s for s in src["streams"] if s["codec_type"] == "video")
    assert int(sv["width"]) == 720 and int(sv["height"]) == 1280, sv
    assert CROP_X + CROP_W <= 720 and CROP_Y + CROP_H <= 1280
    src_dur = float(src["format"]["duration"])
    print(f"source: {SRC} {src_dur:.3f}s 720x1280")

    vf = (f"crop={CROP_W}:{CROP_H}:{CROP_X}:{CROP_Y},"
          f"scale={WIDTH}:{HEIGHT},setsar=1,"
          f"fps={FPS},format=yuv420p")
    cmd = ["ffmpeg", "-y", "-i", SRC,
           "-vf", vf,
           "-c:v", "libx264", "-preset", "medium", "-crf", "20",
           "-pix_fmt", "yuv420p", "-r", str(FPS),
           "-map", "0:v:0", "-map", "0:a:0?",
           "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", "-shortest", OUT]
    print(f"filter: {vf} (audio stream-mapped, never re-timed)")
    subprocess.run(cmd, check=True)

    info = probe(OUT)
    streams = info["streams"]
    kinds = sorted(s.get("codec_type") for s in streams)
    v = next(s for s in streams if s["codec_type"] == "video")
    a = next((s for s in streams if s["codec_type"] == "audio"), {})
    dur = float(info["format"]["duration"])
    vdur = float(v.get("duration", dur))
    adur = float(a.get("duration", dur))
    report = {
        "resolution": f"{v.get('width')}x{v.get('height')}",
        "dar": v.get("display_aspect_ratio"),
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name"),
        "duration": dur,
        "source_duration": src_dur,
        "video_stream_duration": vdur,
        "audio_stream_duration": adur,
        "sample_rate": a.get("sample_rate"),
        "channels": a.get("channels"),
    }
    ok = (v.get("codec_name") == "h264" and a.get("codec_name") == "aac"
          and v.get("width") == WIDTH and v.get("height") == HEIGHT
          and abs(dur - src_dur) <= 0.30 and abs(vdur - adur) <= 0.50)
    report["pass"] = ok
    print(json.dumps(report, indent=2))

    # Bar scan: top/bottom 8 rows and left/right 4 cols mean luminance.
    print("edge-luminance scan (bars would read ~0 or ~255 flat)...")
    out = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-ss", "24", "-i", OUT, "-frames:v", "1", "-f", "rawvideo",
         "-pix_fmt", "gray", "-s", f"{WIDTH}x{HEIGHT}", "-"],
        check=True, capture_output=True)
    import numpy as np
    fr = np.frombuffer(out.stdout, dtype=np.uint8).reshape(HEIGHT, WIDTH)
    edges = {"top": fr[:8, :].mean(), "bottom": fr[-8:, :].mean(),
             "left": fr[:, :4].mean(), "right": fr[:, -4:].mean(),
             "top_std": fr[:8, :].std(), "bottom_std": fr[-8:, :].std()}
    print(json.dumps({k: round(float(x), 1) for k, x in edges.items()}))
    print(f"VALIDATION: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
