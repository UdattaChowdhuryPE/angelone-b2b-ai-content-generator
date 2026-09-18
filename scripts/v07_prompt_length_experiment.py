"""V0.7 isolated prompt-length experiment (caller-side only).

Goal: determine whether explicit duration mechanics can reliably produce a
45-60s VALID script from the SAME four expert key points.

Scope / constraints:
- Does NOT modify pipeline/prompts.py or providers/llm.py (verified: this
  file only imports them).
- Uses the same model + same SCRIPT_SYSTEM_PROMPT + same topic/key points
  as the production path. The ONLY changed variable is an appended caller-side
  duration-mechanics block in the user message.
- No HeyGen / Higgsfield calls. No storyboard generation.
- No new expert financial points, statistics, securities, predictions,
  recommendations, or other substantive financial claims.
- Max 3 candidates. Acceptance criterion = measured ElevenLabs TTS duration
  in [45, 60]s AND script validation valid:true. Word count (125-140) is a
  targeting guide, not a hard gate.
- Stops immediately when one valid candidate passes.

Usage:
    uv run python scripts/v07_prompt_length_experiment.py
"""

import json
import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

from pipeline.models import Script, VideoRequest
from pipeline.prompts import SCRIPT_SYSTEM_PROMPT
from providers.llm import LLMProvider
from providers.voice import ElevenLabsVoiceProvider

TOPIC = "How investors should think about market volatility"

KEY_POINTS = """1. Market volatility is normal, and short-term price movements are difficult to predict.
2. Investors with a long-term horizon should avoid making decisions purely because of short-term market movements.
3. Investors should follow their planned investment approach rather than reacting emotionally to every market move.
4. The appropriate approach depends on the investor's goals, investment horizon, and risk tolerance."""

# Frozen duration-mechanics block. Must be appended verbatim. Do not edit
# without recording the change in the experiment report.
DURATION_BLOCK = """
DURATION REQUIREMENT (HARD): the spoken narration (the full_script field)
must last 45-60 seconds when read aloud at a natural pace, ideally 50-55
seconds -- approximately 125-140 spoken words.

You may elaborate naturally on the supplied key points using connective and
explanatory language: transitions, plain-language restatement, rhetorical
framing, an opening hook, and a closing takeaway.

You must NOT introduce any new financial substance: no new facts,
statistics, numbers, dates, companies or securities, examples involving
specific securities, events, financial indicators, causes, effects,
predictions, recommendations, or investment opinions beyond the four
supplied key points above.

Before returning, self-check: count the approximate words in full_script;
if below ~125, expand ONLY with allowed connective/explanatory language
until inside roughly 125-140 words. Then return the structured Script.
"""

MAX_CANDIDATES = 3
MIN_SECONDS = 45.0
MAX_SECONDS = 60.0


def build_user_message(request: VideoRequest) -> str:
    # Mirrors the production user message in LLMProvider.generate_script
    # byte-for-byte, then appends ONLY the frozen duration block.
    base = f"""
Topic:

{request.topic}

Key points for the video:

{request.key_message or "No key points provided."}

Language:

{request.language}

Target duration:

{request.duration_seconds} seconds.

Write the complete spoken script.

IMPORTANT:
The key points above are the sole source of truth for the
financial content of the script.

Do not introduce new financial facts, statistics, numbers,
dates, companies, events, causes, effects, recommendations,
or investment opinions that are not contained in the key points.

You may:
- improve the hook
- simplify language
- improve transitions
- reorder the supplied points
- remove repetition
- make the script conversational
- add non-financial connective language

Preserve the meaning of the supplied key points.

Do not provide personalized investment advice.
"""
    return base + "\n" + DURATION_BLOCK


def measure_tts(text: str, out_path: str) -> float:
    ElevenLabsVoiceProvider().generate(text, out_path)
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", out_path],
        check=True, capture_output=True, text=True,
    )
    return float(json.loads(out.stdout)["format"].get("duration", 0.0))


def main() -> int:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        print("ERROR: OPENAI_API_KEY missing.", file=sys.stderr)
        return 2
    request = VideoRequest(
        topic=TOPIC, key_message=KEY_POINTS,
        language="English", duration_seconds=60,
    )
    llm = LLMProvider(api_key=api_key)

    # Guard: system prompt + model identical to production path.
    assert llm.model == "gpt-5-mini-2025-08-07", llm.model

    results = []
    for n in range(1, MAX_CANDIDATES + 1):
        print(f"--- candidate {n}/{MAX_CANDIDATES} ---")
        response = llm.client.responses.parse(
            model=llm.model,
            input=[
                {"role": "system", "content": SCRIPT_SYSTEM_PROMPT},
                {"role": "user", "content": build_user_message(request)},
            ],
            text_format=Script,
        )
        script: Script = response.output_parsed
        words = len(script.full_script.split())
        validation = llm.validate_script(request, script)
        audio_path = f"output/v07L{n}_voice.mp3"
        duration = measure_tts(script.full_script, audio_path)
        print(f"  words: {words}")
        print(f"  valid: {validation.valid} {validation.unsupported_claims}")
        print(f"  tts: {duration:.2f}s ({audio_path})")

        with open(f"output/v07L{n}_script.json", "w") as f:
            f.write(script.model_dump_json(indent=2))
        entry = {
            "candidate": n,
            "words": words,
            "valid": validation.valid,
            "unsupported_claims": validation.unsupported_claims,
            "tts_duration": duration,
            "audio_path": audio_path,
            "script_path": f"output/v07L{n}_script.json",
            "pass": bool(validation.valid
                         and MIN_SECONDS <= duration <= MAX_SECONDS),
        }
        results.append(entry)
        with open("output/v07L_experiment.json", "w") as f:
            json.dump(
                {"duration_block": DURATION_BLOCK, "results": results},
                f, indent=2,
            )
        if entry["pass"]:
            print(f"PASS on candidate {n}. Stopping.")
            print("SCRIPT:")
            print(script.full_script)
            return 0

    print("No candidate passed in 3 attempts (verdict B territory).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
