"""Smoke-test backend entrypoint (test-only, explicit fake mode).

Run with::

    uv run uvicorn backend.smoke_app:app --port 8000

No API keys required. Never calls OpenAI / ElevenLabs / HeyGen /
Higgsfield / FFmpeg and never produces a video artifact (``video_url``
stays ``None``). Production ``backend/app.py`` is unchanged and never
imports this module.
"""

from backend.app import create_app
from backend.smoke_fakes import SmokeFakePipeline

app = create_app(
    pipeline_factory=lambda: SmokeFakePipeline(),
    avatar_provider=None,
    broll_provider=None,
    compositor_fn=None,
)
