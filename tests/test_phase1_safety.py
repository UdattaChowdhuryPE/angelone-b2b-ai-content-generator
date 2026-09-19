"""Phase 1 production-safety regression tests (mocks/fakes only).

Covers: async regen/retry enqueue, create idempotency, input/cost guards,
ffmpeg/ffprobe timeouts, configurable OUTPUT_DIR, /healthz + startup
recovery, single-concurrency guard. No network, no creds, no paid calls.
"""

import os
import subprocess
import threading
import time

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.store import JobStore
from tests.fakes import (
    FakeAvatar,
    FakeBroll,
    StagedFakePipeline,
    make_fake_compositor,
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


def _completed_job(client, payload, timeout=30):
    job_id = client.post("/api/videos", json=payload).json()["job_id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = client.get(f"/api/videos/{job_id}").json()
        if job["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    job = client.get(f"/api/videos/{job_id}").json()
    assert job["status"] == "completed", job
    return job_id


class _StubBackground:
    """Collects BackgroundTasks without running them (proves enqueue)."""

    def __init__(self):
        self.tasks = []

    def add_task(self, fn, *args, **kwargs):
        self.tasks.append((fn, args, kwargs))


def _route_endpoint(app, path):
    for route in app.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route missing: {path}")


# --- 1. Async regenerate / retry -------------------------------------------

def test_script_regen_enqueues_without_running_paid_work(tmp_path):
    calls: list = []
    store = JobStore()
    app = create_app(store=store, output_root=str(tmp_path), **_wiring(calls))
    client = TestClient(app)
    job_id = _completed_job(client, _payload())
    paid_before = list(calls)

    endpoint = _route_endpoint(app, "/api/videos/{job_id}/script/regenerate")
    bg = _StubBackground()
    result = endpoint(job_id, bg)  # must return without doing paid work
    assert result["status"] == "processing"
    assert len(bg.tasks) == 1
    assert calls == paid_before  # nothing ran inline

    fn, args, kwargs = bg.tasks[0]
    fn(*args, **kwargs)  # run the background work manually
    job = client.get(f"/api/videos/{job_id}").json()
    assert job["status"] == "completed"
    assert store.get(job_id)["locked_by"] is None


def test_storyboard_regen_enqueues_without_running_paid_work(tmp_path):
    calls: list = []
    store = JobStore()
    app = create_app(store=store, output_root=str(tmp_path), **_wiring(calls))
    client = TestClient(app)
    job_id = _completed_job(client, _payload())
    paid_before = list(calls)

    endpoint = _route_endpoint(app, "/api/videos/{job_id}/storyboard/regenerate")
    bg = _StubBackground()
    result = endpoint(job_id, bg)
    assert result["status"] == "processing"
    assert len(bg.tasks) == 1
    assert calls == paid_before

    fn, args, kwargs = bg.tasks[0]
    fn(*args, **kwargs)
    job = client.get(f"/api/videos/{job_id}").json()
    assert job["status"] == "completed"
    assert store.get(job_id)["locked_by"] is None


def test_retry_enqueues_and_reuses_voice_artifact(tmp_path):
    calls: list = []
    attempts = []

    class FlakyAvatar:
        def generate(self, audio_path, output_path):
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("HeyGen generation failed")
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
    assert client.get(f"/api/videos/{job_id}").json()["status"] == "failed"
    assert calls.count("stage.create_voice") == 1

    endpoint = _route_endpoint(app, "/api/videos/{job_id}/retry")
    bg = _StubBackground()
    result = endpoint(job_id, bg)
    assert result["status"] == "processing"
    assert len(bg.tasks) == 1
    assert calls.count("stage.create_voice") == 1  # not rerun inline

    fn, args, kwargs = bg.tasks[0]
    fn(*args, **kwargs)
    job = client.get(f"/api/videos/{job_id}").json()
    assert job["status"] == "completed"
    assert calls.count("stage.create_voice") == 1  # paid voice reused
    assert len(attempts) == 2


# --- 2. Idempotency ----------------------------------------------------------

def test_same_key_returns_same_job_without_new_work(tmp_path):
    store = JobStore()
    app = create_app(
        store=store,
        pipeline_factory=lambda: StagedFakePipeline([]),
        avatar_provider=FakeAvatar([]),
        broll_provider=FakeBroll([]),
        compositor_fn=make_fake_compositor([]),
        output_root=str(tmp_path),
    )
    client = TestClient(app)
    key = "k" * 16
    first = client.post("/api/videos", json=_payload(idempotency_key=key))
    assert first.status_code == 202
    job_id = first.json()["job_id"]
    dirs_before = sorted(os.listdir(str(tmp_path)))

    second = client.post("/api/videos", json=_payload(idempotency_key=key))
    assert second.status_code == 200
    assert second.json()["job_id"] == job_id
    assert sorted(os.listdir(str(tmp_path))) == dirs_before  # no 2nd job dir


def test_duplicate_key_invokes_no_pipeline(tmp_path):
    made: list = []
    store = JobStore()

    def factory():
        made.append(1)
        return StagedFakePipeline([])

    app = create_app(
        store=store,
        pipeline_factory=factory,
        avatar_provider=FakeAvatar([]),
        broll_provider=FakeBroll([]),
        compositor_fn=make_fake_compositor([]),
        output_root=str(tmp_path),
    )
    client = TestClient(app)
    key = "d" * 32
    client.post("/api/videos", json=_payload(idempotency_key=key))
    assert len(made) == 1  # single background run (drained by TestClient)
    client.post("/api/videos", json=_payload(idempotency_key=key))
    assert len(made) == 1  # duplicate enqueued nothing


def test_different_keys_create_different_jobs(tmp_path):
    calls: list = []
    store = JobStore()
    app = create_app(store=store, output_root=str(tmp_path), **_wiring(calls))
    client = TestClient(app)
    j1 = client.post("/api/videos", json=_payload(idempotency_key="a" * 16)).json()["job_id"]
    j2 = client.post("/api/videos", json=_payload(idempotency_key="b" * 16)).json()["job_id"]
    assert j1 != j2


def test_expired_idempotency_key_allows_new_job():
    store = JobStore()
    key = "e" * 16
    job, outcome = store.create_guarded(_payload(), key)
    assert outcome == "created"
    # Force expiry (white-box, time-based TTL).
    store._idem[key] = (job["job_id"], time.time() - 1)
    # Complete the first job so the concurrency guard does not interfere.
    store.set_status(job["job_id"], "completed")
    job2, outcome2 = store.create_guarded(_payload(), key)
    assert outcome2 == "created"
    assert job2["job_id"] != job["job_id"]


def test_malformed_idempotency_key_rejected(tmp_path):
    calls: list = []
    store = JobStore()
    app = create_app(store=store, output_root=str(tmp_path), **_wiring(calls))
    client = TestClient(app)
    assert client.post("/api/videos", json=_payload(idempotency_key="short")).status_code == 422
    assert client.post("/api/videos", json=_payload(idempotency_key="x" * 65)).status_code == 422
    assert store.all_jobs() == []  # no job record left behind


def test_concurrent_same_key_creates_single_job():
    store = JobStore()
    outcomes = []
    barrier = threading.Barrier(8)

    def attempt():
        barrier.wait()
        _, outcome = store.create_guarded(_payload(), "c" * 16)
        outcomes.append(outcome)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("created") == 1
    assert outcomes.count("duplicate") == 7
    assert len(store.all_jobs()) == 1


def test_concurrent_distinct_keys_single_winner():
    store = JobStore()
    outcomes = []
    barrier = threading.Barrier(8)

    def attempt(i):
        barrier.wait()
        _, outcome = store.create_guarded(_payload(), f"key-{i:012d}")
        outcomes.append(outcome)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert outcomes.count("created") == 1
    assert outcomes.count("busy") == 7
    assert len(store.all_jobs()) == 1


# --- 3. Input / cost guards ---------------------------------------------------

INVALID_BODIES = [
    _payload(topic="x" * 501),
    _payload(key_message="y" * 5001),
    _payload(duration_seconds=90),
    _payload(duration_seconds=300),
    _payload(duration_seconds=0),
    _payload(language="Spanish"),
    _payload(topic="   "),
]


@pytest.mark.parametrize("body", INVALID_BODIES)
def test_invalid_input_rejected_before_any_provider_call(tmp_path, body):
    made: list = []
    store = JobStore()

    def factory():
        made.append(1)
        return StagedFakePipeline([])

    app = create_app(
        store=store,
        pipeline_factory=factory,
        avatar_provider=FakeAvatar([]),
        broll_provider=FakeBroll([]),
        compositor_fn=make_fake_compositor([]),
        output_root=str(tmp_path),
    )
    client = TestClient(app)
    resp = client.post("/api/videos", json=body)
    assert resp.status_code == 422
    assert made == []  # zero provider calls
    assert store.all_jobs() == []  # no job created
    assert os.listdir(str(tmp_path)) == []  # no output directory


@pytest.mark.parametrize("duration", [30, 45, 60])
def test_valid_durations_accepted(tmp_path, duration):
    calls: list = []
    store = JobStore()
    app = create_app(store=store, output_root=str(tmp_path), **_wiring(calls))
    client = TestClient(app)
    resp = client.post("/api/videos", json=_payload(duration_seconds=duration))
    assert resp.status_code == 202


def test_exact_boundaries_accepted(tmp_path):
    calls: list = []
    store = JobStore()
    app = create_app(store=store, output_root=str(tmp_path), **_wiring(calls))
    client = TestClient(app)
    resp = client.post(
        "/api/videos",
        json=_payload(topic="t" * 500, key_message="k" * 5000),
    )
    assert resp.status_code == 202


# --- 4. FFmpeg / ffprobe timeouts ----------------------------------------------

def test_timeout_constants():
    from video import compositor as comp

    assert comp.FFMPEG_TIMEOUT_S == 600
    assert comp.FFPROBE_TIMEOUT_S == 60


def test_ffprobe_timeout_raises_validation_error(monkeypatch):
    from video import compositor as comp

    def _hang(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=60)

    monkeypatch.setattr(comp.subprocess, "run", _hang)
    with pytest.raises(comp.Mp4ValidationError, match="timed out"):
        comp.probe_media("whatever.mp4")


def test_probe_timeout_propagates_timeout_value(monkeypatch):
    from video import compositor as comp

    seen = {}

    def _hang(*args, **kwargs):
        seen.update(kwargs)
        raise subprocess.TimeoutExpired(cmd="ffprobe", timeout=kwargs.get("timeout"))

    monkeypatch.setattr(comp.subprocess, "run", _hang)
    with pytest.raises(comp.Mp4ValidationError):
        comp.probe_media("whatever.mp4")
    assert seen.get("timeout") == 60


def test_ffmpeg_timeout_cleans_partial_and_fails(tmp_path, monkeypatch):
    from video import compositor as comp
    from tests.fakes import make_storyboard

    job_dir = str(tmp_path / "job")
    os.makedirs(job_dir)
    avatar = os.path.join(job_dir, "avatar.mp4")
    with open(avatar, "wb") as f:
        f.write(b"FAKE_AVATAR")

    monkeypatch.setattr(
        comp,
        "probe_media",
        lambda path, timeout=60: {
            "streams": [{"codec_type": "video"}, {"codec_type": "audio"}],
            "format": {"duration": "20.0"},
        },
    )
    monkeypatch.setattr(
        comp, "render_all_cards", lambda segments, cap_dir, font_path=None: []
    )
    seen = {}

    def _hang(cmd, **kwargs):
        seen.update(kwargs)
        # Simulate a leftover .partial from the killed render.
        with open(cmd[-1], "wb") as f:
            f.write(b"partial")
        raise subprocess.TimeoutExpired(cmd="ffmpeg", timeout=kwargs.get("timeout"))

    monkeypatch.setattr(comp.subprocess, "run", _hang)
    out = os.path.join(job_dir, "final.mp4")
    with pytest.raises(comp.CompositorError, match="timed out"):
        comp.compose_final(
            audio_path=avatar,
            avatar_path=avatar,
            broll={},
            storyboard=make_storyboard(),
            output_path=out,
            job_dir=job_dir,
            script_text="Hook. Point one. Takeaway.",
        )
    assert seen.get("timeout") == 600
    assert not os.path.exists(out)  # never shipped as complete
    assert not os.path.exists(out + ".partial")  # cleaned up


# --- 5. Configurable OUTPUT_DIR -------------------------------------------------

def test_artifacts_written_under_custom_output_root(tmp_path):
    calls: list = []
    custom = tmp_path / "custom-output"
    store = JobStore()
    app = create_app(store=store, output_root=str(custom), **_wiring(calls))
    client = TestClient(app)
    job_id = _completed_job(client, _payload())
    assert os.path.exists(os.path.join(str(custom), job_id, "script.json"))
    assert os.path.exists(os.path.join(str(custom), job_id, "result.json"))


def test_output_dir_env_respected_by_default_app(monkeypatch, tmp_path):
    monkeypatch.setenv("OUTPUT_DIR", str(tmp_path / "env-output"))
    app = create_app(
        pipeline_factory=lambda: StagedFakePipeline([]),
        avatar_provider=None,
        broll_provider=None,
        compositor_fn=None,
    )
    assert app.state.output_root == str(tmp_path / "env-output")


def test_preflight_reports_configured_output_dir(tmp_path):
    import backend.preflight as pf

    target = tmp_path / "vol" / "output"
    result = pf.check_output_writable(str(target))
    assert result["status"] == "PASS"
    assert result["detail"] == os.path.abspath(str(target))


# --- 6. /healthz + startup recovery ----------------------------------------------

def test_healthz_is_provider_free(tmp_path):
    made: list = []

    def factory():
        made.append(1)
        raise AssertionError("provider must not be constructed")

    store = JobStore()
    app = create_app(
        store=store,
        pipeline_factory=factory,
        avatar_provider=None,
        broll_provider=None,
        compositor_fn=None,
        output_root=str(tmp_path),
    )
    client = TestClient(app)
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert made == []


def test_get_endpoints_never_trigger_providers(tmp_path):
    made: list = []

    def factory():
        made.append(1)
        return StagedFakePipeline([])

    store = JobStore()
    app = create_app(
        store=store,
        pipeline_factory=factory,
        avatar_provider=FakeAvatar([]),
        broll_provider=FakeBroll([]),
        compositor_fn=make_fake_compositor([]),
        output_root=str(tmp_path),
    )
    client = TestClient(app)
    job_id = _completed_job(client, _payload())
    made.clear()
    client.get(f"/api/videos/{job_id}")
    client.get("/api/preflight")
    client.get("/healthz")
    client.get(f"/api/videos/{job_id}/file")
    assert made == []


def test_stale_processing_job_recovered_on_startup(tmp_path):
    import json

    from tests.fakes import make_script, make_storyboard

    calls: list = []
    store = JobStore()
    seed = store.create(_payload())
    job_id = seed["job_id"]
    job_dir = os.path.join(str(tmp_path), job_id)
    os.makedirs(job_dir)
    voice = os.path.join(job_dir, "voice.mp3")
    with open(voice, "wb") as f:
        f.write(b"FAKE_AUDIO")
    # Persisted upstream artifacts so post-recovery retry can resume.
    with open(os.path.join(job_dir, "script.json"), "w") as f:
        json.dump(make_script().model_dump(), f)
    with open(os.path.join(job_dir, "storyboard.json"), "w") as f:
        json.dump(make_storyboard().model_dump(), f)
    store.update(job_id, current_stage="voice")
    store.set_status(job_id, "processing")
    store.update(job_id, locked_by="retry-old")

    # Boot with fakes: recovery must not invoke any provider.
    app = create_app(store=store, output_root=str(tmp_path), **_wiring(calls))
    client = TestClient(app)
    assert calls == []

    job = client.get(f"/api/videos/{job_id}").json()
    assert job["status"] == "failed"
    assert job["failed_stage"] == "voice"
    assert "restarted" in job["error"].lower()
    assert "NOT automatically rerun" in job["error"]
    assert os.path.exists(voice)  # artifacts preserved
    assert store.get(job_id)["locked_by"] is None  # lock cleared

    # Operator retry resumes with fakes and reuses the preserved voice.
    resp = client.post(f"/api/videos/{job_id}/retry")
    assert resp.status_code == 202
    deadline = time.time() + 30
    while time.time() < deadline:
        done = client.get(f"/api/videos/{job_id}").json()
        if done["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert done["status"] == "completed", done
    assert calls.count("stage.create_voice") == 0  # voice.mp3 reused


# --- 7. Single-concurrency guard ---------------------------------------------------

def test_fresh_key_while_active_rejected_with_409(tmp_path):
    made: list = []
    store = JobStore()

    def factory():
        made.append(1)
        return StagedFakePipeline([])

    app = create_app(
        store=store,
        pipeline_factory=factory,
        avatar_provider=FakeAvatar([]),
        broll_provider=FakeBroll([]),
        compositor_fn=make_fake_compositor([]),
        output_root=str(tmp_path),
    )
    client = TestClient(app)
    seed = store.create(_payload())
    store.set_status(seed["job_id"], "processing")  # simulate active job
    n_before = len(store.all_jobs())

    resp = client.post("/api/videos", json=_payload(idempotency_key="n" * 16))
    assert resp.status_code == 409
    assert "currently running" in resp.json()["detail"]
    assert len(store.all_jobs()) == n_before  # no record left behind
    assert made == []  # no provider call


def test_duplicate_key_for_active_job_returns_200(tmp_path):
    from unittest.mock import patch

    store = JobStore()
    app = create_app(
        store=store,
        pipeline_factory=lambda: StagedFakePipeline([]),
        avatar_provider=None,
        broll_provider=None,
        compositor_fn=None,
        output_root=str(tmp_path),
    )
    with patch("backend.worker.run_job"):  # keep job queued => active
        client = TestClient(app)
        key = "q" * 16
        first = client.post("/api/videos", json=_payload(idempotency_key=key))
        assert first.status_code == 202
        second = client.post("/api/videos", json=_payload(idempotency_key=key))
        assert second.status_code == 200
        assert second.json()["job_id"] == first.json()["job_id"]


def test_no_active_job_returns_202(tmp_path):
    calls: list = []
    store = JobStore()
    app = create_app(store=store, output_root=str(tmp_path), **_wiring(calls))
    client = TestClient(app)
    resp = client.post("/api/videos", json=_payload(idempotency_key="z" * 16))
    assert resp.status_code == 202


def test_regen_endpoints_still_guard_concurrent_mutation(tmp_path):
    calls: list = []
    store = JobStore()
    app = create_app(store=store, output_root=str(tmp_path), **_wiring(calls))
    client = TestClient(app)
    job_id = _completed_job(client, _payload())
    store.set_status(job_id, "processing")
    assert client.post(f"/api/videos/{job_id}/script/regenerate").status_code == 409
    assert client.post(f"/api/videos/{job_id}/storyboard/regenerate").status_code == 409
    assert client.post(f"/api/videos/{job_id}/retry").status_code == 409
