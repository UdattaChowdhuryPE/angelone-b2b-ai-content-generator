"""V0.8c zero-cost complete video assembly (isolated, no API calls).

Avatar pre-roll -> B-roll 1 -> Avatar -> B-roll 2 -> Avatar post-roll,
using the exact V0.7 assembly timeline (output/v07b_assembly_timeline.json)
but with output/v08b_avatar_crop.mp4 as the avatar source.

B-roll windows (frozen from V0.7):
  - B-roll 1 (output/v07_broll_s3.mp4): 12.05s-17.05s
  - B-roll 2 (output/v07_broll_s5.mp4): 24.65s-29.65s

Audio: continuous from avatar input (broll never mapped). No trim,
time-stretch, or retime of the narration. Re-encoded AAC 192k/48kHz
for container/assembly compatibility.

All segments normalized: 1080x1920, 30fps, H.264, yuv420p. Hard cuts.

Usage:
    PYTHONPATH=. uv run python scripts/v08c_avatar_broll_assembly.py
"""

import json
import os
import subprocess
import sys

TIMELINE = "output/v07b_assembly_timeline.json"
AVATAR = "output/v08b_avatar_crop.mp4"
OUTPUT = "output/v08c_avatar_broll_assembly.mp4"

WIDTH = 1080
HEIGHT = 1920
FPS = 30


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


def main() -> int:
    for path in (TIMELINE, AVATAR):
        if not os.path.exists(path):
            print(f"ERROR: missing {path}", file=sys.stderr)
            return 2
    tl = json.load(open(TIMELINE))
    wins = tl["windows"]
    avatar_dur = float(probe(AVATAR)["format"]["duration"])
    print(f"avatar: {AVATAR} {avatar_dur:.3f}s")

    clips = sorted({w["clip"]: w for w in wins if w["kind"] == "BROLL"}.items())
    for clip, _ in clips:
        if not os.path.exists(clip):
            print(f"ERROR: missing {clip}", file=sys.stderr)
            return 2
        info = probe(clip)
        kinds = sorted(s.get("codec_type") for s in info["streams"])
        dur = float(info["format"]["duration"])
        print(f"broll: {clip} {dur:.3f}s {kinds}")
        if "audio" in kinds:
            print(f"ERROR: {clip} must be silent.", file=sys.stderr)
            return 2
        if abs(dur - 5.0) > 0.15:
            print(f"ERROR: {clip} != 5.0s.", file=sys.stderr)
            return 2

    clip_index = {}
    for i, (clip, _) in enumerate(clips):
        clip_index[clip] = i + 1
    n = norm()
    parts, labels = [], []
    seg = 0
    for w in wins:
        s, e = w["start"], w["end"]
        if w["kind"] == "AVATAR":
            parts.append(
                f"[0:v]trim=start={s}:end={e},setpts=PTS-STARTPTS,{n}[s{seg}]"
            )
        else:
            ci = clip_index[w["clip"]]
            parts.append(
                f"[{ci}:v]trim=start=0:end={e - s},"
                f"setpts=PTS-STARTPTS,{n}[s{seg}]"
            )
        labels.append(f"[s{seg}]")
        seg += 1
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vout]")

    cmd = ["ffmpeg", "-y", "-i", AVATAR]
    for clip, _ in clips:
        cmd += ["-i", clip]
    cmd += ["-filter_complex", ";".join(parts),
            "-map", "[vout]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", "-shortest", OUTPUT]
    print("assembling", len(wins), "segments;",
          "+".join(f"{w['start']}-{w['end']}{'B' if w['kind'] == 'BROLL' else ''}"
                   for w in wins))
    print("audio: continuous from avatar (broll never mapped)")
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
        "dar": v.get("display_aspect_ratio"),
        "fps": v.get("r_frame_rate"),
        "pix_fmt": v.get("pix_fmt"),
        "duration": dur,
        "avatar_duration": avatar_dur,
        "sample_rate": a.get("sample_rate"),
        "channels": a.get("channels"),
    }
    ok = (v.get("codec_name") == "h264" and a.get("codec_name") == "aac"
          and v.get("width") == WIDTH and v.get("height") == HEIGHT
          and abs(dur - avatar_dur) <= 0.30)
    report["pass"] = ok
    print(json.dumps(report, indent=2))
    print(f"ASSEMBLY VALIDATION: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
