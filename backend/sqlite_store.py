"""SQLite-backed persistent job store for production.

Drop-in replacement for backend.store.JobStore: identical method names,
signatures, return shapes, and semantics (atomic create/idempotency,
one-active-job invariant, locking, retry support, stale-job recovery).

Differences from JobStore:
- State lives in a SQLite file (production: /data/jobstore.db on the
  Railway persistent volume) so jobs survive process restarts and
  deployments. Reopening the same path restores every job and every
  unexpired idempotency record.
- Write-ahead logging (WAL) for crash safety; a threading lock serializes
  in-process access exactly like JobStore, and each guarded mutation runs
  inside a single SQLite transaction.

The full job dict is stored as JSON (single source of truth); status,
current_stage, locked_by, and updated_at are mirrored into columns so
active-job checks and recovery scans never parse JSON.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid

from backend.store import (
    IDEMPOTENCY_TTL_S,
    PROCESSING,
    QUEUED,
    STAGE_FAILED,
    STAGE_QUEUED,
    TERMINAL_STATES,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY,
  data TEXT NOT NULL,
  status TEXT NOT NULL,
  current_stage TEXT NOT NULL,
  locked_by TEXT,
  updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
CREATE TABLE IF NOT EXISTS idempotency (
  key TEXT PRIMARY KEY,
  job_id TEXT NOT NULL,
  expires_at REAL NOT NULL
);
"""


def _blank_job(job_id: str, request_dict: dict, now: float) -> dict:
    return {
        "job_id": job_id,
        "status": QUEUED,
        "current_stage": STAGE_QUEUED,
        "status_detail": "Queued — waiting to start.",
        "request": dict(request_dict),
        "error": None,
        "error_type": None,
        "failed_stage": None,
        "operation": None,
        "completed_artifacts": None,
        "skipped_stages": None,
        "video_url": None,
        "artifact_path": None,
        "result": None,
        "script": None,
        "storyboard": None,
        "locked_by": None,
        "created_at": now,
        "updated_at": now,
    }


class SQLiteJobStore:
    """Persistent equivalent of backend.store.JobStore."""

    def __init__(self, path: str):
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    @property
    def path(self) -> str:
        return self._path

    def close(self) -> None:
        """Close the database handle. Reopen with SQLiteJobStore(path)."""
        with self._lock:
            self._conn.close()

    @staticmethod
    def _decode(row) -> dict:
        return json.loads(row[0])

    def _insert_locked(self, job: dict) -> None:
        self._conn.execute(
            "INSERT INTO jobs "
            "(job_id, data, status, current_stage, locked_by, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                job["job_id"],
                json.dumps(job),
                job["status"],
                job["current_stage"],
                job.get("locked_by"),
                job["updated_at"],
            ),
        )

    def _update_locked(self, job: dict) -> None:
        self._conn.execute(
            "UPDATE jobs SET data = ?, status = ?, current_stage = ?, "
            "locked_by = ?, updated_at = ? WHERE job_id = ?",
            (
                json.dumps(job),
                job["status"],
                job["current_stage"],
                job.get("locked_by"),
                job["updated_at"],
                job["job_id"],
            ),
        )

    def _get_locked(self, job_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT data FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        return self._decode(row) if row is not None else None

    def create(self, request_dict: dict) -> dict:
        job_id = uuid.uuid4().hex
        now = time.time()
        job = _blank_job(job_id, request_dict, now)
        with self._lock, self._conn:
            self._insert_locked(job)
        return dict(job)

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            return self._get_locked(job_id)

    def all_jobs(self) -> list[dict]:
        """Copies of every known job (used by startup stale-job recovery)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT data FROM jobs ORDER BY rowid"
            ).fetchall()
            return [self._decode(row) for row in rows]

    def has_active_job(self) -> bool:
        """True when any non-terminal job is processing or lock-held."""
        with self._lock:
            return self._has_active_locked()

    def _has_active_locked(self) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM jobs WHERE status NOT IN (?, ?) "
            "OR locked_by IS NOT NULL LIMIT 1",
            tuple(sorted(TERMINAL_STATES)),
        ).fetchone()
        return row is not None

    def create_guarded(
        self, request_dict: dict, idempotency_key: str | None = None
    ) -> tuple[dict | None, str]:
        """Atomically dedupe-or-create under a single lock hold.

        Returns (job, outcome) where outcome is one of:
        - "created": new job minted (caller must enqueue background work).
        - "duplicate": unexpired idempotency hit — existing job returned,
          caller must NOT create work or invoke providers.
        - "busy": another non-terminal job is active — caller must reject
          with 409 and must NOT leave a job record behind.
        """
        now = time.time()
        with self._lock, self._conn:
            # Bounded retention: prune expired idempotency entries on write.
            self._conn.execute(
                "DELETE FROM idempotency WHERE expires_at <= ?", (now,)
            )
            if idempotency_key:
                hit = self._conn.execute(
                    "SELECT job_id, expires_at FROM idempotency WHERE key = ?",
                    (idempotency_key,),
                ).fetchone()
                if hit is not None:
                    job_id, exp = hit
                    job = self._get_locked(job_id)
                    if job is not None and exp > now:
                        return dict(job), "duplicate"
                    # Stale mapping (job gone or expired) — drop it.
                    self._conn.execute(
                        "DELETE FROM idempotency WHERE key = ?",
                        (idempotency_key,),
                    )
            if self._has_active_locked():
                return None, "busy"
            job_id = uuid.uuid4().hex
            job = _blank_job(job_id, request_dict, now)
            self._insert_locked(job)
            if idempotency_key:
                self._conn.execute(
                    "INSERT INTO idempotency (key, job_id, expires_at) "
                    "VALUES (?, ?, ?)",
                    (idempotency_key, job_id, now + IDEMPOTENCY_TTL_S),
                )
            return dict(job), "created"

    def update(self, job_id: str, **fields) -> dict | None:
        with self._lock, self._conn:
            job = self._get_locked(job_id)
            if job is None:
                return None
            job.update(fields)
            job["updated_at"] = time.time()
            self._update_locked(job)
            return dict(job)

    def set_status(self, job_id: str, status: str) -> dict | None:
        return self.update(job_id, status=status)

    def set_stage(
        self,
        job_id: str,
        stage: str,
        detail: str | None = None,
    ) -> dict | None:
        fields: dict = {"current_stage": stage}
        if detail is not None:
            fields["status_detail"] = detail
        return self.update(job_id, **fields)

    def acquire_lock(self, job_id: str, owner: str) -> bool:
        """Single-process mutation guard for regenerate/retry endpoints."""
        with self._lock, self._conn:
            job = self._get_locked(job_id)
            if job is None:
                return False
            if job.get("locked_by") or job.get("status") == PROCESSING:
                return False
            job["locked_by"] = owner
            job["updated_at"] = time.time()
            self._update_locked(job)
            return True

    def release_lock(self, job_id: str, owner: str) -> None:
        with self._lock, self._conn:
            job = self._get_locked(job_id)
            if job is not None and job.get("locked_by") == owner:
                job["locked_by"] = None
                job["updated_at"] = time.time()
                self._update_locked(job)

    def force_release_lock(self, job_id: str) -> None:
        """Clear any mutation lock (startup recovery only)."""
        with self._lock, self._conn:
            job = self._get_locked(job_id)
            if job is not None and job.get("locked_by"):
                job["locked_by"] = None
                job["updated_at"] = time.time()
                self._update_locked(job)

    def reset(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM jobs")
            self._conn.execute("DELETE FROM idempotency")
