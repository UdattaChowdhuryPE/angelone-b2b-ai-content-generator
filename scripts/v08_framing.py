"""V0.8 avatar framing experiment (isolated, ONE paid render).

Tests the single native intervention: top-level `"fit": "cover"` on
POST /v3/videos (field pinned from the official HeyGen OpenAPI
CreateVideoFromAvatar schema; AvatarFit enum {contain, cover}).

Everything else is byte-identical to the V0.7 render: same audio file,
same avatar_id, avatar_iv engine, 9:16, 720p. No provider-file edits --
the payload is built caller-side (the provider has no `fit` parameter).

Usage:
    PYTHONPATH=. uv run python scripts/v08_framing.py
"""

import datetime
import json
import os
import subprocess
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

from providers.heygen import HeyGenAvatarProvider

AUDIO_PATH = "output/v07L2_voice.mp3"
OUTPUT_PATH = "output/v08_avatar_cover.mp4"
LOG_PATH = "output/v08_framing_log.json"

# Single intervention under test. Everything else mirrors V0.7 exactly.
FIT_UNDER_TEST = "cover"


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def main() -> int:
    provider = HeyGenAvatarProvider()
    audio_info = probe(AUDIO_PATH)
    audio_dur = float(audio_info["format"]["duration"])
    print(f"audio: {AUDIO_PATH} {audio_dur:.2f}s")
    print(f"avatar_id: {provider.avatar_id} engine: avatar_iv")

    print("uploading audio (free)...")
    asset_id = provider.upload_audio(AUDIO_PATH)
    print(f"  asset_id: {asset_id}")

    # V0.7-identical payload + exactly one added field.
    payload = {
        "type": "avatar",
        "avatar_id": provider.avatar_id,
        "audio_asset_id": asset_id,
        "title": "V0.8 framing test (fit=cover)",
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "engine": {"type": "avatar_iv"},
        "fit": FIT_UNDER_TEST,
    }
    print(f"creating video with fit={FIT_UNDER_TEST} (ONE paid render)...")
    t0 = datetime.datetime.now(datetime.timezone.utc).isoformat()
    resp = requests.post(
        f"{provider.BASE_URL}/v3/videos",
        headers={**provider.headers, "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    body = resp.json()
    video_id = body.get("data", {}).get("video_id")
    if not video_id:
        print(f"FAILED: no video_id: {resp.text[:300]}")
        return 1
    print(f"  video_id: {video_id} t0={t0}")

    print("polling (free)...")
    final = provider.wait_for_result(video_id, poll_interval=15,
                                     timeout=900)
    t1 = datetime.datetime.now(datetime.timezone.utc).isoformat()
    status = final["data"].get("status")
    url = final["data"].get("video_url")
    print(f"  status: {status} t1={t1} has_url={bool(url)}")
    if not url:
        print(f"FAILED: {json.dumps(final)[:300]}")
        return 1
    provider.download_video(url, OUTPUT_PATH)

    info = probe(OUTPUT_PATH)
    kinds = sorted(s.get("codec_type") for s in info.get("streams", []))
    dur = float(info["format"]["duration"])
    v = next(s for s in info["streams"] if s["codec_type"] == "video")
    res = f"{v.get('width')}x{v.get('height')}"
    print(f"  {OUTPUT_PATH} {dur:.2f}s {res} {kinds}")

    with open(LOG_PATH, "w") as f:
        json.dump({
            "intervention": {"fit": FIT_UNDER_TEST},
            "avatar_id": provider.avatar_id,
            "engine": "avatar_iv",
            "audio_path": AUDIO_PATH,
            "audio_duration": audio_dur,
            "asset_id": asset_id,
            "video_id": video_id,
            "t0": t0, "t1": t1,
            "create_response": body,
            "final_response": final,
            "output_path": OUTPUT_PATH,
            "output_duration": dur,
            "output_resolution": res,
            "output_streams": kinds,
        }, f, indent=2)
    ok = ("video" in kinds and "audio" in kinds
          and abs(dur - audio_dur) <= 3.0 and res == "720x1280")
    print(f"VALIDATION: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
