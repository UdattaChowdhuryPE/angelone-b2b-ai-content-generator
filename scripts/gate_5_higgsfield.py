"""Gate 5 — real Higgsfield B-roll generation only (paid: exactly ONE clip).

Prerequisites:
  - GET /api/preflight passes (Gate 1), including HF_API_KEY_ID and
    HF_API_KEY_SECRET configured.
  - Nothing else is needed: the storyboard scene below is a fixed,
    deterministic single-scene fixture. Exactly ONE paid generation is
    issued — never more. No automatic retries.

This gate calls Higgsfield ONLY. It does NOT call OpenAI, ElevenLabs,
HeyGen, or FFmpeg composition. Narration is NEVER sent to the video
model — the visual_prompt alone is the generation input.

Usage:
  RUN_REAL_HIGGSFIELD_GATE=true uv run python scripts/gate_5_higgsfield.py \
      [--output-dir output/gates/gate5]

The output directory receives the full artifact set Gate 6 consumes:
  <output-dir>/storyboard.json      the frozen single-scene fixture
  <output-dir>/broll/scene_1.mp4    the generated clip (+ metadata.json)

Chain into Gate 6 with:
  --broll-dir output/gates/gate5 --storyboard-json output/gates/gate5/storyboard.json

Without the flag the script prints a refusal and exits 2 without any API call.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Make the repository root importable when executed as
# `uv run python scripts/gate_5_higgsfield.py` (plain `python script.py`
# puts scripts/ — not the repo root — on sys.path).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

from backend.worker import validate_broll_artifact
from pipeline.models import Scene, Storyboard

# NOTE: module-style access (higgsfield.HiggsfieldProvider) so tests can
# substitute the provider without touching paid code paths.
from providers import higgsfield

FLAG = "RUN_REAL_HIGGSFIELD_GATE"

# Frozen single-scene fixture — exactly one paid clip. Do not add scenes.
FROZEN_VISUAL_PROMPT = (
    "Realistic cinematic vertical video of an Indian retail investor sitting "
    "at home reviewing a long-term investment portfolio on a laptop, "
    "thoughtful and focused. Modern Indian home, natural lighting, subtle "
    "camera movement, documentary-style cinematography. No visible brand "
    "logos, no readable text, no stock names, no numbers, no charts. "
    "9:16 vertical social-media composition."
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Gate 5: exactly one real Higgsfield B-roll clip from "
        "a frozen single-scene fixture."
    )
    parser.add_argument(
        "--output-dir",
        default="output/gates/gate5",
        help="Job-style directory receiving broll/scene_1.mp4 + metadata.",
    )
    parser.add_argument(
        "--log",
        default="output/gates/gate5_higgsfield.json",
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

    print("stage=broll operation=higgsfield.fetch")
    for key in ("HF_API_KEY_ID", "HF_API_KEY_SECRET"):
        if not os.getenv(key):
            print(f"FAIL: {key} is missing (value never printed).")
            return 1

    # Repo modules are imported at module top (after the sys.path
    # bootstrap) so import failures surface at launch, not mid-gate.
    storyboard = Storyboard(
        scenes=[
            Scene(
                scene_id=1,
                start=0.0,
                end=5.0,
                narration=(
                    "[traceability only — approved narration slice; "
                    "never sent to Higgsfield]"
                ),
                key_claim="B-roll validation scene",
                visual_type="clip",
                visual_prompt=FROZEN_VISUAL_PROMPT,
                broll_required=True,
            )
        ],
        total_duration=5.0,
    )
    # Persist the fixture storyboard alongside the B-roll output (purely
    # local write, before any paid call) so Gate 6's explicit
    # --storyboard-json has a concrete artifact to point at, and so the
    # attempted input survives as evidence even if fetch fails.
    os.makedirs(args.output_dir, exist_ok=True)
    storyboard_path = os.path.join(args.output_dir, "storyboard.json")
    with open(storyboard_path, "w") as f:
        json.dump(storyboard.model_dump(), f, indent=2)
    print(f"  storyboard: {storyboard_path} (fixture, 1 scene)")

    try:
        provider = higgsfield.HiggsfieldProvider()
        print(
            f"  model: {higgsfield.BROLL_MODEL} "
            "(exactly one generation, no retry)"
        )
        clips = provider.fetch(storyboard, args.output_dir)
        if len(clips) != 1:
            raise RuntimeError(
                f"Gate 5 must produce exactly one clip, got {len(clips)}"
            )
        validate_broll_artifact(clips, storyboard)
    except Exception as exc:
        print(f"FAIL: Gate 5 failed: {type(exc).__name__}: {exc}")
        return 1

    clip_path = clips[1]
    log_parent = os.path.dirname(args.log)
    if log_parent:
        os.makedirs(log_parent, exist_ok=True)
    with open(args.log, "w") as f:
        json.dump(
            {
                "gate": 5,
                "stage": "broll",
                "operation": "higgsfield.fetch",
                "status": "passed",
                "model": higgsfield.BROLL_MODEL,
                "generations": 1,
                "storyboard": storyboard_path,
                "clip": clip_path,
                "bytes": os.path.getsize(clip_path),
            },
            f,
            indent=2,
        )
    print(
        f"PASS: Gate 5 succeeded. Storyboard: {storyboard_path}. "
        f"B-roll artifact: {clip_path}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
