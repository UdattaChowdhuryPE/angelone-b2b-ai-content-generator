"""HeyGen Avatar III vs IV 30s A/B test (real API, exactly 2 paid renders).

Prereqs (done before this script runs):
- output/heygen_ab_30s.mp3 exists, ~30s, generated ONCE via ElevenLabs.
- Personal avatar supports avatar_iii + avatar_iv (free GET check).

Flow: upload audio ONCE (one asset_id => byte-identical audio for both),
create one video per engine, poll both, download both, combined log.

Usage:
    PYTHONPATH=. uv run python scripts/heygen_ab.py
"""

import datetime
import json
import os
import subprocess
import sys

from dotenv import load_dotenv

load_dotenv()

from providers.heygen import HeyGenAvatarProvider

AUDIO_PATH = "output/heygen_ab_30s.mp3"
RESULTS = {
    "avatar_iii": "output/heygen_personal_avatar_iii_30s.mp4",
    "avatar_iv": "output/heygen_personal_avatar_iv_30s.mp4",
}
LOG_PATH = "output/heygen_ab_30s.json"


def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json",
         "-show_format", "-show_streams", path],
        check=True, capture_output=True, text=True,
    )
    return json.loads(out.stdout)


def utcnow() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def main() -> int:
    audio_info = probe(AUDIO_PATH)
    audio_duration = float(audio_info["format"].get("duration", 0.0))
    print(f"audio: {AUDIO_PATH} {audio_duration:.2f}s")

    provider = HeyGenAvatarProvider()
    print(f"avatar_id: {provider.avatar_id}")

    print("Uploading audio ONCE (shared asset for both renders)...")
    asset_id = provider.upload_audio(AUDIO_PATH)
    print(f"  asset_id: {asset_id}")

    jobs: dict = {}
    for engine, out_path in RESULTS.items():
        print(f"Creating {engine} video...")
        t0 = utcnow()
        created = provider.create_video(
            asset_id,
            title=f"A/B test {engine} 30s",
            aspect_ratio="9:16",
            resolution="720p",
            engine={"type": engine},
        )
        video_id = created["data"]["video_id"]
        print(f"  {engine}: video_id={video_id} status={created['data'].get('status')} t0={t0}")
        jobs[engine] = {
            "video_id": video_id,
            "output_path": out_path,
            "create_response": created,
            "render_started_at": t0,
        }

    log: dict = {
        "avatar_id": provider.avatar_id,
        "audio_path": AUDIO_PATH,
        "audio_duration": audio_duration,
        "asset_id": asset_id,
        "aspect_ratio": "9:16",
        "resolution": "720p",
        "tests": {},
    }

    ok_all = True
    for engine, out_path in RESULTS.items():
        job = jobs[engine]
        print(f"Polling {engine} ({job['video_id']})...")
        final = provider.wait_for_result(job["video_id"])
        t1 = utcnow()
        status = final["data"].get("status")
        video_url = final["data"].get("video_url")
        print(f"  {engine}: status={status} t1={t1}")
        entry = {
            "video_id": job["video_id"],
            "status": status,
            "render_started_at": job["render_started_at"],
            "render_finished_at": t1,
            "create_response": job["create_response"],
            "final_response": final,
        }
        if not video_url:
            print(f"  {engine} FAILED: no video_url")
            entry["output_path"] = out_path
            log["tests"][engine] = entry
            ok_all = False
            continue
        provider.download_video(video_url, out_path)
        info = probe(out_path)
        kinds = sorted(s.get("codec_type") for s in info.get("streams", []))
        dur = float(info["format"].get("duration", 0.0))
        vstream = next((s for s in info["streams"] if s.get("codec_type") == "video"), {})
        res = f"{vstream.get('width')}x{vstream.get('height')}"
        print(f"  {engine}: {out_path} {dur:.2f}s {res} {kinds}")
        entry.update({
            "output_path": out_path,
            "output_duration": dur,
            "output_streams": kinds,
            "output_resolution": res,
        })
        ok = ("video" in kinds and "audio" in kinds
              and abs(dur - audio_duration) <= 3.0 and res == "720x1280")
        entry["validation"] = "PASS" if ok else "FAIL"
        ok_all = ok_all and ok
        log["tests"][engine] = entry

    with open(LOG_PATH, "w") as f:
        json.dump(log, f, indent=2)
    print(f"log: {LOG_PATH}")
    print(f"A/B VALIDATION: {'PASS' if ok_all else 'FAIL'}")
    return 0 if ok_all else 2


if __name__ == "__main__":
    sys.exit(main())
