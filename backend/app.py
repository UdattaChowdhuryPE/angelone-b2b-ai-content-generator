import os

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from backend import worker
from backend.schemas import CreateVideoRequest, CreateVideoResponse, JobStatusResponse
from backend.store import COMPLETED, JobStore
from backend.worker import (
    OUTPUT_ROOT,
    USE_REAL_PROVIDERS,
    default_pipeline_factory,
)


def _job_payload(job: dict) -> dict:
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "current_stage": job.get("current_stage"),
        "status_detail": job.get("status_detail"),
        "error": job.get("error"),
        "error_type": job.get("error_type"),
        "failed_stage": job.get("failed_stage"),
        "operation": job.get("operation"),
        "completed_artifacts": job.get("completed_artifacts"),
        "skipped_stages": job.get("skipped_stages"),
        "video_url": job.get("video_url"),
        "request": job.get("request"),
        "result": job.get("result"),
        "script": job.get("script"),
        "storyboard": job.get("storyboard"),
        "created_at": job.get("created_at"),
        "updated_at": job.get("updated_at"),
    }


def _resolve_final_path(job: dict, output_root: str) -> str | None:
    """Server-side artifact resolution only — never from user input."""
    job_id = job.get("job_id", "")
    if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
        return None
    candidate = os.path.join(output_root, job_id, worker.FINAL_FILENAME)
    if not os.path.exists(candidate):
        return None
    return candidate


def create_app(
    store: JobStore | None = None,
    pipeline_factory=default_pipeline_factory,
    avatar_provider=USE_REAL_PROVIDERS,
    broll_provider=USE_REAL_PROVIDERS,
    compositor_fn=USE_REAL_PROVIDERS,
    output_root: str | None = None,
) -> FastAPI:
    app = FastAPI(title="AI Financial Video Generator API")
    app.state.store = store or JobStore()
    app.state.pipeline_factory = pipeline_factory
    app.state.avatar_provider = avatar_provider
    app.state.broll_provider = broll_provider
    app.state.compositor_fn = compositor_fn
    # Persistent artifact root: explicit arg wins, else $OUTPUT_DIR,
    # else the local-development default. Layout stays <root>/<job_id>/.
    app.state.output_root = (
        output_root or os.getenv("OUTPUT_DIR", OUTPUT_ROOT)
    )
    # Startup recovery: jobs left 'processing' by a restart become safely
    # failed (locks cleared, artifacts preserved, no provider invoked).
    worker.recover_stale_jobs(app.state.store, app.state.output_root)

    def _wiring() -> dict:
        return {
            "pipeline_factory": app.state.pipeline_factory,
            "avatar_provider": app.state.avatar_provider,
            "broll_provider": app.state.broll_provider,
            "compositor_fn": app.state.compositor_fn,
            "output_root": app.state.output_root,
        }

    def _get_job_or_404(job_id: str) -> dict:
        job = app.state.store.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    @app.post("/api/videos", response_model=CreateVideoResponse, status_code=202)
    def create_video(payload: CreateVideoRequest, background: BackgroundTasks):
        # Atomic idempotency + single-concurrency guard: duplicate keys
        # return the existing job (200, no new work); a fresh request
        # while another job is active is rejected (409, no record left).
        job, outcome = app.state.store.create_guarded(
            payload.model_dump(), payload.idempotency_key
        )
        if outcome == "duplicate":
            return JSONResponse(
                status_code=200,
                content={"job_id": job["job_id"], "status": job["status"]},
            )
        if outcome == "busy":
            raise HTTPException(
                status_code=409,
                detail=(
                    "Another video generation is currently running. "
                    "Please try again later."
                ),
            )
        background.add_task(
            worker.run_job,
            job["job_id"],
            payload.model_dump(),
            app.state.store,
            *(_wiring_kwargs(app)),
        )
        return {"job_id": job["job_id"], "status": job["status"]}

    @app.get("/healthz")
    def get_healthz():
        """Lightweight liveness probe. Never touches providers or disk."""
        return {"ok": True}

    @app.get("/api/videos/{job_id}", response_model=JobStatusResponse)
    def get_video(job_id: str):
        return _job_payload(_get_job_or_404(job_id))

    @app.get("/api/preflight")
    def get_preflight():
        """Non-paid readiness check — never calls a paid provider."""
        from backend.preflight import run_preflight

        return run_preflight(output_root=app.state.output_root)

    @app.get("/api/videos/{job_id}/file")
    def get_video_file(job_id: str):
        job = _get_job_or_404(job_id)
        if job["status"] != COMPLETED:
            raise HTTPException(
                status_code=409,
                detail=f"job is {job['status']}, file not ready",
            )
        artifact = _resolve_final_path(job, app.state.output_root)
        stored = job.get("artifact_path")
        if artifact is None or stored is None or artifact != stored:
            # Fall back to the stored path only if it resolves inside the
            # job directory (guards against path traversal via stored data).
            expected_dir = os.path.join(app.state.output_root, job_id)
            if (
                not stored
                or os.path.dirname(os.path.abspath(stored))
                != os.path.abspath(expected_dir)
                or not os.path.exists(stored)
            ):
                raise HTTPException(
                    status_code=409, detail="artifact missing"
                )
            artifact = stored
        if artifact.endswith(".mp4"):
            return FileResponse(
                artifact,
                media_type="video/mp4",
                filename=f"{job_id}_final.mp4",
                headers={
                    "Content-Disposition": (
                        f'attachment; filename="{job_id}_final.mp4"'
                    )
                },
            )
        # Legacy text test artifacts (never a fake mp4).
        media_type = "text/plain" if artifact.endswith(".txt") else "application/octet-stream"
        return FileResponse(artifact, media_type=media_type, filename=artifact.split("/")[-1])

    @app.post(
        "/api/videos/{job_id}/script/regenerate",
        response_model=JobStatusResponse,
        status_code=202,
    )
    def regenerate_script(job_id: str, background: BackgroundTasks):
        # Cheap synchronous checks only; paid OpenAI/media work runs in the
        # background and surfaces through GET polling (never blocks here).
        job = _get_job_or_404(job_id)
        _reject_if_mutating(job)
        owner = f"regen-script-{job_id}"
        if not app.state.store.acquire_lock(job_id, owner):
            raise HTTPException(
                status_code=409,
                detail="job is currently running another operation",
            )
        app.state.store.set_status(job_id, worker.PROCESSING)
        background.add_task(
            worker.run_regenerate_script_bg,
            job_id,
            app.state.store,
            owner,
            *(_wiring_kwargs(app)),
        )
        return _job_payload(app.state.store.get(job_id))

    @app.post(
        "/api/videos/{job_id}/storyboard/regenerate",
        response_model=JobStatusResponse,
        status_code=202,
    )
    def regenerate_storyboard(job_id: str, background: BackgroundTasks):
        job = _get_job_or_404(job_id)
        _reject_if_mutating(job)
        owner = f"regen-storyboard-{job_id}"
        if not app.state.store.acquire_lock(job_id, owner):
            raise HTTPException(
                status_code=409,
                detail="job is currently running another operation",
            )
        app.state.store.set_status(job_id, worker.PROCESSING)
        background.add_task(
            worker.run_regenerate_storyboard_bg,
            job_id,
            app.state.store,
            owner,
            *(_wiring_kwargs(app)),
        )
        return _job_payload(app.state.store.get(job_id))

    @app.post(
        "/api/videos/{job_id}/retry",
        response_model=JobStatusResponse,
        status_code=202,
    )
    def retry_job(job_id: str, background: BackgroundTasks):
        job = _get_job_or_404(job_id)
        _reject_if_mutating(job)
        if job["status"] not in ("failed", "completed"):
            raise HTTPException(
                status_code=409,
                detail=f"job is {job['status']}, nothing to retry",
            )
        owner = f"retry-{job_id}"
        if not app.state.store.acquire_lock(job_id, owner):
            raise HTTPException(
                status_code=409,
                detail="job is currently running another operation",
            )
        app.state.store.set_status(job_id, worker.PROCESSING)
        background.add_task(
            worker.run_retry_bg,
            job_id,
            app.state.store,
            owner,
            *(_wiring_kwargs(app)),
        )
        return _job_payload(app.state.store.get(job_id))

    return app


def _wiring_kwargs(app: FastAPI) -> tuple:
    return (
        app.state.pipeline_factory,
        app.state.avatar_provider,
        app.state.broll_provider,
        app.state.compositor_fn,
        app.state.output_root,
    )


def _reject_if_mutating(job: dict) -> None:
    from backend.store import PROCESSING

    if job.get("status") == PROCESSING or job.get("locked_by"):
        raise HTTPException(
            status_code=409,
            detail="job is currently running another operation",
        )


app = create_app()
