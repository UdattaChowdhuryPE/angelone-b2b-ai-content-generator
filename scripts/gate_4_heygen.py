"""Gate 4 — real HeyGen avatar generation only (paid: one avatar render).

Prerequisites:
  - GET /api/preflight passes (Gate 1), including HEYGEN_API_KEY and
    HEYGEN_AVATAR_ID configured.
  - A previously generated valid voice.mp3 (e.g. from Gate 3), passed via
    --audio. This gate NEVER generates voice itself (no ElevenLabs call).
  - Canonical studio background at assets/backgrounds/studio_background.png
    (the ONLY production background; no override is accepted).

This gate calls HeyGen ONLY. It does NOT call OpenAI, ElevenLabs,
Higgsfield, or FFmpeg composition. Single-shot: no automatic retries.

Usage:
  RUN_REAL_HEYGEN_GATE=true uv run python scripts/gate_4_heygen.py \
      --audio output/gates/gate3_voice.mp3 \
      [--output output/gates/gate4_avatar.mp4]

Without the flag the script prints a refusal and exits 2 without any API call.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Make the repository root importable when executed as
# `uv run python scripts/gate_4_heygen.py` (plain `python script.py`
# puts scripts/ — not the repo root — on sys.path).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

from backend.worker import validate_avatar_artifact
from providers.heygen import HeyGenAvatarProvider
from video.compositor import probe_media

FLAG = "RUN_REAL_HEYGEN_GATE"
CANONICAL_BACKGROUND = os.path.join(
    "assets", "backgrounds", "studio_background.png"
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Gate 4: real HeyGen avatar render from an existing "
        "voice artifact and the canonical studio background."
    )
    parser.add_argument(
        "--audio",
        required=True,
        help="Existing valid voice.mp3 (e.g. Gate 3 output). Required.",
    )
    parser.add_argument(
        "--output",
        default="output/gates/gate4_avatar.mp4",
        help="Where to write the generated avatar.mp4.",
    )
    parser.add_argument(
        "--log",
        default="output/gates/gate4_heygen.json",
        help="Where to write the JSON run log.",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if os.getenv(FLAG) != "true":
        print(
            f"REFUSING: set {FLAG}=true to run this paid gate. "
            "No API call was made."
        )
        return 2

    print("stage=avatar operation=heygen.generate")
    for key in ("HEYGEN_API_KEY", "HEYGEN_AVATAR_ID"):
        if not os.getenv(key):
            print(f"FAIL: {key} is missing (value never printed).")
            return 1
    if not os.path.exists(args.audio) or os.path.getsize(args.audio) == 0:
        print(f"FAIL: voice artifact missing or empty: {args.audio}")
        print("  Prepare it first via Gate 3; Gate 4 never generates voice.")
        return 1
    if not os.path.exists(CANONICAL_BACKGROUND):
        print(f"FAIL: canonical background missing: {CANONICAL_BACKGROUND}")
        return 1
    print(f"  audio: {args.audio} ({os.path.getsize(args.audio)} bytes)")
    print(f"  background: {CANONICAL_BACKGROUND} (canonical, no override)")

    # Repo modules are imported at module top (after the sys.path
    # bootstrap) so import failures surface at launch, not mid-gate.
    try:
        provider = HeyGenAvatarProvider()
        if provider.background_image_path != CANONICAL_BACKGROUND and os.getenv(
            "HEYGEN_BACKGROUND_IMAGE"
        ):
            print(
                "FAIL: HEYGEN_BACKGROUND_IMAGE override is set — Gate 4 "
                "requires the canonical studio background. Unset it."
            )
            return 1
        provider.generate(args.audio, args.output)
        validate_avatar_artifact(args.output)
        info = probe_media(args.output)
        video = next(
            s for s in info.get("streams", []) if s.get("codec_type") == "video"
        )
        resolution = f"{video.get('width')}x{video.get('height')}"
    except Exception as exc:
        print(f"FAIL: Gate 4 failed: {type(exc).__name__}: {exc}")
        return 1

    log_parent = os.path.dirname(args.log)
    if log_parent:
        os.makedirs(log_parent, exist_ok=True)
    with open(args.log, "w") as f:
        json.dump(
            {
                "gate": 4,
                "stage": "avatar",
                "operation": "heygen.generate",
                "status": "passed",
                "audio": args.audio,
                "output": args.output,
                "background": CANONICAL_BACKGROUND,
                "source_resolution": resolution,
                "bytes": os.path.getsize(args.output),
            },
            f,
            indent=2,
        )
    print(
        f"PASS: Gate 4 succeeded. Avatar artifact: {args.output} "
        f"(source {resolution}, upscaled to 1080x1920 at composition)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
