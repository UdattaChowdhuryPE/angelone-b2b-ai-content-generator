"""Temporary local-only green-screen E2E (opt-in paid, NEVER runs by default).

Run only with:

    RUN_REAL_VIDEO_TEST=true GREEN_STUDIO_OUTDIR=/tmp/green_studio_e2e \
        uv run pytest tests/test_real_green_studio.py -s

Scope (exactly one controlled paid run when armed):
  - 30s controlled request (same body as tests/test_real_video.py)
  - real OpenAI script + claim gate (existing stages, unchanged)
  - real ElevenLabs voice (existing stage, unchanged)
  - real HeyGen GREEN-SCREEN avatar via a test-local worker-compatible
    adapter delegating to HeyGenAvatarProvider.generate_green()
  - broll_provider=LocalBrollProvider (deterministic local lavfi clips;
    Higgsfield skipped for this controlled test)
  - real local studio composition: compose_final() with
    avatar_mode="green" over the canonical production background
    assets/backgrounds/studio_background.png (desk foreground + captions)
  - existing fail-fast semantics (no retries — provider failures surface
    at the failed stage and stop; e.g. if HeyGen rejects the solid-color
    background payload, the job fails at the avatar stage with no
    downstream spend)

Production defaults are NOT changed by this file: backend/worker.py,
backend/app.py, and tests/test_real_video.py are untouched. When
RUN_REAL_VIDEO_TEST is unset (the normal suite), only the mocked
guard tests below run — zero paid calls.

GREEN_STUDIO_OUTDIR (optional): when set, final.mp4 + result.json +
desk_foreground.png are copied there for inspection, since the pytest
tmp_path is ephemeral.
"""

import inspect
import json
import os
import shutil
from unittest.mock import MagicMock, patch

import pytest
from dotenv import load_dotenv

RUN_REAL = os.getenv("RUN_REAL_VIDEO_TEST", "").lower() in ("1", "true", "yes")

#: Paid green E2E — skipped unless explicitly armed. Mirrors the gating
#: of tests/test_real_video.py; RUN_REAL_VIDEO_TEST behavior is unchanged.
needs_real = pytest.mark.skipif(
    not RUN_REAL,
    reason="Set RUN_REAL_VIDEO_TEST=true to run the paid green-studio E2E.",
)

#: Credential names only — values are never printed or logged.
REQUIRED_ENV_VARS = [
    "OPENAI_API_KEY",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "HEYGEN_API_KEY",
    "HEYGEN_AVATAR_ID",
]

#: Canonical production background. The only background this test may use.
CANONICAL_STUDIO_BACKGROUND = "assets/backgrounds/studio_background.png"

# Same credential-loading mechanism as app.py and scripts/*: resolve the
# project .env WITHOUT overriding already-exported values. Placed after
# the RUN_REAL read so arming semantics are unchanged — the flag comes
# exclusively from the process environment.
load_dotenv()


class GreenScreenAvatarAdapter:
    """Test-local worker-compatible adapter for HeyGen green-screen renders.

    Exposes the exact interface _run_downstream() requires (a .generate()
    method taking audio + output paths). Delegates to the REAL
    HeyGenAvatarProvider.generate_green() — audio-only upload, solid-green
    Avatar IV render, poll, download. Used ONLY by the paid green E2E in
    this file; production wiring is untouched.
    """

    def __init__(self, provider=None):
        if provider is not None:
            self.provider = provider
        else:
            from providers.heygen import HeyGenAvatarProvider

            self.provider = HeyGenAvatarProvider()

    def generate(self, audio_path: str, output_path: str) -> str:
        return self.provider.generate_green(audio_path, output_path)


def green_studio_compositor_fn(
    *,
    audio_path,
    avatar_path,
    broll,
    storyboard,
    job_dir,
    script_text=None,
) -> str:
    """Test-local compositor wiring for the green-screen E2E.

    Calls the REAL compose_final() with the studio path enabled:
    chromakeyed presenter -> canonical studio BG -> desk foreground ->
    captions, 1080x1920 H.264/AAC with existing validation.
    """
    from backend.worker import FINAL_FILENAME
    from video.compositor import compose_final

    return compose_final(
        audio_path=audio_path,
        avatar_path=avatar_path,
        broll=broll,
        storyboard=storyboard,
        output_path=os.path.join(job_dir, FINAL_FILENAME),
        job_dir=job_dir,
        script_text=script_text,
        studio_background_path=CANONICAL_STUDIO_BACKGROUND,
        avatar_mode="green",
    )


class LocalBrollProvider:
    """Test-local deterministic B-roll (zero Higgsfield spend).

    fetch(storyboard, job_dir) renders one silent 720x1280 H.264 lavfi
    clip per broll_required scene, sized to cover that scene's window,
    and returns {scene_id: clip_path} (the dict form worker validation
    and the compositor accept directly). Keeps the approved storyboard
    byte-identical — flags are never cleared. B-roll windows render as
    flat cutaways; the avatar scenes carry the green-studio validation.
    Used ONLY by the paid green E2E in this file.
    """

    COLOR = "navy"

    def fetch(self, storyboard, job_dir: str) -> dict:
        import subprocess

        from video.storyboard import get_broll_scenes

        clips: dict = {}
        bdir = os.path.join(job_dir, "broll")
        os.makedirs(bdir, exist_ok=True)
        for scene in get_broll_scenes(storyboard):
            window = float(scene.end) - float(scene.start)
            duration = round(max(window, 0.0) + 1.0, 2)
            path = os.path.join(bdir, f"local_s{scene.scene_id}.mp4")
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-f", "lavfi", "-i",
                    f"color=size=720x1280:rate=30:color={self.COLOR}"
                    f":duration={duration:.2f}",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-an", path,
                ],
                check=True,
                capture_output=True,
            )
            clips[scene.scene_id] = path
        return clips


def _controlled_request() -> dict:
    """Existing 30-second controlled request (same body as test_real_video)."""
    return {
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


@needs_real
def test_real_green_studio_end_to_end(tmp_path):
    missing = [k for k in REQUIRED_ENV_VARS if not os.getenv(k)]
    assert not missing, f"Missing credentials for real test: {missing}"
    bg = CANONICAL_STUDIO_BACKGROUND
    assert os.path.exists(bg), f"Canonical studio background missing: {bg}"

    from backend.store import JobStore
    from backend.worker import run_job
    from video.compositor import probe_media

    store = JobStore()
    request = _controlled_request()
    job = store.create(request)
    # Fail-fast, no retries: provider failures surface at the failed stage
    # and stop (run_job never auto-retries paid stages).
    final = run_job(
        job["job_id"],
        request,
        store,
        avatar_provider=GreenScreenAvatarAdapter(),
        broll_provider=LocalBrollProvider(),
        compositor_fn=green_studio_compositor_fn,
        output_root=str(tmp_path),
    )
    assert final["status"] == "completed", final.get("error")
    artifact = final["artifact_path"]
    assert artifact and os.path.exists(artifact)
    info = probe_media(artifact)
    video = next(s for s in info["streams"] if s["codec_type"] == "video")
    audio = next(s for s in info["streams"] if s["codec_type"] == "audio")
    assert video["codec_name"] == "h264"
    assert audio["codec_name"] == "aac"
    assert (video["width"], video["height"]) == (1080, 1920)
    # Captions were produced (PNG cards burned in by the compositor).
    cap_dir = os.path.join(str(tmp_path), job["job_id"], "captions")
    captions = sorted(
        n for n in os.listdir(cap_dir) if n.endswith(".png")
    ) if os.path.isdir(cap_dir) else []
    assert captions, "No caption PNGs produced for the green-studio final"
    # Desk foreground strip was built from the canonical background.
    desk = os.path.join(str(tmp_path), job["job_id"], "desk_foreground.png")
    assert os.path.exists(desk), "desk_foreground.png missing for green-studio final"

    report = {
        "job_id": job["job_id"],
        "status": final["status"],
        "artifact_path": artifact,
        "resolution": f"{video['width']}x{video['height']}",
        "duration": float(info["format"]["duration"]),
        "caption_pngs": len(captions),
        "desk_foreground": desk,
        "broll_source": "local-lavfi (Higgsfield skipped)",
        "result": final.get("result"),
    }
    print(json.dumps(
        {k: report[k] for k in (
            "job_id", "status", "artifact_path", "resolution",
            "duration", "caption_pngs", "desk_foreground",
            "broll_source",
        )},
        indent=2,
    ))
    outdir = os.getenv("GREEN_STUDIO_OUTDIR")
    if outdir:
        os.makedirs(outdir, exist_ok=True)
        shutil.copy(artifact, os.path.join(outdir, "final_green_studio.mp4"))
        with open(os.path.join(outdir, "result.json"), "w") as f:
            json.dump(report, f, indent=2)
        shutil.copy(desk, os.path.join(outdir, "desk_foreground.png"))
        print(f"inspection artifacts preserved at {outdir}")


def test_green_adapter_delegates_to_generate_green():
    """Non-paid: adapter.generate() calls the real provider's green path."""
    adapter = GreenScreenAvatarAdapter(provider=MagicMock())
    adapter.provider.generate_green.return_value = "avatar.mp4"
    assert adapter.generate("voice.mp3", "avatar.mp4") == "avatar.mp4"
    adapter.provider.generate_green.assert_called_once_with(
        "voice.mp3", "avatar.mp4"
    )


def test_green_compositor_wires_studio_mode():
    """Non-paid: green compositor passes green mode + canonical BG through."""
    with patch("video.compositor.compose_final", return_value="final.mp4") as mock:
        out = green_studio_compositor_fn(
            audio_path="a",
            avatar_path="v",
            broll={},
            storyboard=MagicMock(),
            job_dir="j",
            script_text="s",
        )
    assert out == "final.mp4"
    kwargs = mock.call_args[1]
    assert kwargs["avatar_mode"] == "green"
    assert kwargs["studio_background_path"] == CANONICAL_STUDIO_BACKGROUND


def test_local_broll_provider_maps_required_scenes(tmp_path):
    """Non-paid: local provider covers every broll_required scene (mocked)."""
    import subprocess as subprocess_mod

    from tests.fakes import make_storyboard

    board = make_storyboard()
    board.scenes[1].broll_required = True
    board.scenes[1].visual_prompt = "clip two"

    def fake_run(cmd, **kwargs):
        with open(cmd[-1], "wb") as f:
            f.write(b"clipbytes")
        return MagicMock()

    with patch.object(subprocess_mod, "run", side_effect=fake_run):
        clips = LocalBrollProvider().fetch(board, str(tmp_path))
    assert set(clips) == {2}
    assert clips[2].endswith("local_s2.mp4")


def test_controlled_request_matches_legacy_real_test():
    """Non-paid: same 30s controlled request body as test_real_video.py."""
    req = _controlled_request()
    assert req["duration_seconds"] == 30
    assert req["topic"] == "Why long-term investing beats reacting to market noise"
    assert "Market volatility is normal" in req["key_message"]


def test_worker_production_default_is_full_scene():
    """Non-paid: worker defaults wire the full_scene production path.

    Architecture change (controlled Photo Avatar experiment): the
    complete-scene Photo Avatar IS the studio, so the production
    default must NOT upload/composite any background and must NOT
    invoke green-screen generation. default_compositor_fn() passes
    avatar_mode="full_scene" (never a studio background path, never
    generate_green), and default_avatar_provider() is a
    FullSceneAvatarProvider (a HeyGenAvatarProvider whose generate()
    is the no-background full-scene render).
    """
    import backend.worker as worker_mod
    from providers.heygen import FullSceneAvatarProvider, HeyGenAvatarProvider

    src = inspect.getsource(worker_mod.default_compositor_fn)
    assert 'avatar_mode="full_scene"' in src
    assert "studio_background_path" not in src
    assert "generate_green" not in src
    provider = worker_mod.default_avatar_provider()
    assert isinstance(provider, FullSceneAvatarProvider)
    assert isinstance(provider, HeyGenAvatarProvider)
    assert not isinstance(provider, GreenScreenAvatarAdapter)


def test_visual_reference_never_used_in_green_e2e():
    """Non-paid: only the canonical production background may be wired in."""
    assert (
        CANONICAL_STUDIO_BACKGROUND
        == "assets/backgrounds/studio_background.png"
    )
    assert "reference" not in CANONICAL_STUDIO_BACKGROUND
