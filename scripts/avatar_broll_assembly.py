"""V0.5 Avatar + B-roll assembly experiment (isolated, local-only).

Scope:
- Does NOT touch pipeline/, orchestrator, storyboard models, validators,
  voice, HeyGen/Higgsfield providers, compositor.py, app.py, or tests.
- Combines ONE existing HeyGen avatar MP4 + ONE existing silent
  Higgsfield B-roll MP4 into a single coherent MP4.
- Narration = avatar MP4's embedded audio, passed through continuously
  (never re-cut, never re-timed). B-roll contributes video only.
- No paid API calls. Deterministic from explicit CLI inputs.

Usage:
    uv run python scripts/avatar_broll_assembly.py
    uv run python scripts/avatar_broll_assembly.py \
        --avatar output/heygen_personal_avatar_iv_30s.mp4 \
        --broll output/higgsfield_broll_5s.mp4 \
        --broll-start 5.0 --broll-end 10.0 \
        --output output/avatar_broll_v05.mp4
"""

import argparse
import json
import os
import subprocess
import sys

DEFAULT_AVATAR = "output/heygen_personal_avatar_iv_30s.mp4"
DEFAULT_BROLL = "output/higgsfield_broll_5s.mp4"
DEFAULT_START = 5.0
DEFAULT_END = 10.0
DEFAULT_OUTPUT = "output/avatar_broll_v05.mp4"

WIDTH = 1080
HEIGHT = 1920
FPS = 30


def probe(path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def fmt_duration(info: dict) -> float:
    return float(info["format"].get("duration", 0.0))


def norm_chain() -> str:
    # Shared normalization: common fps + 1080x1920 pad + 9:16 DAR.
    return (
        f"fps={FPS},"
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={WIDTH}:{HEIGHT}:(ow-iw)/2:(oh-ih)/2,"
        "format=yuv420p,setdar=9/16"
    )


def build_filter(avatar_dur: float, start: float, end: float) -> str:
    norm = norm_chain()
    parts = []
    labels = []
    if start > 0:
        parts.append(
            f"[0:v]trim=start=0:end={start},setpts=PTS-STARTPTS,{norm}[pre]"
        )
        labels.append("[pre]")
    window = end - start
    parts.append(
        f"[1:v]trim=start=0:end={window},setpts=PTS-STARTPTS,{norm}[mid]"
    )
    labels.append("[mid]")
    if end < avatar_dur - 0.001:
        parts.append(
            f"[0:v]trim=start={end}:end={avatar_dur},setpts=PTS-STARTPTS,{norm}[post]"
        )
        labels.append("[post]")
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vout]")
    return ";".join(parts)


def validate_output(out_path: str, avatar_dur: float) -> tuple[bool, dict]:
    info = probe(out_path)
    streams = info.get("streams", [])
    kinds = sorted(s.get("codec_type") for s in streams)
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    a = next((s for s in streams if s.get("codec_type") == "audio"), {})
    dur = fmt_duration(info)
    v_dur = float(v.get("duration", dur))
    a_dur = float(a.get("duration", dur))
    report = {
        "streams": kinds,
        "video_codec": v.get("codec_name"),
        "audio_codec": a.get("codec_name"),
        "resolution": f"{v.get('width')}x{v.get('height')}",
        "duration": dur,
        "video_stream_duration": v_dur,
        "audio_stream_duration": a_dur,
    }
    ok = (
        "video" in kinds
        and "audio" in kinds
        and v.get("codec_name") == "h264"
        and v.get("width") == WIDTH
        and v.get("height") == HEIGHT
        and abs(dur - avatar_dur) <= 0.30
        and abs(v_dur - a_dur) <= 0.50
    )
    report["pass"] = ok
    return ok, report


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--avatar", default=DEFAULT_AVATAR)
    p.add_argument("--broll", default=DEFAULT_BROLL)
    p.add_argument("--broll-start", type=float, default=DEFAULT_START)
    p.add_argument("--broll-end", type=float, default=DEFAULT_END)
    p.add_argument("--output", default=DEFAULT_OUTPUT)
    args = p.parse_args()

    for path in (args.avatar, args.broll):
        if not os.path.exists(path):
            print(f"ERROR: missing input: {path}", file=sys.stderr)
            return 2

    avatar_info = probe(args.avatar)
    broll_info = probe(args.broll)
    avatar_dur = fmt_duration(avatar_info)
    broll_dur = fmt_duration(broll_info)
    broll_kinds = sorted(s.get("codec_type") for s in broll_info.get("streams", []))
    avatar_kinds = sorted(s.get("codec_type") for s in avatar_info.get("streams", []))
    print(f"avatar: {args.avatar} {avatar_dur:.3f}s {avatar_kinds}")
    print(f"broll:  {args.broll} {broll_dur:.3f}s {broll_kinds} (video-only expected)")
    if "audio" in broll_kinds:
        print("ERROR: broll must be silent (no audio stream).", file=sys.stderr)
        return 2
    if "audio" not in avatar_kinds or "video" not in avatar_kinds:
        print("ERROR: avatar must contain video + audio.", file=sys.stderr)
        return 2

    start, end = args.broll_start, args.broll_end
    window = end - start
    if not (0 <= start < end <= avatar_dur):
        print(
            f"ERROR: window [{start}, {end}] out of avatar range [0, {avatar_dur:.3f}].",
            file=sys.stderr,
        )
        return 2
    if abs(window - broll_dur) > 0.15:
        print(
            f"ERROR: window length {window:.3f}s != broll duration {broll_dur:.3f}s "
            "(V0.5 requires exactly one 5s segment).",
            file=sys.stderr,
        )
        return 2

    filter_graph = build_filter(avatar_dur, start, end)
    cmd = [
        "ffmpeg", "-y",
        "-i", args.avatar,
        "-i", args.broll,
        "-filter_complex", filter_graph,
        "-map", "[vout]", "-map", "0:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        "-shortest",
        args.output,
    ]
    print(f"assembling: avatar[0:{start:.1f}] + broll[0:{window:.1f}] + avatar[{end:.1f}:{avatar_dur:.1f}]")
    print("audio: continuous from avatar input (broll silent, never mapped)")
    subprocess.run(cmd, check=True)

    ok, report = validate_output(args.output, avatar_dur)
    print(f"output: {args.output} ({os.path.getsize(args.output)} bytes)")
    print(f"validation detail: {json.dumps(report, indent=2)}")
    print(f"VALIDATION: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
