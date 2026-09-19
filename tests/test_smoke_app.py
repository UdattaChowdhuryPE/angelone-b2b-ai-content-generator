"""Smoke-backend UI contract tests (no browser automation).

Covers the exact contract the Streamlit UI depends on:
POST /api/videos -> 202 + job_id, poll GET -> completed with
video_url=None + script/storyboard summary, and the test-only failure
sentinel -> failed. Also guards that smoke fakes make no external calls
and production backend/app.py was not switched to fake mode.
"""

import pathlib
import subprocess

import requests
from fastapi.testclient import TestClient

from backend.smoke_app import app as smoke_app
from backend.smoke_fakes import (
    SMOKE_FAILURE_SENTINEL,
    SMOKE_TAKEAWAY,
    SmokeFakePipeline,
)


def _ui_payload(**overrides):
    body = {
        "topic": "Smoke topic",
        "key_message": "Smoke key point one. Smoke key point two.",
        "language": "English",
        "duration_seconds": 30,
    }
    body.update(overrides)
    return body


def test_smoke_success_flow_matches_ui_expectations():
    client = TestClient(smoke_app)
    resp = client.post("/api/videos", json=_ui_payload())
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]
    assert resp.json()["status"] in ("queued", "processing", "completed")
    # BackgroundTasks run synchronously under TestClient: already terminal.
    job = client.get(f"/api/videos/{job_id}").json()
    assert job["status"] == "completed"
    assert job["video_url"] is None
    assert job["error"] is None
    result = job["result"] or {}
    assert "Smoke Test Video" in (result.get("script_preview") or "") or \
        "backend integration smoke test" in (result.get("script_preview") or "")
    assert SMOKE_TAKEAWAY in (result.get("script_preview") or "")
    assert result.get("num_scenes") == 2
    # UI contract: video_url null must be falsy so Streamlit shows caption, not player.
    assert not job.get("video_url")
    # File endpoint honestly reports no artifact (never a fake mp4).
    assert client.get(f"/api/videos/{job_id}/file").status_code == 409


def test_smoke_failure_sentinel_goes_failed():
    client = TestClient(smoke_app)
    resp = client.post("/api/videos", json=_ui_payload(key_message=SMOKE_FAILURE_SENTINEL))
    assert resp.status_code == 202
    job = client.get(f"/api/videos/{resp.json()['job_id']}").json()
    assert job["status"] == "failed"
    assert "Smoke test induced failure" in (job["error"] or "")
    assert job["video_url"] is None


def test_smoke_pipeline_writes_no_media_and_needs_no_keys(tmp_path, monkeypatch):
    for key in (
        "OPENAI_API_KEY", "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID",
        "HEYGEN_API_KEY", "HEYGEN_AVATAR_ID", "HF_API_KEY_ID", "HF_API_KEY_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    def _no_net(*a, **k):
        raise AssertionError("network call blocked")

    monkeypatch.setattr(requests, "post", _no_net)
    monkeypatch.setattr(requests, "get", _no_net)
    monkeypatch.setattr(subprocess, "run", _no_net)
    monkeypatch.chdir(tmp_path)

    from backend.store import JobStore
    from backend.worker import run_job

    store = JobStore()
    req = _ui_payload()
    job = store.create(req)
    final = run_job(
        job["job_id"], req, store,
        pipeline_factory=lambda: SmokeFakePipeline(delay_seconds=0),
        avatar_provider=None,  # explicit skip (defaults wire real providers)
        broll_provider=None,
        compositor_fn=None,
        output_root="output",
    )
    assert final["status"] == "completed"
    assert final["video_url"] is None
    assert (tmp_path / "output" / job["job_id"] / "result.json").exists()
    assert list(tmp_path.rglob("*.mp4")) == []


def test_smoke_modules_make_no_external_calls():
    import re

    root = pathlib.Path(__file__).resolve().parent.parent
    for name in ("backend/smoke_fakes.py", "backend/smoke_app.py"):
        src = (root / name).read_text()
        # Strip docstrings/comments so prose mentions don't trip the guard;
        # what matters is no import/call of external providers.
        code = re.sub(r'""".*?"""', "", src, flags=re.DOTALL)
        code = re.sub(r"'''.*?'''", "", code, flags=re.DOTALL)
        code = "\n".join(
            line.split("#")[0] for line in code.splitlines()
        ).lower()
        for banned in ("openai", "elevenlabs", "heygen", "higgsfield",
                       "ffmpeg", "subprocess", "os.getenv", "api_key",
                       "requests.post", "requests.get", "httpx",
                       "urllib", "llmprovider", "elevenlabsvoiceprovider"):
            assert banned not in code, f"{name} must not reference {banned}"
    fakes_src = (root / "backend/smoke_fakes.py").read_text()
    assert "from tests" not in fakes_src and "import tests" not in fakes_src


def test_production_backend_not_switched_to_fake_mode():
    root = pathlib.Path(__file__).resolve().parent.parent
    prod = (root / "backend/app.py").read_text()
    assert "smoke" not in prod.lower()
    assert "SMOKE_TEST_FAILURE" not in prod
    assert "FakePipeline" not in prod
