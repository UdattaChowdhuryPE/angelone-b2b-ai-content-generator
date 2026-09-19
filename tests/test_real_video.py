"""Explicit opt-in real end-to-end test — NEVER runs by default.

Run only with:

    RUN_REAL_VIDEO_TEST=true uv run pytest tests/test_real_video.py -s

This spends real money/time (OpenAI + ElevenLabs + HeyGen + Higgsfield +
FFmpeg) and requires all credentials. It is skipped in every other case,
including the normal `uv run pytest` suite.

Required env (names only — values stay in .env, never printed):
  OPENAI_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID,
  HEYGEN_API_KEY, HEYGEN_AVATAR_ID,
  HF_API_KEY_ID, HF_API_KEY_SECRET
Optional:
  HEYGEN_BACKGROUND_IMAGE (defaults to assets/backgrounds/studio_background.png)
"""

import os

import pytest
from dotenv import load_dotenv

RUN_REAL = os.getenv("RUN_REAL_VIDEO_TEST", "").lower() in ("1", "true", "yes")

pytestmark = pytest.mark.skipif(
    not RUN_REAL,
    reason="Set RUN_REAL_VIDEO_TEST=true to run the paid end-to-end test.",
)

#: Credential names only — values are never printed or logged.
REQUIRED_ENV_VARS = [
    "OPENAI_API_KEY",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "HEYGEN_API_KEY",
    "HEYGEN_AVATAR_ID",
    "HF_API_KEY_ID",
    "HF_API_KEY_SECRET",
]

# Same credential-loading mechanism as app.py and scripts/*: resolve the
# project .env WITHOUT overriding already-exported values. Placed after
# the RUN_REAL read so arming semantics are unchanged — the flag comes
# exclusively from the process environment.
load_dotenv()


def test_real_end_to_end_captioned_1080x1920(tmp_path):
    missing = [k for k in REQUIRED_ENV_VARS if not os.getenv(k)]
    assert not missing, f"Missing credentials for real test: {missing}"

    from backend.store import JobStore
    from backend.worker import run_job
    from video.compositor import probe_media

    store = JobStore()
    request = {
        "topic": "Why long-term investing beats reacting to market noise",
        "key_message": (
            "Market volatility is normal. Short-term price movements are "
            "difficult to predict. Investors with a long-term horizon should "
            "avoid decisions based purely on short-term movements and follow "
            "their planned investment approach."
        ),
        "language": "English",
        "duration_seconds": 30,
    }
    job = store.create(request)
    final = run_job(
        job["job_id"],
        request,
        store,
        output_root=str(tmp_path),
        # Production defaults: real VideoPipeline + HeyGen + Higgsfield +
        # captioned 1080x1920 compositor.
    )
    assert final["status"] == "completed", final.get("error")
    artifact = final["artifact_path"]
    assert artifact and os.path.exists(artifact)
    info = probe_media(artifact)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (1080, 1920)
