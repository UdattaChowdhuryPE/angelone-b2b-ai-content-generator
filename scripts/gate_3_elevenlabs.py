"""Gate 3 — real ElevenLabs voice generation only (paid: one TTS call).

Prerequisites:
  - GET /api/preflight passes (Gate 1), including ELEVENLABS_API_KEY and
    ELEVENLABS_VOICE_ID configured.
  - A validated script artifact (e.g. from Gate 2) OR the built-in frozen
    fixture text. This gate NEVER generates a script itself.

This gate calls ElevenLabs ONLY. It does NOT call OpenAI, HeyGen,
Higgsfield, or FFmpeg composition. Single-shot: no automatic retries.

Usage:
  RUN_REAL_ELEVENLABS_GATE=true uv run python scripts/gate_3_elevenlabs.py \
      [--script-json output/gates/gate2_script.json] \
      [--output output/gates/gate3_voice.mp3]

Without the flag the script prints a refusal and exits 2 without any API call.
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Make the repository root importable when executed as
# `uv run python scripts/gate_3_elevenlabs.py` (plain `python script.py`
# puts scripts/ — not the repo root — on sys.path).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from dotenv import load_dotenv

load_dotenv()

from backend.worker import validate_voice_artifact
from providers.voice import ElevenLabsVoiceProvider

FLAG = "RUN_REAL_ELEVENLABS_GATE"

# Deterministic fallback narration (used only when --script-json is absent).
FIXTURE_TEXT = (
    "Market volatility is normal. Short-term price movements are difficult "
    "to predict. Investors with a long-term horizon should avoid decisions "
    "based purely on short-term movements and follow their planned "
    "investment approach."
)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Gate 3: real ElevenLabs voice generation only."
    )
    parser.add_argument(
        "--script-json",
        default="output/gates/gate2_script.json",
        help="Validated script artifact from Gate 2 (falls back to a "
        "frozen fixture when the file is absent).",
    )
    parser.add_argument(
        "--output",
        default="output/gates/gate3_voice.mp3",
        help="Where to write the generated voice.mp3.",
    )
    parser.add_argument(
        "--log",
        default="output/gates/gate3_elevenlabs.json",
        help="Where to write the JSON run log.",
    )
    return parser.parse_args(argv)


def _load_text(script_json: str) -> tuple[str, str]:
    if script_json and os.path.exists(script_json):
        with open(script_json) as f:
            data = json.load(f)
        text = (data.get("full_script") or "").strip()
        if not text:
            raise ValueError(
                f"Script artifact has empty full_script: {script_json}"
            )
        return text, script_json
    return FIXTURE_TEXT, "frozen-fixture"


def main(argv=None) -> int:
    args = parse_args(argv)
    if os.getenv(FLAG) != "true":
        print(
            f"REFUSING: set {FLAG}=true to run this paid gate. "
            "No API call was made."
        )
        return 2

    print("stage=voice operation=voice.generate")
    for key in ("ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID"):
        if not os.getenv(key):
            print(f"FAIL: {key} is missing (value never printed).")
            return 1

    # Repo modules are imported at module top (after the sys.path
    # bootstrap) so import failures surface at launch, not mid-gate.
    try:
        text, source = _load_text(args.script_json)
        print(f"  narration source: {source} ({len(text)} chars)")
        provider = ElevenLabsVoiceProvider()
        provider.generate(text, args.output)
        validate_voice_artifact(args.output)
    except Exception as exc:
        print(f"FAIL: Gate 3 failed: {type(exc).__name__}: {exc}")
        return 1

    size = os.path.getsize(args.output)
    log_parent = os.path.dirname(args.log)
    if log_parent:
        os.makedirs(log_parent, exist_ok=True)
    with open(args.log, "w") as f:
        json.dump(
            {
                "gate": 3,
                "stage": "voice",
                "operation": "voice.generate",
                "status": "passed",
                "output": args.output,
                "bytes": size,
                "narration_source": source,
            },
            f,
            indent=2,
        )
    print(f"PASS: Gate 3 succeeded. Voice artifact: {args.output} ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
