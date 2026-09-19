import os

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse

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
    output_root: str = OUTPUT_ROOT,
) -> FastAPI:
    app = FastAPI(title="AI Financial Video Generator API")
    app.state.store = store or JobStore()
    app.state.pipeline_factory = pipeline_factory
    app.state.avatar_provider = avatar_provider
    app.state.broll_provider = broll_provider
    app.state.compositor_fn = compositor_fn
    app.state.output_root = output_root

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
        job = app.state.store.create(payload.model_dump())
        background.add_task(
            worker.run_job,
            job["job_id"],
            payload.model_dump(),
            app.state.store,
            *(_wiring_kwargs(app)),
        )
        return {"job_id": job["job_id"], "status": job["status"]}

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
    def regenerate_script(job_id: str):
        job = _get_job_or_404(job_id)
        _reject_if_mutating(job)
        try:
            updated = worker.regenerate_script(
                job_id, app.state.store, *(_wiring_kwargs(app))
            )
        except worker.RegenValidationError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": str(exc),
                    "unsupported_claims": exc.unsupported_claims,
                },
            )
        except worker.JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found")
        return _job_payload(updated)

    @app.post(
        "/api/videos/{job_id}/storyboard/regenerate",
        response_model=JobStatusResponse,
        status_code=202,
    )
    def regenerate_storyboard(job_id: str):
        job = _get_job_or_404(job_id)
        _reject_if_mutating(job)
        try:
            updated = worker.regenerate_storyboard(
                job_id, app.state.store, *(_wiring_kwargs(app))
            )
        except worker.JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found")
        return _job_payload(updated)

    @app.post(
        "/api/videos/{job_id}/retry",
        response_model=JobStatusResponse,
        status_code=202,
    )
    def retry_job(job_id: str):
        job = _get_job_or_404(job_id)
        _reject_if_mutating(job)
        if job["status"] not in ("failed", "completed"):
            raise HTTPException(
                status_code=409,
                detail=f"job is {job['status']}, nothing to retry",
            )
        try:
            updated = worker.retry_job(
                job_id, app.state.store, *(_wiring_kwargs(app))
            )
        except worker.JobConflictError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except KeyError:
            raise HTTPException(status_code=404, detail="job not found")
        return _job_payload(updated)

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
