"""API contract tests: HTTP validation + enqueue behaviour only.

Does NOT run the pipeline lifecycle. backend.worker.run_job is patched to a
no-op so POST stays queued and tests are deterministic. Lifecycle (state
transitions, orchestration) is covered in test_worker.py.
"""

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.store import JobStore


def _valid_payload(**overrides):
    body = {
        "topic": "Why markets move",
        "key_message": "Point one. Point two.",
        "language": "English",
        "duration_seconds": 60,
    }
    body.update(overrides)
    return body


@pytest.fixture()
def client_noop_worker():
    store = JobStore()
    app = create_app(store=store, pipeline_factory=MagicMock())
    with patch("backend.worker.run_job") as mock_run:
        # TestClient runs BackgroundTasks synchronously; mock keeps job queued.
        test_client = TestClient(app)
        yield test_client, store, mock_run


def test_post_valid_returns_queued_and_schedules_worker(client_noop_worker):
    client, store, mock_run = client_noop_worker
    resp = client.post("/api/videos", json=_valid_payload())
    assert resp.status_code == 202
    body = resp.json()
    assert "job_id" in body and body["job_id"]
    assert body["status"] == "queued"
    assert mock_run.called
    job = client.get(f"/api/videos/{body['job_id']}").json()
    assert job["status"] == "queued"
    assert job["request"]["topic"] == "Why markets move"
    assert job["video_url"] is None
    assert job["error"] is None


def test_post_missing_key_message_rejected(client_noop_worker):
    client, _, _ = client_noop_worker
    body = _valid_payload()
    del body["key_message"]
    resp = client.post("/api/videos", json=body)
    assert resp.status_code == 422


def test_post_blank_key_message_rejected(client_noop_worker):
    client, _, _ = client_noop_worker
    resp = client.post("/api/videos", json=_valid_payload(key_message="   "))
    assert resp.status_code == 422


def test_post_blank_topic_rejected(client_noop_worker):
    client, _, _ = client_noop_worker
    resp = client.post("/api/videos", json=_valid_payload(topic="  "))
    assert resp.status_code == 422


def test_post_bad_duration_rejected(client_noop_worker):
    client, _, _ = client_noop_worker
    resp = client.post("/api/videos", json=_valid_payload(duration_seconds=0))
    assert resp.status_code == 422
    resp = client.post("/api/videos", json=_valid_payload(duration_seconds=9999))
    assert resp.status_code == 422


def test_get_unknown_job_404(client_noop_worker):
    client, _, _ = client_noop_worker
    assert client.get("/api/videos/doesnotexist").status_code == 404


def test_file_unknown_job_404(client_noop_worker):
    client, _, _ = client_noop_worker
    assert client.get("/api/videos/doesnotexist/file").status_code == 404


def test_file_before_completion_409(client_noop_worker):
    client, _, _ = client_noop_worker
    resp = client.post("/api/videos", json=_valid_payload())
    job_id = resp.json()["job_id"]
    file_resp = client.get(f"/api/videos/{job_id}/file")
    assert file_resp.status_code == 409


def test_completed_without_artifact_file_409(tmp_path):
    """A completed job with no compositor artifact honestly reports 409."""
    from backend import worker as worker_mod

    store = JobStore()
    app = create_app(
        store=store,
        pipeline_factory=lambda: _instant_success_pipeline(tmp_path),
        # Explicit no-provider wiring: production defaults are real and
        # would fail without credentials; this test needs the legacy
        # audio/script-only path.
        avatar_provider=None,
        broll_provider=None,
        compositor_fn=None,
        output_root=str(tmp_path),
    )
    client = TestClient(app)
    resp = client.post("/api/videos", json=_valid_payload())
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    # BackgroundTasks ran synchronously with the real (fake) pipeline.
    job = client.get(f"/api/videos/{job_id}").json()
    assert job["status"] == "completed"
    assert job["video_url"] is None
    assert client.get(f"/api/videos/{job_id}/file").status_code == 409
    assert worker_mod  # silence lint


def _instant_success_pipeline(tmp_path):
    import sys

    sys.path.insert(0, ".")
    from tests.fakes import FakePipeline

    calls: list = []
    return FakePipeline(calls)
