"""Regression: worker entry-point defaults wire REAL providers.

AGENTS.md contract: "Defaults wire REAL providers (USE_REAL_PROVIDERS);
None skips a stage." A direct run_job() call with no provider args (what
Gate 7 / test_real_video.py does) must execute avatar → broll →
composition instead of silently skipping to a hollow "completed".

All providers here are fakes injected via the default factories —
zero network, zero paid calls.
"""

from unittest.mock import patch

from backend import worker as worker_mod
from backend.store import JobStore
from backend.worker import run_job
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


def test_run_job_defaults_execute_downstream_stages(tmp_path):
    """Omitting provider args must NOT skip paid stages."""
    store = JobStore()
    job = store.create(_request())
    calls: list = []
    avatar = FakeAvatar(calls)
    broll = FakeBroll(calls)
    comp = make_fake_compositor(calls)
    with (
        patch.object(worker_mod, "default_avatar_provider", return_value=avatar),
        patch.object(worker_mod, "default_broll_provider", return_value=broll),
        patch.object(worker_mod, "default_compositor_fn", comp),
    ):
        final = run_job(
            job["job_id"],
            _request(),
            store,
            pipeline_factory=lambda: StagedFakePipeline(calls),
            output_root=str(tmp_path),
        )
    assert final["status"] == "completed"
    assert "avatar.generate" in calls
    assert "broll.fetch" in calls
    assert "compositor.render" in calls
    assert final["artifact_path"] is not None
    assert final["video_url"] == f"/api/videos/{job['job_id']}/file"


def test_explicit_none_still_skips_stages(tmp_path):
    """None keeps its documented meaning: skip the stage (fake flows)."""
    store = JobStore()
    job = store.create(_request())
    calls: list = []
    final = run_job(
        job["job_id"],
        _request(),
        store,
        pipeline_factory=lambda: StagedFakePipeline(calls),
        avatar_provider=None,
        broll_provider=None,
        compositor_fn=None,
        output_root=str(tmp_path),
    )
    assert final["status"] == "completed"
    assert "avatar.generate" not in calls
    assert "broll.fetch" not in calls
    assert "compositor.render" not in calls
    assert final["artifact_path"] is None
