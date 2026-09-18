"""V0.8d native HeyGen fit:cover A/B experiment (isolated, ONE paid render).

Single-variable test: V0.7-identical Avatar IV payload + ONLY "fit": "cover".
Field pinned from the official HeyGen CreateVideoFromAvatar schema
(fit enum {contain, cover}; 'cover' scales to fill the frame, may crop edges).

Frozen: avatar_id 3fe5fd255bb04807b3cce413b8b35381, engine avatar_iv,
audio output/v07L2_voice.mp3, 9:16, 720p. No production-code edits.

Usage:
    PYTHONPATH=. uv run python scripts/v08d_heygen_cover.py
"""

import datetime
import json
import subprocess
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

from providers.heygen import HeyGenAvatarProvider

AUDIO_PATH = "output/v07L2_voice.mp3"
OUTPUT_PATH = "output/v08d_avatar_cover.mp4"
LOG_PATH = "output/v08d_cover_log.json"

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
    print(f"avatar_id: {provider.avatar_id} engine: avatar_iv "
          f"fit: {FIT_UNDER_TEST} (credentials present, values redacted)")

    audio_info = probe(AUDIO_PATH)
    audio_dur = float(audio_info["format"]["duration"])
    print(f"audio: {AUDIO_PATH} {audio_dur:.2f}s")

    print("uploading audio (free)...")
    asset_id = provider.upload_audio(AUDIO_PATH)
    print("  asset_id: <redacted> (upload ok)")

    # V0.7-identical payload + exactly one added field.
    payload = {
        "type": "avatar",
        "avatar_id": provider.avatar_id,
        "audio_asset_id": asset_id,
        "title": "V0.7 volatility 49s",
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "engine": {"type": "avatar_iv"},
        "fit": FIT_UNDER_TEST,
    }
    redacted = {**payload, "avatar_id": "<redacted>",
                "audio_asset_id": "<redacted>"}
    print(f"request payload (redacted): {json.dumps(redacted)}")
    assert set(payload) == {"type", "avatar_id", "audio_asset_id", "title",
                            "aspect_ratio", "resolution", "engine", "fit"}, \
        "payload shape drifted from single-variable design"

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
    final = provider.wait_for_result(video_id, poll_interval=15, timeout=1200)
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
            "avatar_id": "<redacted>",
            "engine": "avatar_iv",
            "audio_path": AUDIO_PATH,
            "audio_duration": audio_dur,
            "asset_id": "<redacted>",
            "video_id": video_id,
            "t0": t0, "t1": t1,
            "create_response_status": body.get("data", {}).get("status"),
            "final_status": status,
            "final_duration": final.get("data", {}).get("duration"),
            "final_usage": final.get("data", {}).get("usage"),
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
