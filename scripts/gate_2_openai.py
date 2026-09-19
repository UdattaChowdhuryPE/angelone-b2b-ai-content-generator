"""Gate 2 — real OpenAI script generation only (paid: one script + one validation).

Prerequisites:
  - GET /api/preflight passes (Gate 1), including OPENAI_API_KEY configured.
  - Nothing else is needed; this gate tests ONLY the script stage.

This gate does NOT call ElevenLabs, HeyGen, Higgsfield, or FFmpeg, and it
does NOT create a video. Single-shot: no automatic retries.

Usage:
  RUN_REAL_OPENAI_GATE=true uv run python scripts/gate_2_openai.py \
      [--output output/gates/gate2_script.json]

Without the flag the script prints a refusal and exits 2 without any API call.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Make the repository root importable when executed as
# `uv run python scripts/gate_2_openai.py` (plain `python script.py`
# puts scripts/ — not the repo root — on sys.path).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

from backend.worker import validate_script_artifact
from pipeline.models import Script, VideoRequest
from pipeline.orchestrator import VideoPipeline

FLAG = "RUN_REAL_OPENAI_GATE"

FIXED_TOPIC = "Why long-term investing beats reacting to market noise"
FIXED_KEY_MESSAGE = (
    "Market volatility is normal. Short-term price movements are "
    "difficult to predict. Investors with a long-term horizon should "
    "avoid decisions based purely on short-term movements and follow "
    "their planned investment approach."
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Gate 2: real OpenAI script generation + validation only."
    )
    parser.add_argument(
        "--output",
        default="output/gates/gate2_script.json",
        help="Where to persist the validated script artifact.",
    )
    parser.add_argument(
        "--log",
        default="output/gates/gate2_openai.json",
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

    print("stage=script operation=llm.generate_script")
    if not os.getenv("OPENAI_API_KEY"):
        print("FAIL: OPENAI_API_KEY is missing (value never printed).")
        return 1

    # Repo modules are imported at module top (after the sys.path
    # bootstrap) so import failures surface at launch, not mid-gate.
    request = VideoRequest(
        topic=FIXED_TOPIC,
        key_message=FIXED_KEY_MESSAGE,
        language="English",
        duration_seconds=30,
    )
    try:
        pipeline = VideoPipeline()
        script = pipeline.create_script(request)
        print("stage=script_validation operation=llm.validate_script")
        validation = pipeline.validate_script_or_raise(request, script)
        validate_script_artifact(script)
    except Exception as exc:
        print(f"FAIL: Gate 2 failed: {type(exc).__name__}: {exc}")
        return 1

    parent = os.path.dirname(args.output)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(script.model_dump(), f, indent=2)
    # Verify the persisted artifact round-trips.
    with open(args.output) as f:
        reloaded = Script(**json.load(f))
    validate_script_artifact(reloaded)

    log_parent = os.path.dirname(args.log)
    if log_parent:
        os.makedirs(log_parent, exist_ok=True)
    with open(args.log, "w") as f:
        json.dump(
            {
                "gate": 2,
                "stage": "script",
                "operation": "llm.generate_script+validate",
                "status": "passed",
                "output": args.output,
                "validation_valid": bool(getattr(validation, "valid", True)),
                "script_chars": len(script.full_script),
            },
            f,
            indent=2,
        )
    print(f"PASS: Gate 2 succeeded. Script artifact: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
