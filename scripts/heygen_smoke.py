"""V0.3 HeyGen smoke test (real API).

Default: runs the V0.2 voice stage ONCE via VideoPipeline.create_assets(),
consumes the resulting audio artifact AS-IS (no ffmpeg trim),
then drives HeyGen avatar + external-audio workflow.

Optional env overrides (personal-avatar run — no pipeline regen):
    SMOKE_AUDIO_PATH   existing audio file to use as-is (skips TTS/LLM)
    SMOKE_OUTPUT_PATH  output MP4 path
    SMOKE_LOG_PATH     JSON log path
    SMOKE_ENGINE       HeyGen engine type (e.g. avatar_iv, avatar_v, avatar_iii)
    SMOKE_TITLE        video title

Usage:
    uv run python scripts/heygen_smoke.py
"""

import json
import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

from pipeline.models import VideoRequest
from pipeline.orchestrator import VideoPipeline
from providers.heygen import HeyGenAvatarProvider

AUDIO_PATH = os.getenv("SMOKE_AUDIO_PATH", "output/voice.mp3")
OUTPUT_PATH = os.getenv("SMOKE_OUTPUT_PATH", "output/heygen_avatar_9x16.mp4")
LOG_PATH = os.getenv("SMOKE_LOG_PATH", "output/heygen_smoke.json")
ENGINE = os.getenv("SMOKE_ENGINE", "avatar_iii")
TITLE = os.getenv("SMOKE_TITLE", "V0.3 smoke test")


def probe(path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", "-show_streams", path,
    ]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def main() -> int:
    reuse_audio = os.getenv("SMOKE_AUDIO_PATH")
    if reuse_audio:
        # Personal-avatar run: consume the existing artifact unchanged.
        # No LLM / TTS calls, no voice or script changes.
        audio_path = reuse_audio
        print("Step 1/5: reusing existing audio artifact (no regeneration)...")
        print(f"  audio artifact: {audio_path} ({os.path.getsize(audio_path)} bytes)")
    else:
        # Short request so the single TTS artifact is ~30s.
        request = VideoRequest(
            topic="Why SIP discipline matters",
            key_message=(
                "SIPs spread investments over time. "
                "Staying invested through ups and downs matters more "
                "than timing the market."
            ),
            language="English",
            duration_seconds=30,
        )

        print("Step 1/5: generating script + voice artifact (single pipeline run)...")
        pipeline = VideoPipeline()
        result = pipeline.create_assets(request, audio_output_path=AUDIO_PATH)
        audio_path = result["audio_path"]
        print(f"  audio artifact: {audio_path} ({os.path.getsize(audio_path)} bytes)")

    audio_info = probe(audio_path)
    input_duration = float(audio_info["format"].get("duration", 0.0))
    print(f"  input duration: {input_duration:.2f}s (used as-is, no trim)")

    provider = HeyGenAvatarProvider()

    print("Step 2/5: uploading audio to HeyGen...")
    asset_id = provider.upload_audio(audio_path)
    print(f"  asset_id: {asset_id}")

    print("Step 3/5: creating avatar video (9:16)...")
    print(f"  avatar_id: {provider.avatar_id} engine: {ENGINE}")
    created = provider.create_video(
        asset_id, title=TITLE, engine={"type": ENGINE},
    )
    video_id = created["data"]["video_id"]
    print(f"  video_id: {video_id} status={created['data'].get('status')}")

    print("Step 4/5: polling for completion...")
    final = provider.wait_for_result(video_id)
    status = final["data"].get("status")
    video_url = final["data"].get("video_url")
    print(f"  final status: {status}")
    if not video_url:
        print(f"  FAILED: no video_url in final response: {json.dumps(final)[:500]}")
        return 1

    print("Step 5/5: downloading MP4...")
    provider.download_video(video_url, OUTPUT_PATH)
    print(f"  downloaded: {OUTPUT_PATH} ({os.path.getsize(OUTPUT_PATH)} bytes)")

    video_info = probe(OUTPUT_PATH)
    kinds = sorted(s.get("codec_type") for s in video_info.get("streams", []))
    output_duration = float(video_info["format"].get("duration", 0.0))
    print(f"  streams: {kinds}")
    print(f"  output duration: {output_duration:.2f}s")

    log = {
        "avatar_id": provider.avatar_id,
        "engine": ENGINE,
        "video_id": video_id,
        "status": status,
        "create_response": created,
        "final_response": final,
        "input_path": audio_path,
        "input_duration": input_duration,
        "output_path": OUTPUT_PATH,
        "output_duration": output_duration,
        "output_streams": kinds,
    }
    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)
    print(f"  log: {LOG_PATH}")

    ok = (
        os.path.exists(OUTPUT_PATH)
        and os.path.getsize(OUTPUT_PATH) > 0
        and "video" in kinds
        and "audio" in kinds
        and abs(output_duration - input_duration) <= 3.0
    )
    usage = final["data"].get("usage") or final["data"].get("credit") or "none reported by API"
    print("---- REPORT ----")
    print(f"job_id: {video_id}")
    print(f"status: {status}")
    print(f"input: {audio_path} {input_duration:.2f}s")
    print(f"output: {OUTPUT_PATH} {output_duration:.2f}s")
    print(f"usage/cost: {usage}")
    print(f"VALIDATION: {'PASS' if ok else 'FAIL (see durations/streams above)'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
