"""File-artifact serving: text test artifact only, never fake mp4 bytes."""

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.store import JobStore
from tests.fakes import FakeAvatar, FakeBroll, FakePipeline, make_fake_compositor


def _payload():
    return {
        "topic": "T",
        "key_message": "Point one.",
        "language": "English",
        "duration_seconds": 60,
    }


def test_completed_with_compositor_serves_text_artifact(tmp_path):
    calls: list = []
    store = JobStore()
    app = create_app(
        store=store,
        pipeline_factory=lambda: FakePipeline(calls),
        avatar_provider=FakeAvatar(calls),
        broll_provider=FakeBroll(calls),
        compositor_fn=make_fake_compositor(calls),
        output_root=str(tmp_path),
    )
    client = TestClient(app)
    job_id = client.post("/api/videos", json=_payload()).json()["job_id"]
    job = client.get(f"/api/videos/{job_id}").json()
    assert job["status"] == "completed"
    assert job["video_url"] == f"/api/videos/{job_id}/file"
    resp = client.get(f"/api/videos/{job_id}/file")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/plain")
    assert resp.text == "FAKE_ARTIFACT_FOR_TESTS"
    assert "video/mp4" not in resp.headers["content-type"]


def test_frontend_backend_contract():
    """Payload the Streamlit UI builds validates; job response has UI fields."""
    from backend.schemas import CreateVideoRequest, JobStatusResponse

    ui_payload = {
        "topic": "Why markets move",
        "key_message": "Point one. Point two.",
        "language": "English",
        "duration_seconds": 60,
    }
    req = CreateVideoRequest(**ui_payload)  # must not raise
    assert req.key_message

    job = JobStatusResponse(
        job_id="abc123",
        status="completed",
        error=None,
        video_url="/api/videos/abc123/file",
        request=ui_payload,
        result={"script_preview": "x", "num_scenes": 2},
    )
    for field in ("job_id", "status", "video_url", "error"):
        assert field in job.model_dump()
    for state in ("queued", "processing", "completed", "failed"):
        assert JobStatusResponse(job_id="x", status=state).status == state
