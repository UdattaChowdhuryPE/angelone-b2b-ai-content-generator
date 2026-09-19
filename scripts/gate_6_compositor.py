"""Gate 6 — real local FFmpeg composition only (NO paid provider calls).

Prerequisites (all explicit — there are NO defaults for inputs):
  - --voice: an existing valid voice.mp3 (e.g. Gate 3 output).
  - --avatar: an existing valid avatar.mp4 (e.g. Gate 4 output).
  - --broll-dir: a directory holding broll/scene_<id>.mp4 clips
    (e.g. Gate 5 output directory).
  - --storyboard-json: the storyboard JSON the B-roll was generated for.
    When chaining Gate 5, pass output/gates/gate5/storyboard.json
    (persisted by Gate 5; for real videos use the job's storyboard.json).
  - ffmpeg + ffprobe on PATH; a caption font available (see preflight).

Every composition run requires conscious artifact selection: stale
artifacts are never picked up implicitly. This gate imports no provider
module and makes no network calls. The final must validate as exactly
1080x1920 H.264/AAC 9:16 with captions covering the timeline.

Usage:
  RUN_REAL_COMPOSITOR_GATE=true uv run python scripts/gate_6_compositor.py \
      --voice output/gates/gate3_voice.mp3 \
      --avatar output/gates/gate4_avatar.mp4 \
      --broll-dir output/gates/gate5 \
      --storyboard-json output/gates/gate5/storyboard.json \
      [--output output/gates/gate6_final.mp4]

Without the flag the script prints a refusal and exits 2 without doing anything.
"""

import argparse
import glob
import json
import os
import sys
from pathlib import Path

# Make the repository root importable when executed as
# `uv run python scripts/gate_6_compositor.py` (plain `python script.py`
# puts scripts/ — not the repo root — on sys.path).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

from backend.worker import validate_captions_artifact
from pipeline.models import Storyboard
from video.compositor import compose_final, validate_mp4

FLAG = "RUN_REAL_COMPOSITOR_GATE"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Gate 6: local FFmpeg composition from explicitly "
        "chosen artifacts. All four inputs are required (no defaults)."
    )
    parser.add_argument("--voice", required=True, help="Existing voice.mp3.")
    parser.add_argument("--avatar", required=True, help="Existing avatar.mp4.")
    parser.add_argument(
        "--broll-dir",
        required=True,
        help="Directory holding broll/scene_<id>.mp4 clips.",
    )
    parser.add_argument(
        "--storyboard-json", required=True, help="Storyboard JSON file."
    )
    parser.add_argument(
        "--script-text",
        default="",
        help="Optional explicit narration for captions (defaults to the "
        "concatenated storyboard narrations).",
    )
    parser.add_argument(
        "--output",
        default="output/gates/gate6_final.mp4",
        help="Where to write the validated final.mp4.",
    )
    parser.add_argument(
        "--log",
        default="output/gates/gate6_compositor.json",
        help="Where to write the JSON run log.",
    )
    return parser.parse_args(argv)


def _load_broll_lookup(broll_dir: str) -> dict:
    lookup: dict[int, str] = {}
    for path in sorted(glob.glob(os.path.join(broll_dir, "broll", "scene_*.mp4"))):
        base = os.path.basename(path)
        try:
            scene_id = int(base.replace("scene_", "").replace(".mp4", ""))
        except ValueError:
            continue
        lookup[scene_id] = path
    return lookup


def main(argv=None) -> int:
    args = parse_args(argv)
    if os.getenv(FLAG) != "true":
        print(
            f"REFUSING: set {FLAG}=true to run this gate. "
            "Nothing was executed."
        )
        return 2

    print("stage=composition operation=compositor.compose_final")
    for label, path in (
        ("--voice", args.voice),
        ("--avatar", args.avatar),
        ("--storyboard-json", args.storyboard_json),
    ):
        if not path or not os.path.exists(path):
            print(f"FAIL: {label} artifact missing: {path}")
            return 1
    if not os.path.isdir(args.broll_dir):
        print(f"FAIL: --broll-dir missing: {args.broll_dir}")
        return 1
    print(f"  voice: {args.voice}")
    print(f"  avatar: {args.avatar}")
    print(f"  broll-dir: {args.broll_dir}")
    print(f"  storyboard: {args.storyboard_json}")

    # Repo modules are imported at module top (after the sys.path
    # bootstrap) so import failures surface at launch, not mid-gate.
    try:
        with open(args.storyboard_json) as f:
            storyboard = Storyboard(**json.load(f))
        broll = _load_broll_lookup(args.broll_dir)
        print(f"  b-roll clips selected: {sorted(broll)}")

        job_dir = os.path.dirname(args.output) or "."
        os.makedirs(job_dir, exist_ok=True)
        # Caption validation reads <job_dir>/storyboard.json.
        with open(os.path.join(job_dir, "storyboard.json"), "w") as f:
            json.dump(storyboard.model_dump(), f, indent=2)

        out = compose_final(
            audio_path=args.voice,
            avatar_path=args.avatar,
            broll=broll,
            storyboard=storyboard,
            output_path=args.output,
            job_dir=job_dir,
            script_text=args.script_text or None,
        )
        info = validate_mp4(out)
        script_text = (args.script_text or "").strip() or " ".join(
            (s.narration or "").strip() for s in storyboard.scenes
        ).strip()
        validate_captions_artifact(job_dir, script_text)
    except Exception as exc:
        print(f"FAIL: Gate 6 failed: {type(exc).__name__}: {exc}")
        return 1

    if info.get("resolution") != "1080x1920":
        print(f"FAIL: unexpected resolution: {info.get('resolution')}")
        return 1
    if info.get("video_codec") != "h264" or info.get("audio_codec") != "aac":
        print(
            "FAIL: unexpected codecs: "
            f"{info.get('video_codec')}/{info.get('audio_codec')}"
        )
        return 1

    log_parent = os.path.dirname(args.log)
    if log_parent:
        os.makedirs(log_parent, exist_ok=True)
    with open(args.log, "w") as f:
        json.dump(
            {
                "gate": 6,
                "stage": "composition",
                "operation": "compositor.compose_final",
                "status": "passed",
                "output": out,
                "resolution": info.get("resolution"),
                "video_codec": info.get("video_codec"),
                "audio_codec": info.get("audio_codec"),
                "duration": info.get("duration"),
                "paid_calls": 0,
            },
            f,
            indent=2,
        )
    print(
        f"PASS: Gate 6 succeeded. Final: {out} "
        f"({info.get('resolution')} {info.get('video_codec')}/"
        f"{info.get('audio_codec')})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
