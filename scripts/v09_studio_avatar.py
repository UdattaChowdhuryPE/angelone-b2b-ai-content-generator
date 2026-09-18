"""V0.9 studio-background avatar render (isolated, EXACTLY ONE paid render).

Single-variable test vs V0.8d: V0.8d-identical Avatar IV payload
(same avatar_id, same audio output/v07L2_voice.mp3, 9:16, 720p,
engine avatar_iv, fit cover) + ONLY "background": {"type": "image",
"asset_id": <uploaded output/v09_studio_bg.png>}.

Gate 0 (passed): docs confirm CreateVideoFromAvatar.background
{color|image} for Avatar IV; /v3/assets image upload verified free.
No production-code edits.

Usage:
    PYTHONPATH=. uv run python scripts/v09_studio_avatar.py
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
BG_PATH = "output/v09_studio_bg.png"
OUTPUT_PATH = "output/v09_avatar_studio.mp4"
LOG_PATH = "output/v09_studio_log.json"


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def main() -> int:
    provider = HeyGenAvatarProvider()
    print("avatar_id: <redacted> engine: avatar_iv fit: cover + image background")

    audio_info = probe(AUDIO_PATH)
    audio_dur = float(audio_info["format"]["duration"])
    print(f"audio: {AUDIO_PATH} {audio_dur:.2f}s")

    print("uploading audio (free)...")
    audio_asset_id = provider.upload_audio(AUDIO_PATH)
    print("  audio asset ok")

    print("uploading studio bg image (free)...")
    with open(BG_PATH, "rb") as f:
        resp = requests.post(
            f"{provider.BASE_URL}/v3/assets",
            headers=provider.headers,
            files={"file": ("v09_studio_bg.png", f, "image/png")},
            timeout=120,
        )
    resp.raise_for_status()
    bg_asset_id = resp.json()["data"]["asset_id"]
    print("  bg asset ok")

    # V0.8d-identical payload + exactly one added field.
    payload = {
        "type": "avatar",
        "avatar_id": provider.avatar_id,
        "audio_asset_id": audio_asset_id,
        "title": "V0.9 studio 49s",
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "engine": {"type": "avatar_iv"},
        "fit": "cover",
        "background": {"type": "image", "asset_id": bg_asset_id},
    }
    print("payload keys:", sorted(payload))
    assert set(payload) == {"type", "avatar_id", "audio_asset_id", "title",
                            "aspect_ratio", "resolution", "engine",
                            "fit", "background"}, "payload shape drifted"

    print("creating video (ONE paid render)...")
    t0 = datetime.datetime.now(datetime.timezone.utc).isoformat()
    create = requests.post(
        f"{provider.BASE_URL}/v3/videos",
        headers={**provider.headers, "Content-Type": "application/json"},
        json=payload,
        timeout=60,
    )
    create.raise_for_status()
    body = create.json()
    video_id = body.get("data", {}).get("video_id")
    if not video_id:
        print(f"FAILED: no video_id: {create.text[:300]}")
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
            "intervention": {"fit": "cover",
                             "background": {"type": "image",
                                            "source": BG_PATH}},
            "avatar_id": "<redacted>",
            "engine": "avatar_iv",
            "audio_path": AUDIO_PATH,
            "audio_duration": audio_dur,
            "audio_asset_id": "<redacted>",
            "bg_asset_id": "<redacted>",
            "video_id": video_id,
            "t0": t0, "t1": t1,
            "create_response_status": body.get("data", {}).get("status"),
            "final_status": status,
            "final_duration": final.get("data", {}).get("duration"),
            "final_usage": final.get("data", {}).get("usage"),
            "subtitle_url": final.get("data", {}).get("subtitle_url"),
            "captioned_video_url": final.get("data", {}).get("captioned_video_url"),
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
