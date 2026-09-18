"""V0.4 Higgsfield B-roll smoke experiment (isolated, single 5s clip).

Scope:
- Does NOT touch pipeline/, orchestrator, storyboard models, validators,
  voice, HeyGen/avatar, compositor, app.py, or existing tests.
- Uses ONE frozen visual_prompt verbatim as the sole generation input.
- Narration slice below is metadata/traceability ONLY. It is logged
  locally and NEVER sent to Higgsfield.

Usage:
    uv run python scripts/higgsfield_broll_smoke.py --dry-run   # zero API calls
    uv run python scripts/higgsfield_broll_smoke.py             # ONE paid call, no retries
"""

import argparse
import json
import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

from providers.higgsfield import HiggsfieldProvider
from video.assets import download_file

MODEL = "alibaba/wan-3.0/text-to-video"

# Frozen visual prompt — must be sent verbatim. Do not edit.
FROZEN_VISUAL_PROMPT = (
    "Realistic cinematic vertical video of an Indian retail investor sitting at home "
    "reviewing their long-term investment portfolio on a laptop.\n"
    "The person appears thoughtful and focused, naturally interacting with the laptop "
    "and reviewing investment information.\n"
    "Modern Indian home environment, authentic Indian visual details, premium "
    "financial-content production quality, natural lighting, subtle camera movement, "
    "realistic human motion, documentary-style cinematography.\n"
    "No visible brand logos, no readable text, no specific stock names, no financial numbers, "
    "no charts, no exaggerated wealth imagery, no futuristic UI, no unrealistic holograms.\n"
    "9:16 vertical social-media composition."
)

# Traceability metadata ONLY. Never included in the Higgsfield payload and
# never influences generation. In the full pipeline this would be copied from
# the approved storyboard scene; here it is a fixed placeholder proving the
# linkage without sending narration to the video model.
NARRATION_METADATA = (
    "[storyboard traceability only — example approved narration slice; "
    "not sent to Higgsfield]"
)

OUTPUT_PATH = os.getenv(
    "BROLL_OUTPUT_PATH", "output/higgsfield_broll_5s.mp4"
)
LOG_PATH = os.getenv(
    "BROLL_LOG_PATH", "output/higgsfield_broll_5s.json"
)


def build_payload() -> dict:
    """Exact payload for the single generation. Narration excluded by design."""
    return {
        "prompt": FROZEN_VISUAL_PROMPT,
        "duration": 5,
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "generate_audio": False,
        "enable_thinking": False,
    }


def credentials_present() -> bool:
    """Presence check only — never print or log secret values."""
    return bool(os.getenv("HF_API_KEY_ID") and os.getenv("HF_API_KEY_SECRET"))


def probe(path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def extract_video_url(result: dict) -> str | None:
    """Try common Higgsfield result shapes without assuming one."""
    if not isinstance(result, dict):
        return None
    for key in ("video_url", "url", "download_url", "output_url"):
        value = result.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    video = result.get("video")
    if isinstance(video, dict):
        url = video.get("url")
        if isinstance(url, str) and url.startswith("http"):
            return url
    data = result.get("data")
    if isinstance(data, dict):
        for key in ("video_url", "url", "download_url", "output_url"):
            value = data.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        # Some APIs nest a list of outputs.
        for key in ("outputs", "videos", "results"):
            items = data.get(key)
            if isinstance(items, list) and items and isinstance(items[0], str):
                if items[0].startswith("http"):
                    return items[0]
    return None


def dry_run() -> int:
    payload = build_payload()
    # Guard: narration must never leak into the generation payload.
    assert NARRATION_METADATA not in json.dumps(payload), (
        "narration leaked into Higgsfield payload"
    )
    assert payload["prompt"] == FROZEN_VISUAL_PROMPT
    print("DRY-RUN (no API calls made)")
    print(f"  model: {MODEL}")
    print(f"  payload: {json.dumps(payload, indent=2)}")
    print(f"  output_path: {OUTPUT_PATH}")
    print(f"  log_path: {LOG_PATH}")
    print(f"  credentials_present: {credentials_present()}")
    print("  narration: traceability metadata only, excluded from payload")
    if not credentials_present():
        print("  NOTE: HF credentials missing — real execution cannot proceed.")
    else:
        print("  NOTE: credentials present — real run would make exactly ONE call.")
    return 0


def live_run() -> int:
    if not credentials_present():
        print(
            "ERROR: HF_API_KEY_ID / HF_API_KEY_SECRET are missing. "
            "Real execution cannot proceed.",
            file=sys.stderr,
        )
        return 2
    payload = build_payload()
    provider = HiggsfieldProvider()

    print("Step 1/3: submitting single 5s generation (no retries)...")
    print(f"  model: {MODEL}")
    submitted = provider.submit_video(MODEL, payload)
    request_id = (
        submitted.get("request_id")
        or submitted.get("id")
        or (submitted.get("data") or {}).get("request_id")
        or (submitted.get("data") or {}).get("id")
    )
    if not request_id:
        print(f"FAILED: no request id in submit response: {json.dumps(submitted)[:500]}")
        return 1
    print(f"  request_id: {request_id}")

    print("Step 2/3: polling for completion (single wait, no resubmit)...")
    final = provider.wait_for_result(str(request_id))
    video_url = extract_video_url(final)
    if not video_url:
        print(f"FAILED: no video URL in final response: {json.dumps(final)[:800]}")
        return 1

    print("Step 3/3: downloading MP4...")
    download_file(video_url, OUTPUT_PATH)
    print(f"  downloaded: {OUTPUT_PATH} ({os.path.getsize(OUTPUT_PATH)} bytes)")

    info = probe(OUTPUT_PATH)
    kinds = sorted(s.get("codec_type") for s in info.get("streams", []))
    duration = float(info["format"].get("duration", 0.0))
    vstream = next((s for s in info["streams"] if s.get("codec_type") == "video"), {})
    res = f"{vstream.get('width')}x{vstream.get('height')}"
    print(f"  streams: {kinds} duration: {duration:.2f}s res: {res}")

    log = {
        "model": MODEL,
        "payload": payload,
        "narration_metadata": NARRATION_METADATA,
        "request_id": request_id,
        "submit_response": submitted,
        "final_response": final,
        "output_path": OUTPUT_PATH,
        "output_duration": duration,
        "output_streams": kinds,
        "output_resolution": res,
    }
    os.makedirs(os.path.dirname(LOG_PATH) or ".", exist_ok=True)
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)
    print(f"  log: {LOG_PATH}")

    ok = (
        "video" in kinds
        and abs(duration - 5.0) <= 1.5
        and res == "720x1280"
    )
    print(f"VALIDATION: {'PASS' if ok else 'FAIL (see streams/duration/res above)'}")
    return 0 if ok else 2


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved model/payload without any API calls.",
    )
    args = parser.parse_args()
    if args.dry_run:
        return dry_run()
    return live_run()


if __name__ == "__main__":
    sys.exit(main())
