"""Deterministic worker lifecycle tests: call run_job directly (no HTTP)."""

import json
import os

import pytest

from backend.store import JobStore
from backend.worker import run_job
from pipeline.models import ScriptValidationError
from tests.fakes import (
    FakeAvatar,
    FakeBroll,
    FakePipeline,
    FailingPipeline,
    make_fake_compositor,
)


def _request(**overrides):
    body = {
        "topic": "T",
        "key_message": "Point one. Point two.",
        "language": "English",
        "duration_seconds": 60,
    }
    body.update(overrides)
    return body


def test_success_transitions_queued_processing_completed(tmp_path):
    store = JobStore()
    job = store.create(_request())
    assert job["status"] == "queued"
    calls: list = []
    final = run_job(
        job["job_id"], _request(), store,
        pipeline_factory=lambda: FakePipeline(calls),
        avatar_provider=None,  # explicit skip (defaults wire real providers)
        broll_provider=None,
        compositor_fn=None,
        output_root=str(tmp_path),
    )
    assert final["status"] == "completed"
    assert final["error"] is None
    assert final["video_url"] is None  # no compositor → no invented artifact
    assert os.path.exists(os.path.join(str(tmp_path), job["job_id"], "voice.mp3"))
    assert os.path.exists(os.path.join(str(tmp_path), job["job_id"], "result.json"))


def test_failure_transitions_to_failed(tmp_path):
    store = JobStore()
    job = store.create(_request())
    calls: list = []
    err = ScriptValidationError("Unsupported claims: x")
    final = run_job(
        job["job_id"], _request(), store,
        pipeline_factory=lambda: FailingPipeline(err, calls),
        output_root=str(tmp_path),
    )
    assert final["status"] == "failed"
    assert "Unsupported claims" in final["error"]


def test_provider_invocation_order(tmp_path):
    store = JobStore()
    job = store.create(_request())
    calls: list = []
    avatar = FakeAvatar(calls)
    broll = FakeBroll(calls)
    compositor = make_fake_compositor(calls)
    final = run_job(
        job["job_id"], _request(), store,
        pipeline_factory=lambda: FakePipeline(calls),
        avatar_provider=avatar,
        broll_provider=broll,
        compositor_fn=compositor,
        output_root=str(tmp_path),
    )
    assert final["status"] == "completed"
    assert calls == [
        "generate_script",
        "validate_script",
        "generate_storyboard",
        "voice.generate",
        "avatar.generate",
        "broll.fetch",
        "compositor.render",
    ]
    # compositor artifact exists and job exposes a file URL (text, not mp4)
    assert final["video_url"] == f"/api/videos/{job['job_id']}/file"
    assert final["artifact_path"].endswith("artifact.txt")
    assert not final["artifact_path"].endswith(".mp4")


def test_job_output_isolation(tmp_path):
    store = JobStore()
    j1 = store.create(_request(topic="One"))
    j2 = store.create(_request(topic="Two"))
    assert j1["job_id"] != j2["job_id"]
    run_job(j1["job_id"], _request(topic="One"), store,
            pipeline_factory=lambda: FakePipeline([]),
            avatar_provider=None, broll_provider=None, compositor_fn=None,
            output_root=str(tmp_path))
    run_job(j2["job_id"], _request(topic="Two"), store,
            pipeline_factory=lambda: FakePipeline([]),
            avatar_provider=None, broll_provider=None, compositor_fn=None,
            output_root=str(tmp_path))
    d1 = os.path.join(str(tmp_path), j1["job_id"])
    d2 = os.path.join(str(tmp_path), j2["job_id"])
    assert os.path.isdir(d1) and os.path.isdir(d2) and d1 != d2
    assert os.path.exists(os.path.join(d1, "voice.mp3"))
    assert os.path.exists(os.path.join(d2, "voice.mp3"))
    with open(os.path.join(d1, "result.json")) as f:
        assert json.load(f)["audio_path"].startswith(d1)
    with open(os.path.join(d2, "result.json")) as f:
        assert json.load(f)["audio_path"].startswith(d2)


def test_production_worker_creates_no_fake_video(tmp_path):
    """Without compositor, worker must not invent any video/artifact file."""
    store = JobStore()
    job = store.create(_request())
    run_job(job["job_id"], _request(), store,
            pipeline_factory=lambda: FakePipeline([]),
            avatar_provider=None, broll_provider=None, compositor_fn=None,
            output_root=str(tmp_path))
    job_dir = os.path.join(str(tmp_path), job["job_id"])
    names = set(os.listdir(job_dir))
    assert "fake_video.mp4" not in names
    assert not any(n.endswith(".mp4") for n in names)
    # Stage outputs are persisted; only the compositor may add a video.
    assert names == {"voice.mp3", "result.json", "script.json", "storyboard.json"}
