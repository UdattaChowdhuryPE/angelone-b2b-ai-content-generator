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


class JobStore:
    """Lightweight in-memory job store for development/tests.

    No Redis/Celery/Postgres. Single-process only; jobs are lost on restart.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict] = {}

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

    def reset(self) -> None:
        with self._lock:
            self._jobs.clear()
