"""Smoke-test-only fakes for the manual Streamlit <-> FastAPI smoke test.

This module is intentionally self-contained: it must NOT import from
``tests/``, must NOT import any production provider (OpenAI, ElevenLabs,
HeyGen, Higgsfield), and must NOT touch the network, subprocess/FFmpeg,
API keys, or the filesystem (no audio/video artifacts written).

The worker (``backend.worker.run_job``) still writes its standard
``result.json`` summary into the job dir — that is the only file a smoke
job creates. No compositor is wired, so ``video_url`` stays ``None``.
"""

import time

from pipeline.models import Scene, Script, Storyboard

SMOKE_FAILURE_SENTINEL = "__SMOKE_TEST_FAILURE__"

SMOKE_TITLE = "Smoke Test Video"
SMOKE_HOOK = "This is a backend integration smoke test."
SMOKE_BODY = ["Test body point one.", "Test body point two."]
SMOKE_TAKEAWAY = "Smoke test completed."
SMOKE_FULL_SCRIPT = (
    f"{SMOKE_TITLE}. {SMOKE_HOOK} {' '.join(SMOKE_BODY)} {SMOKE_TAKEAWAY}"
)


def make_smoke_script() -> Script:
    return Script(
        title=SMOKE_TITLE,
        hook=SMOKE_HOOK,
        body=list(SMOKE_BODY),
        takeaway=SMOKE_TAKEAWAY,
        full_script=SMOKE_FULL_SCRIPT,
    )


def make_smoke_storyboard(duration_seconds: int = 30) -> Storyboard:
    total = float(duration_seconds)
    half = total / 2.0
    scenes = [
        Scene(
            scene_id=1,
            start=0.0,
            end=half,
            narration=SMOKE_HOOK,
            key_claim="Smoke test hook",
            visual_type="text",
        ),
        Scene(
            scene_id=2,
            start=half,
            end=total,
            narration=" ".join(SMOKE_BODY),
            key_claim="Smoke test body",
            visual_type="text",
        ),
    ]
    return Storyboard(scenes=scenes, total_duration=total)


class SmokeFakePipeline:
    """Deterministic fake honouring VideoPipeline.create_assets signature.

    - No network, no API keys, no FFmpeg, no file writes.
    - Raises a controlled error when the test-only sentinel is present in
      ``request.key_message`` (smoke backend only, never production).
    - Sleeps briefly (~1.5s) so manual polling can observe
      queued -> processing -> completed.
    """

    def __init__(self, delay_seconds: float = 1.5):
        self.delay_seconds = delay_seconds

    def create_assets(self, request, audio_output_path: str = "output/voice.mp3") -> dict:
        key_message = getattr(request, "key_message", "") or ""
        if SMOKE_FAILURE_SENTINEL in key_message:
            raise RuntimeError(
                "Smoke test induced failure (test-only, no external calls made)."
            )
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        script = make_smoke_script()
        duration = getattr(request, "duration_seconds", 30) or 30
        storyboard = make_smoke_storyboard(duration)
        return {
            "script": script,
            "storyboard": storyboard,
            "broll": [],
            "audio_path": audio_output_path,
        }
