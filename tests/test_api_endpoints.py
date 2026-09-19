"""Regenerate / retry / file endpoints + extended status contract."""

import os

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.store import JobStore
from backend.worker import run_job
from tests.fakes import (
    FakeAvatar,
    FakeBroll,
    StagedFakePipeline,
    make_fake_compositor,
    make_script,
    make_storyboard,
)


def _payload(**overrides):
    body = {
        "topic": "T",
        "key_message": "Point one. Point two.",
        "language": "English",
        "duration_seconds": 60,
    }
    body.update(overrides)
    return body


def _wiring(calls):
    return {
        "pipeline_factory": lambda: StagedFakePipeline(calls),
        "avatar_provider": FakeAvatar(calls),
        "broll_provider": FakeBroll(calls),
        "compositor_fn": make_fake_compositor(calls),
    }


def _completed_client(tmp_path, calls, **overrides):
    store = JobStore()
    wiring = _wiring(calls)
    wiring.update(overrides)
    app = create_app(store=store, output_root=str(tmp_path), **wiring)
    client = TestClient(app)
    job_id = client.post("/api/videos", json=_payload()).json()["job_id"]
    job = client.get(f"/api/videos/{job_id}").json()
    assert job["status"] == "completed"
    return client, store, job_id


def test_status_includes_stages_script_storyboard(tmp_path):
    calls: list = []
    client, _, job_id = _completed_client(tmp_path, calls)
    job = client.get(f"/api/videos/{job_id}").json()
    assert job["current_stage"] == "completed"
    assert job["status_detail"] == "Completed."
    assert job["script"]["title"] == "Test Title"
    assert len(job["storyboard"]["scenes"]) == 2
    assert job["result"]["num_scenes"] == 2
    assert "created_at" in job and "updated_at" in job


def test_script_regenerate_rebuilds_downstream(tmp_path):
    calls: list = []
    new_script = make_script("Brand new hook. Brand new point. New takeaway.")
    store = JobStore()
    created = []

    def factory():
        # First pipeline run uses defaults; regen run uses the new script.
        if not created:
            created.append(True)
            return StagedFakePipeline(calls)
        return StagedFakePipeline(calls, scripts=[new_script])

    app = create_app(
        store=store,
        pipeline_factory=factory,
        avatar_provider=FakeAvatar(calls),
        broll_provider=FakeBroll(calls),
        compositor_fn=make_fake_compositor(calls),
        output_root=str(tmp_path),
    )
    client = TestClient(app)
    job_id = client.post("/api/videos", json=_payload()).json()["job_id"]
    voice_calls_before = calls.count("stage.create_voice")

    resp = client.post(f"/api/videos/{job_id}/script/regenerate")
    assert resp.status_code == 202
    job = resp.json()
    assert job["status"] == "completed"
    assert job["script"]["full_script"].startswith("Brand new hook")
    voice_calls_after = calls.count("stage.create_voice")
    assert voice_calls_after > voice_calls_before  # downstream regenerated
    assert "compositor.render" in calls
    # New script persisted to disk.
    import json as _json

    disk = _json.load(open(os.path.join(str(tmp_path), job_id, "script.json")))
    assert disk["full_script"].startswith("Brand new hook")


def test_script_regenerate_validation_failure_preserves_working_video(tmp_path):
    calls: list = []
    store = JobStore()
    runs = []

    def factory():
        runs.append(1)
        if len(runs) == 1:
            return StagedFakePipeline(calls)
        return StagedFakePipeline(calls, validation_valid=False)

    app = create_app(
        store=store,
        pipeline_factory=factory,
        avatar_provider=FakeAvatar(calls),
        broll_provider=FakeBroll(calls),
        compositor_fn=make_fake_compositor(calls),
        output_root=str(tmp_path),
    )
    client = TestClient(app)
    job_id = client.post("/api/videos", json=_payload()).json()["job_id"]
    before = client.get(f"/api/videos/{job_id}").json()
    assert before["status"] == "completed"
    final_before = os.path.join(str(tmp_path), job_id, "artifact.txt")
    assert os.path.exists(final_before)

    resp = client.post(f"/api/videos/{job_id}/script/regenerate")
    assert resp.status_code == 422
    assert "unsupported" in str(resp.json()["detail"]).lower()

    after = client.get(f"/api/videos/{job_id}").json()
    assert after["status"] == "completed"  # working video NOT destroyed
    assert after["script"]["full_script"] == before["script"]["full_script"]
    assert os.path.exists(final_before)


def test_storyboard_regenerate_preserves_script(tmp_path):
    calls: list = []
    alt_board = make_storyboard()
    alt_board.scenes[0].narration = "Different narration slice."
    store = JobStore()
    runs = []

    def factory():
        runs.append(1)
        if len(runs) == 1:
            return StagedFakePipeline(calls)
        return StagedFakePipeline(calls, storyboards=[alt_board])

    app = create_app(
        store=store,
        pipeline_factory=factory,
        avatar_provider=FakeAvatar(calls),
        broll_provider=FakeBroll(calls),
        compositor_fn=make_fake_compositor(calls),
        output_root=str(tmp_path),
    )
    client = TestClient(app)
    job_id = client.post("/api/videos", json=_payload()).json()["job_id"]
    before = client.get(f"/api/videos/{job_id}").json()

    resp = client.post(f"/api/videos/{job_id}/storyboard/regenerate")
    assert resp.status_code == 202
    job = resp.json()
    assert job["status"] == "completed"
    assert job["script"] == before["script"]  # script untouched
    assert job["storyboard"]["scenes"][0]["narration"] == (
        "Different narration slice."
    )
    assert "compositor.render" in calls


def test_retry_resumes_from_failed_avatar_without_regenerating_voice(tmp_path):
    calls: list = []
    attempts = []

    class FlakyAvatar:
        def generate(self, audio_path, output_path):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("HeyGen generation failed with status 'failed'")
            calls.append("avatar.generate")
            with open(output_path, "w") as f:
                f.write("FAKE_AVATAR_FOR_TESTS")
            return output_path

    store = JobStore()
    app = create_app(
        store=store,
        pipeline_factory=lambda: StagedFakePipeline(calls),
        avatar_provider=FlakyAvatar(),
        broll_provider=FakeBroll(calls),
        compositor_fn=make_fake_compositor(calls),
        output_root=str(tmp_path),
    )
    client = TestClient(app)
    job_id = client.post("/api/videos", json=_payload()).json()["job_id"]
    failed = client.get(f"/api/videos/{job_id}").json()
    assert failed["status"] == "failed"
    voice_runs = calls.count("stage.create_voice")
    assert voice_runs == 1

    resp = client.post(f"/api/videos/{job_id}/retry")
    assert resp.status_code == 202
    job = resp.json()
    assert job["status"] == "completed"
    # Voice NOT regenerated (paid stage reused); avatar retried.
    assert calls.count("stage.create_voice") == 1
    assert len(attempts) == 2


def test_regen_retry_unknown_job_404(tmp_path):
    calls: list = []
    client, _, _ = _completed_client(tmp_path, calls)
    assert client.post("/api/videos/nope/script/regenerate").status_code == 404
    assert client.post("/api/videos/nope/storyboard/regenerate").status_code == 404
    assert client.post("/api/videos/nope/retry").status_code == 404
    assert client.get("/api/videos/nope/file").status_code == 404


def test_regen_while_processing_409(tmp_path):
    calls: list = []
    store = JobStore()
    app = create_app(store=store, output_root=str(tmp_path),
                     **_wiring(calls))
    client = TestClient(app)
    job_id = client.post("/api/videos", json=_payload()).json()["job_id"]
    store.set_status(job_id, "processing")  # simulate a running job
    assert client.post(f"/api/videos/{job_id}/script/regenerate").status_code == 409
    assert client.post(f"/api/videos/{job_id}/storyboard/regenerate").status_code == 409
    assert client.post(f"/api/videos/{job_id}/retry").status_code == 409


def test_file_serves_mp4_with_attachment(tmp_path):
    store = JobStore()
    app = create_app(
        store=store,
        pipeline_factory=lambda: StagedFakePipeline([]),
        avatar_provider=None,
        broll_provider=None,
        compositor_fn=None,
        output_root=str(tmp_path),
    )
    client = TestClient(app)
    job_id = client.post("/api/videos", json=_payload()).json()["job_id"]
    # Simulate a real completed render: final.mp4 on disk + stored path.
    final = os.path.join(str(tmp_path), job_id, "final.mp4")
    with open(final, "wb") as f:
        f.write(b"FAKE_MP4_BYTES")
    store.update(
        job_id,
        status="completed",
        video_url=f"/api/videos/{job_id}/file",
        artifact_path=final,
        current_stage="completed",
    )
    resp = client.get(f"/api/videos/{job_id}/file")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert "attachment" in resp.headers["content-disposition"]
    assert f"{job_id}_final.mp4" in resp.headers["content-disposition"]
    assert resp.content == b"FAKE_MP4_BYTES"


def test_file_409_when_not_ready_or_missing(tmp_path):
    calls: list = []
    client, store, job_id = _completed_client(tmp_path, calls)
    # Completed but artifact missing on disk.
    store.update(job_id, artifact_path=os.path.join(str(tmp_path), "gone.mp4"))
    assert client.get(f"/api/videos/{job_id}/file").status_code == 409


def test_file_never_serves_arbitrary_paths(tmp_path):
    calls: list = []
    client, store, job_id = _completed_client(tmp_path, calls)
    store.update(
        job_id,
        artifact_path="/etc/passwd",
        video_url=f"/api/videos/{job_id}/file",
    )
    assert client.get(f"/api/videos/{job_id}/file").status_code == 409


def test_job_isolation_for_regen(tmp_path):
    calls: list = []
    client, _, _ = _completed_client(tmp_path, calls)
    j1 = client.post("/api/videos", json=_payload(topic="One")).json()["job_id"]
    j2 = client.post("/api/videos", json=_payload(topic="Two")).json()["job_id"]
    assert j1 != j2
    assert client.get(f"/api/videos/{j1}").json()["request"]["topic"] == "One"
    assert client.get(f"/api/videos/{j2}").json()["request"]["topic"] == "Two"
