from typing import List, Optional

from pydantic import BaseModel


class VideoRequest(BaseModel):
    topic: str
    key_message: Optional[str] = None
    language: str = "English"
    duration_seconds: int = 60


class Script(BaseModel):
    title: str
    hook: str
    body: List[str]
    takeaway: str
    full_script: str


class ScriptValidation(BaseModel):
    valid: bool
    unsupported_claims: List[str] = []


class Scene(BaseModel):
    scene_id: int
    start: float
    end: float
    narration: str
    key_claim: str
    visual_type: str
    visual_prompt: Optional[str] = None
    on_screen_text: Optional[str] = None
    avatar_required: bool = False
    broll_required: bool = False


class Storyboard(BaseModel):
    scenes: List[Scene]
    total_duration: float


class ScriptValidationError(ValueError):
    """Raised when the generated script fails content validation."""

    pass


class StoryboardValidationError(ValueError):
    """Raised when the generated storyboard fails structural validation."""

    pass