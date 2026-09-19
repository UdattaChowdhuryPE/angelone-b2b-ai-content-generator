"""Staged worker: stage progression, persistence, real-provider wiring."""

import json
import os
from unittest.mock import patch

from backend import worker as worker_mod
from backend.store import JobStore
from backend.worker import (
    USE_REAL_PROVIDERS,
    load_stored_script,
    load_stored_storyboard,
    run_job,
)
from tests.fakes import (
    FakeAvatar,
    FakeBroll,
    FakePipeline,
    StagedFakePipeline,
    make_fake_compositor,
    make_script,
    make_storyboard,
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


def test_staged_run_persists_and_recovers_artifacts(tmp_path):
    store = JobStore()
    job = store.create(_request())
    calls: list = []
    final = run_job(
        job["job_id"], _request(), store,
        pipeline_factory=lambda: StagedFakePipeline(calls),
        avatar_provider=FakeAvatar(calls),
        broll_provider=FakeBroll(calls),
        compositor_fn=make_fake_compositor(calls),
        output_root=str(tmp_path),
    )
    assert final["status"] == "completed"
    assert final["current_stage"] == "completed"
    assert final["status_detail"] == "Completed."
    assert set(final["result"]) >= {
        "audio_path", "script_preview", "num_scenes",
        "avatar_path", "broll", "caption_count",
    }
    # Full Script + Storyboard recoverable from store and from disk.
    assert final["script"]["title"] == "Test Title"
    assert len(final["storyboard"]["scenes"]) == 2
    job_dir = os.path.join(str(tmp_path), job["job_id"])
    assert load_stored_script(job_dir).title == "Test Title"
    assert len(load_stored_storyboard(job_dir).scenes) == 2
    assert json.load(open(os.path.join(job_dir, "result.json")))["num_scenes"] == 2
    # Real production avatar naming.
    assert final["result"]["avatar_path"].endswith("avatar.mp4")


def test_use_real_sentinel_resolves_to_real_providers(tmp_path):
    """USE_REAL_PROVIDERS (the production default) builds real providers."""
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
            job["job_id"], _request(), store,
            pipeline_factory=lambda: StagedFakePipeline(calls),
            avatar_provider=USE_REAL_PROVIDERS,
            broll_provider=USE_REAL_PROVIDERS,
            compositor_fn=USE_REAL_PROVIDERS,
            output_root=str(tmp_path),
        )
    assert final["status"] == "completed"
    assert "avatar.generate" in calls
    assert "compositor.render" in calls


def test_production_app_defaults_are_real_providers():
    from backend.app import create_app

    app = create_app()
    assert app.state.avatar_provider is USE_REAL_PROVIDERS
    assert app.state.broll_provider is USE_REAL_PROVIDERS
    assert app.state.compositor_fn is USE_REAL_PROVIDERS


def test_no_fake_providers_imported_by_production_backend():
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    for name in ("backend/app.py", "backend/worker.py"):
        src = (root / name).read_text()
        code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
        code = "\n".join(line.split("#")[0] for line in code.splitlines())
        assert "tests.fakes" not in code, f"{name} imports test fakes"
        assert "tests import" not in code
        assert "from tests" not in code
        assert "smoke_fakes" not in code
        assert "FakePipeline" not in code
        assert "FakeAvatar" not in code
        assert "FakeBroll" not in code


def test_failed_stage_records_failed_stage_and_cleans_partial(tmp_path):
    store = JobStore()
    job = store.create(_request())

    class FailAvatar:
        def generate(self, audio_path, output_path):
            raise RuntimeError("HeyGen generation failed with status 'failed'")

    final = run_job(
        job["job_id"], _request(), store,
        pipeline_factory=lambda: StagedFakePipeline([]),
        avatar_provider=FailAvatar(),
        broll_provider=None,
        compositor_fn=None,
        output_root=str(tmp_path),
    )
    assert final["status"] == "failed"
    assert final["current_stage"] == "failed"
    assert "HeyGen" in final["error"]
    job_dir = os.path.join(str(tmp_path), job["job_id"])
    assert not os.path.exists(os.path.join(job_dir, "final.mp4.partial"))
    # Upstream artifacts survive the failure (retry reuses them).
    assert os.path.exists(os.path.join(job_dir, "script.json"))
    assert os.path.exists(os.path.join(job_dir, "voice.mp3"))


def test_legacy_pipeline_still_completes(tmp_path):
    store = JobStore()
    job = store.create(_request())
    final = run_job(
        job["job_id"], _request(), store,
        pipeline_factory=lambda: FakePipeline([]),
        avatar_provider=None,  # explicit skip (defaults wire real providers)
        broll_provider=None,
        compositor_fn=None,
        output_root=str(tmp_path),
    )
    assert final["status"] == "completed"
    assert final["script"]["title"] == "Test Title"


def test_broll_empty_dict_falls_back_to_metadata(tmp_path):
    """Regression: broll_out={} must recover persisted metadata.json.

    The provider may return an empty mapping even though it persisted
    valid clips + metadata.json. The compositor must receive the
    recovered clips, not an empty mapping.
    """
    store = JobStore()
    job = store.create(_request())
    calls: list = []
    captured: dict = {}

    class EmptyBroll:
        def fetch(self, storyboard, job_dir):
            calls.append("broll.fetch")
            broll_dir = os.path.join(job_dir, "broll")
            os.makedirs(broll_dir, exist_ok=True)
            clip = os.path.join(broll_dir, "scene_1.mp4")
            with open(clip, "w") as f:
                f.write("FAKE_BROLL_FOR_TESTS")
            with open(os.path.join(broll_dir, "metadata.json"), "w") as f:
                json.dump([{"scene_id": 1, "clip_path": clip}], f)
            return {}

    def _capture_compositor(
        *,
        audio_path,
        avatar_path,
        broll,
        job_dir,
        storyboard=None,
        script_text=None,
    ):
        captured["broll"] = broll
        out = os.path.join(job_dir, "artifact.txt")
        with open(out, "w") as f:
            f.write("FAKE_ARTIFACT_FOR_TESTS")
        return out

    final = run_job(
        job["job_id"], _request(), store,
        pipeline_factory=lambda: StagedFakePipeline(calls),
        avatar_provider=FakeAvatar(calls),
        broll_provider=EmptyBroll(),
        compositor_fn=_capture_compositor,
        output_root=str(tmp_path),
    )
    assert final["status"] == "completed"
    job_dir = os.path.join(str(tmp_path), job["job_id"])
    expected = os.path.join(job_dir, "broll", "scene_1.mp4")
    assert captured["broll"] == {1: expected}
    assert final["result"]["broll"] == [expected]
