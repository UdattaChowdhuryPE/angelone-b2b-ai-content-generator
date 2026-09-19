"""Fail-fast stage propagation: Tests A-F.

If an upstream stage fails, no downstream stage may execute. The job must
be FAILED at the exact failed stage with structured context persisted.
No paid retries happen — run_job never raises, it records the failure.
"""

import os

from backend.store import JobStore
from backend.worker import run_job
from tests.fakes import (
    FakeAvatar,
    FakeBroll,
    StagedFakePipeline,
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


def _run(tmp_path, pipeline_factory, avatar, broll, comp, calls):
    store = JobStore()
    job = store.create(_request())
    final = run_job(
        job["job_id"],
        _request(),
        store,
        pipeline_factory=pipeline_factory,
        avatar_provider=avatar,
        broll_provider=broll,
        compositor_fn=comp,
        output_root=str(tmp_path),
    )
    return store, final, job["job_id"]


class FailScript(StagedFakePipeline):
    def create_script(self, request):
        self.calls.append("stage.create_script")
        raise RuntimeError("boom-script")


class FailStoryboard(StagedFakePipeline):
    def create_storyboard(self, request, script):
        self.calls.append("stage.create_storyboard")
        raise RuntimeError("boom-storyboard")


class FailVoice(StagedFakePipeline):
    def create_voice(self, script, audio_output_path):
        self.calls.append("stage.create_voice")
        raise RuntimeError("ElevenLabs boom-voice")


class FailAvatar:
    def __init__(self, calls):
        self.calls = calls

    def generate(self, audio_path, output_path):
        self.calls.append("avatar.generate")
        raise RuntimeError("HeyGen generation failed with status 'failed'")


class FailBroll:
    def __init__(self, calls):
        self.calls = calls

    def fetch(self, scenes, job_dir):
        self.calls.append("broll.fetch")
        raise RuntimeError("Higgsfield boom-broll")


def _fail_compositor(calls):
    def _compose(**kwargs):
        calls.append("compositor.render")
        raise RuntimeError("FFmpeg assembly failed (exit 1)")

    return _compose


def test_a_script_failure_stops_everything(tmp_path):
    calls: list = []
    _, final, job_id = _run(
        tmp_path,
        pipeline_factory=lambda: FailScript(calls),
        avatar=FakeAvatar(calls),
        broll=FakeBroll(calls),
        comp=make_fake_compositor(calls),
        calls=calls,
    )
    assert final["status"] == "failed"
    assert final["failed_stage"] == "script"
    assert final["current_stage"] == "failed"
    assert "stage.create_storyboard" not in calls
    assert "stage.create_voice" not in calls
    assert "avatar.generate" not in calls
    assert "broll.fetch" not in calls
    assert "compositor.render" not in calls
    assert "storyboard" in (final["skipped_stages"] or [])
    assert "composition" in (final["skipped_stages"] or [])


def test_b_storyboard_failure_stops_voice_and_beyond(tmp_path):
    calls: list = []
    _, final, _ = _run(
        tmp_path,
        pipeline_factory=lambda: FailStoryboard(calls),
        avatar=FakeAvatar(calls),
        broll=FakeBroll(calls),
        comp=make_fake_compositor(calls),
        calls=calls,
    )
    assert final["status"] == "failed"
    assert final["failed_stage"] == "storyboard"
    assert "stage.create_voice" not in calls
    assert "avatar.generate" not in calls
    assert "broll.fetch" not in calls
    assert "compositor.render" not in calls
    assert "voice" in (final["skipped_stages"] or [])


def test_c_voice_failure_stops_avatar_and_beyond(tmp_path):
    calls: list = []
    _, final, _ = _run(
        tmp_path,
        pipeline_factory=lambda: FailVoice(calls),
        avatar=FakeAvatar(calls),
        broll=FakeBroll(calls),
        comp=make_fake_compositor(calls),
        calls=calls,
    )
    assert final["status"] == "failed"
    assert final["failed_stage"] == "voice"
    assert final["operation"] == "voice.generate"
    assert "avatar.generate" not in calls
    assert "broll.fetch" not in calls
    assert "compositor.render" not in calls


def test_d_avatar_failure_skips_broll_and_composition(tmp_path):
    calls: list = []
    store, final, job_id = _run(
        tmp_path,
        pipeline_factory=lambda: StagedFakePipeline(calls),
        avatar=FailAvatar(calls),
        broll=FakeBroll(calls),
        comp=make_fake_compositor(calls),
        calls=calls,
    )
    assert final["status"] == "failed"
    assert final["failed_stage"] == "avatar"
    assert "broll.fetch" not in calls
    assert "compositor.render" not in calls
    assert "broll" in (final["skipped_stages"] or [])
    assert "composition" in (final["skipped_stages"] or [])
    completed = final["completed_artifacts"] or []
    assert "script.json" in completed
    assert "storyboard.json" in completed
    assert "voice.mp3" in completed
    # Upstream paid artifacts survive for explicit retry.
    job_dir = os.path.join(str(tmp_path), job_id)
    assert os.path.exists(os.path.join(job_dir, "voice.mp3"))
    assert not os.path.exists(os.path.join(job_dir, "final.mp4"))


def test_e_broll_failure_skips_composition(tmp_path):
    calls: list = []
    _, final, _ = _run(
        tmp_path,
        pipeline_factory=lambda: StagedFakePipeline(calls),
        avatar=FakeAvatar(calls),
        broll=FailBroll(calls),
        comp=make_fake_compositor(calls),
        calls=calls,
    )
    assert final["status"] == "failed"
    assert final["failed_stage"] == "broll"
    assert "compositor.render" not in calls
    assert "composition" in (final["skipped_stages"] or [])


def test_f_composition_failure_never_marks_completed(tmp_path):
    calls: list = []
    _, final, job_id = _run(
        tmp_path,
        pipeline_factory=lambda: StagedFakePipeline(calls),
        avatar=FakeAvatar(calls),
        broll=FakeBroll(calls),
        comp=_fail_compositor(calls),
        calls=calls,
    )
    assert final["status"] == "failed"
    assert final["failed_stage"] == "composition"
    assert final["artifact_path"] is None
    assert final["video_url"] is None
    assert "compositor.render" in calls  # attempted once, never retried
    assert calls.count("compositor.render") == 1
