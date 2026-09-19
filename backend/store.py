import threading
import time
import uuid


QUEUED = "queued"
PROCESSING = "processing"
COMPLETED = "completed"
FAILED = "failed"

TERMINAL_STATES = {COMPLETED, FAILED}

# Ordered worker stages exposed as job["current_stage"].
STAGE_QUEUED = "queued"
STAGE_SCRIPT = "script"
STAGE_SCRIPT_VALIDATION = "script_validation"
STAGE_STORYBOARD = "storyboard"
STAGE_VOICE = "voice"
STAGE_AVATAR = "avatar"
STAGE_BROLL = "broll"
STAGE_COMPOSITION = "composition"
STAGE_COMPLETED = "completed"
STAGE_FAILED = "failed"


#: Retention for create idempotency keys (duplicate Generate protection).
IDEMPOTENCY_TTL_S = 24 * 3600


class JobStore:
    """Lightweight in-memory job store for development/tests.

    No Redis/Celery/Postgres. Single-process only; jobs are lost on restart.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}
        # idempotency_key -> (job_id, expires_at). Bounded by TTL pruning
        # on every guarded create; never persisted (single-process v1).
        self._idem: dict[str, tuple[str, float]] = {}

    def create(self, request_dict: dict) -> dict:
        job_id = uuid.uuid4().hex
        now = time.time()
        job = {
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
        with self._lock:
            self._jobs[job_id] = job
            return dict(self._jobs[job_id])

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job is not None else None

    def all_jobs(self) -> list[dict]:
        """Copies of every known job (used by startup stale-job recovery)."""
        with self._lock:
            return [dict(job) for job in self._jobs.values()]

    def has_active_job(self) -> bool:
        """True when any non-terminal job is processing or lock-held.

        Observability helper only — creation uses create_guarded() so the
        idempotency lookup + active check + insert stay atomic.
        """
        with self._lock:
            return self._has_active_locked()

    def _has_active_locked(self) -> bool:
        for job in self._jobs.values():
            if job.get("status") not in TERMINAL_STATES or job.get("locked_by"):
                return True
        return False

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
        with self._lock:
            # Bounded retention: prune expired idempotency entries on write.
            expired = [
                key for key, (_, exp) in self._idem.items() if exp <= now
            ]
            for key in expired:
                del self._idem[key]
            if idempotency_key:
                hit = self._idem.get(idempotency_key)
                if hit is not None:
                    job_id, exp = hit
                    job = self._jobs.get(job_id)
                    if job is not None and exp > now:
                        return dict(job), "duplicate"
                    # Stale mapping (job gone or expired) — drop it.
                    del self._idem[idempotency_key]
            if self._has_active_locked():
                return None, "busy"
            job_id = uuid.uuid4().hex
            job = {
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
            self._jobs[job_id] = job
            if idempotency_key:
                self._idem[idempotency_key] = (job_id, now + IDEMPOTENCY_TTL_S)
            return dict(job), "created"

    def update(self, job_id: str, **fields) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            job.update(fields)
            job["updated_at"] = time.time()
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
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.get("locked_by") or job.get("status") == PROCESSING:
                return False
            job["locked_by"] = owner
            job["updated_at"] = time.time()
            return True

    def release_lock(self, job_id: str, owner: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.get("locked_by") == owner:
                job["locked_by"] = None
                job["updated_at"] = time.time()

    def force_release_lock(self, job_id: str) -> None:
        """Clear any mutation lock (startup recovery only)."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is not None and job.get("locked_by"):
                job["locked_by"] = None
                job["updated_at"] = time.time()

    def reset(self) -> None:
        with self._lock:
            self._jobs.clear()
            self._idem.clear()
