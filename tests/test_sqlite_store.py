"""SQLite-backed job store: persistence across store/process recreation.

All databases live under tmp_path — production (/data/jobstore.db) is never
touched. No network, no providers, no paid calls.
"""

import os

import pytest

from backend.sqlite_store import SQLiteJobStore
from backend.store import COMPLETED, FAILED, PROCESSING


def _request():
    return {
        "topic": "T",
        "key_message": "Point one. Point two.",
        "language": "English",
        "duration_seconds": 60,
    }


def _db(tmp_path, name="jobs.db"):
    return SQLiteJobStore(str(tmp_path / name))


def test_job_survives_store_recreation(tmp_path):
    store = _db(tmp_path)
    job = store.create(_request())
    store.update(
        job["job_id"],
        status=PROCESSING,
        script={"full_script": "Hook. Point one."},
        storyboard={"scenes": [{"scene_id": 1}]},
        artifact_path="/data/output/abc/voice.mp3",
        error="boom",
        error_type="CompositorError",
        failed_stage="composition",
    )
    store.set_stage(job["job_id"], "composition", "Rendering final video...")
    path = store.path
    store.close()

    reopened = SQLiteJobStore(path)
    try:
        revived = reopened.get(job["job_id"])
    finally:
        reopened.close()
    assert revived is not None
    assert revived["job_id"] == job["job_id"]
    assert revived["status"] == PROCESSING
    assert revived["current_stage"] == "composition"
    assert revived["status_detail"] == "Rendering final video..."
    assert revived["request"] == _request()
    assert revived["script"] == {"full_script": "Hook. Point one."}
    assert revived["storyboard"] == {"scenes": [{"scene_id": 1}]}
    assert revived["artifact_path"] == "/data/output/abc/voice.mp3"
    assert revived["error"] == "boom"
    assert revived["failed_stage"] == "composition"
    assert revived["created_at"] == job["created_at"]


def test_idempotency_survives_recreation(tmp_path):
    store = _db(tmp_path)
    job, outcome = store.create_guarded(_request(), "key-1")
    assert outcome == "created"
    path = store.path
    store.close()

    reopened = SQLiteJobStore(path)
    try:
        same, outcome = reopened.create_guarded(_request(), "key-1")
        assert outcome == "duplicate"
        assert same["job_id"] == job["job_id"]
        # A different key is still blocked by the active job, and leaves
        # no record behind.
        other, outcome = reopened.create_guarded(_request(), "key-2")
        assert outcome == "busy"
        assert other is None
        assert reopened.get("nope") is None
    finally:
        reopened.close()


def test_expired_idempotency_pruned_after_recreation(tmp_path):
    store = _db(tmp_path)
    job, outcome = store.create_guarded(_request(), "key-old")
    assert outcome == "created"
    # Age the idempotency record past its TTL directly.
    store._conn.execute(
        "UPDATE idempotency SET expires_at = 0 WHERE key = ?", ("key-old",)
    )
    store._conn.commit()
    path = store.path
    store.close()

    reopened = SQLiteJobStore(path)
    try:
        # Terminal job first so the active-job check does not interfere.
        reopened.set_status(job["job_id"], COMPLETED)
        fresh, outcome = reopened.create_guarded(_request(), "key-old")
        assert outcome == "created"
        assert fresh["job_id"] != job["job_id"]
    finally:
        reopened.close()


def test_active_job_invariant_survives_recreation(tmp_path):
    store = _db(tmp_path)
    job = store.create(_request())
    store.set_status(job["job_id"], PROCESSING)
    assert store.has_active_job() is True
    path = store.path
    store.close()

    reopened = SQLiteJobStore(path)
    try:
        assert reopened.has_active_job() is True
        assert reopened.all_jobs()[0]["status"] == PROCESSING
        other, outcome = reopened.create_guarded(_request(), "key-x")
        assert outcome == "busy"
        # Completing the job releases the invariant across the restart.
        reopened.set_status(job["job_id"], COMPLETED)
        assert reopened.has_active_job() is False
        fresh, outcome = reopened.create_guarded(_request(), "key-x")
        assert outcome == "created"
    finally:
        reopened.close()


def test_stale_processing_job_recovered_after_restart(tmp_path):
    from backend.worker import recover_stale_jobs

    store = _db(tmp_path)
    job = store.create(_request())
    assert store.acquire_lock(job["job_id"], "retry-abc") is True
    store.set_status(job["job_id"], PROCESSING)
    store.set_stage(job["job_id"], "composition", "Rendering final video...")
    path = store.path
    store.close()  # lock + processing state persist (the bug being fixed)

    reopened = SQLiteJobStore(path)
    try:
        # A stale lock must not block recovery bookkeeping.
        recovered = recover_stale_jobs(reopened, str(tmp_path))
        assert recovered == [job["job_id"]]
        failed = reopened.get(job["job_id"])
        assert failed["status"] == FAILED
        assert failed["error_type"] == "RestartRecovery"
        assert failed["failed_stage"] == "composition"
        assert failed["locked_by"] is None  # locks do not stay stuck
        # The job is retryable again after recovery.
        assert reopened.acquire_lock(job["job_id"], "retry-abc") is True
        reopened.release_lock(job["job_id"], "retry-abc")
    finally:
        reopened.close()


def test_locks_do_not_remain_stuck_after_restart(tmp_path):
    store = _db(tmp_path)
    job = store.create(_request())
    assert store.acquire_lock(job["job_id"], "owner-1") is True
    path = store.path
    store.close()

    reopened = SQLiteJobStore(path)
    try:
        # A different owner is still fenced out (lock persisted)...
        assert reopened.acquire_lock(job["job_id"], "owner-2") is False
        # ...until explicitly released, e.g. by startup recovery.
        reopened.force_release_lock(job["job_id"])
        assert reopened.acquire_lock(job["job_id"], "owner-2") is True
        reopened.release_lock(job["job_id"], "owner-2")
        assert reopened.get(job["job_id"])["locked_by"] is None
    finally:
        reopened.close()


def test_stores_are_isolated_per_path(tmp_path):
    first = _db(tmp_path, "a.db")
    second = _db(tmp_path, "b.db")
    job = first.create(_request())
    try:
        assert second.get(job["job_id"]) is None
        assert second.all_jobs() == []
        assert os.path.exists(first.path) and os.path.exists(second.path)
        assert first.path != second.path
    finally:
        first.close()
        second.close()


def test_create_app_uses_sqlite_only_when_configured(tmp_path, monkeypatch):
    from backend.app import create_app
    from backend.store import JobStore

    monkeypatch.delenv("JOBSTORE_PATH", raising=False)
    plain = create_app()
    assert isinstance(plain.state.store, JobStore)
    assert not isinstance(plain.state.store, SQLiteJobStore)

    db_path = str(tmp_path / "prod" / "jobstore.db")
    monkeypatch.setenv("JOBSTORE_PATH", db_path)
    app = create_app()
    try:
        assert isinstance(app.state.store, SQLiteJobStore)
        assert app.state.store.path == db_path
        assert os.path.exists(db_path)
    finally:
        app.state.store.close()
