"""V0.11 HeyGen native studio background (isolated, EXACTLY ONE paid render).

Delta vs V0.9: V0.9 sent background:{type:image} ALONE on the photo_avatar
look 3fe5fd255bb04807b3cce413b8b35381 and the server silently ignored it
(Gate 0c: v09_avatar_studio corners ~250-253 white, identical to v08d).
This experiment adds the only other supported matting switch on the MP4
path: remove_background:true alongside background:{type:image}.

Config: type avatar, avatar_id 3fe5fd255bb04807b3cce413b8b35381,
audio output/v07L2_voice.mp3, bg output/background_image.png,
9:16, 720p, engine avatar_iv, fit cover, output mp4 (default).
No local keying, no B-roll/voice/script changes, no prod-code edits.
One paid render, no retry.

Usage:
    PYTHONPATH=. uv run python scripts/v11_heygen_studio_avatar.py
"""

import datetime
import json
import subprocess
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

from providers.heygen import HeyGenAvatarProvider

AVATAR_ID = "3fe5fd255bb04807b3cce413b8b35381"
AUDIO_PATH = "output/v07L2_voice.mp3"
BG_PATH = "output/background_image.png"
OUTPUT_PATH = "output/v11_heygen_studio_avatar.mp4"
LOG_PATH = "output/v11_heygen_studio_avatar_log.json"


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def main() -> int:
    provider = HeyGenAvatarProvider()
    assert provider.avatar_id == AVATAR_ID, "unexpected HEYGEN_AVATAR_ID"
    print("avatar_id: <redacted> engine: avatar_iv fit: cover + "
          "background:image + remove_background:true")

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
            files={"file": ("background_image.png", f, "image/png")},
            timeout=120,
        )
    resp.raise_for_status()
    bg_asset_id = resp.json()["data"]["asset_id"]
    print("  bg asset ok")

    payload = {
        "type": "avatar",
        "avatar_id": provider.avatar_id,
        "audio_asset_id": audio_asset_id,
        "title": "V0.11 studio 49s",
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "engine": {"type": "avatar_iv"},
        "fit": "cover",
        "background": {"type": "image", "asset_id": bg_asset_id},
        "remove_background": True,
    }
    print("payload keys:", sorted(payload))
    assert set(payload) == {"type", "avatar_id", "audio_asset_id", "title",
                            "aspect_ratio", "resolution", "engine",
                            "fit", "background", "remove_background"}, \
        "payload shape drifted"

    print("creating video (ONE paid render, no retry)...")
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
    a = next(s for s in info["streams"] if s["codec_type"] == "audio")
    res = f"{v.get('width')}x{v.get('height')}"
    print(f"  {OUTPUT_PATH} {dur:.2f}s {res} {kinds}")

    with open(LOG_PATH, "w") as f:
        json.dump({
            "intervention": {
                "fit": "cover",
                "background": {"type": "image", "source": BG_PATH},
                "remove_background": True,
                "delta_vs_v09": "added remove_background:true",
            },
            "avatar_id": "<redacted>",
            "avatar_look_type": "photo_avatar",
            "engine": "avatar_iv",
            "audio_path": AUDIO_PATH,
            "audio_duration": audio_dur,
            "audio_asset_id": "<redacted>",
            "bg_asset_id": "<redacted>",
            "video_id": video_id,
            "t0": t0, "t1": t1,
            "create_response_status": body.get("data", {}).get("status"),
            "create_output_format": body.get("data", {}).get("output_format"),
            "final_status": status,
            "final_duration": final.get("data", {}).get("duration"),
            "final_usage": final.get("data", {}).get("usage"),
            "failure_message": final.get("data", {}).get("failure_message"),
            "subtitle_url": final.get("data", {}).get("subtitle_url"),
            "captioned_video_url": final.get("data", {}).get("captioned_video_url"),
            "output_path": OUTPUT_PATH,
            "output_duration": dur,
            "output_resolution": res,
            "output_fps": v.get("r_frame_rate"),
            "output_pix_fmt": v.get("pix_fmt"),
            "output_video_codec": v.get("codec_name"),
            "output_audio_codec": a.get("codec_name"),
            "output_sample_rate": a.get("sample_rate"),
            "output_streams": kinds,
        }, f, indent=2)
    ok = ("video" in kinds and "audio" in kinds
          and abs(dur - audio_dur) <= 3.0 and res == "720x1280")
    print(f"VALIDATION: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
