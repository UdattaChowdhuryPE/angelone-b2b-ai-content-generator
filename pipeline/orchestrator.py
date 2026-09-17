import os

from pipeline.models import VideoRequest
from providers.llm import LLMProvider


class VideoPipeline:
    def __init__(self):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is missing from .env")
        self.llm = LLMProvider(api_key=api_key)

    def create_assets(self, request: VideoRequest) -> dict:
        script = self.llm.generate_script(request)

        validation = self.llm.validate_script(request, script)
        if not validation.valid:
            raise ValueError(
                "Unsupported claims: "
                + ", ".join(validation.unsupported_claims)
            )

        storyboard = self.llm.generate_storyboard(
            request,
            script,
            request.duration_seconds,
        )

        return {
            "script": script,
            "storyboard": storyboard,
            "broll": [],
        }
