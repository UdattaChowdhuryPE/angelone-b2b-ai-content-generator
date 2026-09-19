from typing import Literal

from pydantic import BaseModel, Field, field_validator


class CreateVideoRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=500)
    key_message: str = Field(min_length=1, max_length=5000)
    language: Literal["English", "Hinglish", "Hindi"] = "English"
    duration_seconds: Literal[30, 45, 60]
    # Opaque client-generated token (one per Generate intent). Used to
    # deduplicate double-clicks/refreshes/retries so a duplicate request
    # can never mint a second paid job. Length-bounded only (not UUID).
    idempotency_key: str | None = Field(default=None, min_length=16, max_length=64)

    @field_validator("topic", "key_message")
    @classmethod
    def _must_be_non_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()


class CreateVideoResponse(BaseModel):
    job_id: str
    status: str


class JobStatusResponse(BaseModel):
    job_id: str
    status: str
    current_stage: str | None = None
    status_detail: str | None = None
    error: str | None = None
    error_type: str | None = None
    failed_stage: str | None = None
    operation: str | None = None
    completed_artifacts: list | None = None
    skipped_stages: list | None = None
    video_url: str | None = None
    request: dict | None = None
    result: dict | None = None
    script: dict | None = None
    storyboard: dict | None = None
    created_at: float | None = None
    updated_at: float | None = None
