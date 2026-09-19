"""Dry-run contract test (fakes/synthetic only — never paid providers).

Proves worker stage ordering, artifact contracts, stage persistence,
failure propagation wiring, compositor signature, and final validation
config without calling any external API.
"""

import inspect
import json
import os

from backend.store import JobStore
from backend.worker import USE_REAL_PROVIDERS, run_job
from tests.fakes import (
    FakeAvatar,
    FakeBroll,
    StagedFakePipeline,
    make_fake_compositor,
)


def _request():
    return {
        "topic": "T",
        "key_message": "Point one. Point two.",
        "language": "English",
        "duration_seconds": 60,
    }


def test_dry_run_stage_order_and_contracts(tmp_path):
    calls: list = []
    store = JobStore()
    job = store.create(_request())
    final = run_job(
        job["job_id"],
        _request(),
        store,
        pipeline_factory=lambda: StagedFakePipeline(calls),
        avatar_provider=FakeAvatar(calls),
        broll_provider=FakeBroll(calls),
        compositor_fn=make_fake_compositor(calls),
        output_root=str(tmp_path),
    )
    assert final["status"] == "completed"
    # Stage ordering: script -> validate -> storyboard -> voice ->
    # avatar -> broll -> compositor.
    order = [
        "stage.create_script",
        "stage.validate_script",
        "stage.create_storyboard",
        "stage.create_voice",
        "avatar.generate",
        "broll.fetch",
        "compositor.render",
    ]
    positions = [calls.index(marker) for marker in order]
    assert positions == sorted(positions)
    # Artifact contracts persisted to store and disk.
    assert final["script"]["full_script"]
    assert len(final["storyboard"]["scenes"]) == 2
    assert final["failed_stage"] is None
    job_dir = os.path.join(str(tmp_path), job["job_id"])
    assert os.path.exists(os.path.join(job_dir, "script.json"))
    assert os.path.exists(os.path.join(job_dir, "storyboard.json"))
    assert os.path.exists(os.path.join(job_dir, "voice.mp3"))
    assert json.load(open(os.path.join(job_dir, "result.json")))[
        "num_scenes"
    ] == 2


def test_production_contracts_match_worker_expectations():
    """Provider/compositor method contracts match what the worker calls."""
    from providers.heygen import HeyGenAvatarProvider
    from providers.higgsfield import HiggsfieldProvider
    from video.compositor import FINAL_HEIGHT, FINAL_WIDTH, compose_final

    assert hasattr(HeyGenAvatarProvider, "generate")
    assert hasattr(HeyGenAvatarProvider, "upload_audio")
    assert hasattr(HeyGenAvatarProvider, "upload_image")
    assert hasattr(HeyGenAvatarProvider, "create_video")
    assert hasattr(HiggsfieldProvider, "fetch")
    sig = inspect.signature(compose_final)
    for param in (
        "audio_path",
        "avatar_path",
        "broll",
        "storyboard",
        "output_path",
    ):
        assert param in sig.parameters
    assert (FINAL_WIDTH, FINAL_HEIGHT) == (1080, 1920)


def test_production_defaults_stay_real_and_fake_free():
    from backend.app import create_app

    app = create_app()
    assert app.state.avatar_provider is USE_REAL_PROVIDERS
    assert app.state.broll_provider is USE_REAL_PROVIDERS
    assert app.state.compositor_fn is USE_REAL_PROVIDERS
