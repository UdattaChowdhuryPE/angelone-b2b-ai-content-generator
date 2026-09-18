import os

from pipeline.models import (
    VideoRequest,
    ScriptValidationError,
    StoryboardValidationError,
)
from pipeline.validators import validate_storyboard
from providers.llm import LLMProvider
from providers.voice import ElevenLabsVoiceProvider, VoiceProvider


class VideoPipeline:
    def __init__(self, voice_provider: VoiceProvider | None = None):
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY is missing from .env")
        self.llm = LLMProvider(api_key=api_key)
        self.voice = voice_provider

    def create_assets(
        self,
        request: VideoRequest,
        audio_output_path: str = "output/voice.mp3",
    ) -> dict:
        script = self.llm.generate_script(request)

        validation = self.llm.validate_script(request, script)
        if not validation.valid:
            raise ScriptValidationError(
                "Unsupported claims: "
                + ", ".join(validation.unsupported_claims)
            )

        storyboard = None
        last_error = None

        for attempt in range(2):
            if attempt == 0:
                storyboard = self.llm.generate_storyboard(
                    request, script, request.duration_seconds,
                )
            else:
                correction_prompt = (
                    f"The previous storyboard failed structural validation "
                    f"with this error:\n{last_error}\n\n"
                    f"CRITICAL RULES FOR CORRECTION:\n"
                    f"- Keep the EXACT same approved script, narration, "
                    f"key points, and claims.\n"
                    f"- Fix ONLY the structural/timestamp violation described "
                    f"above.\n"
                    f"- Fit the existing narration within "
                    f"{request.duration_seconds} seconds using realistic "
                    f"scene timings.\n"
                    f"- NEVER exceed the requested duration of "
                    f"{request.duration_seconds} seconds.\n"
                    f"- Do NOT shorten, rewrite, or condense the narration. "
                    f"Keep every word.\n"
                    f"- Adjust scene start/end timestamps so all narration "
                    f"fits within the duration limit."
                )
                storyboard = self.llm.generate_storyboard(
                    request, script, request.duration_seconds,
                    correction_prompt=correction_prompt,
                )

            try:
                validate_storyboard(storyboard, request.duration_seconds)
                break
            except StoryboardValidationError as e:
                last_error = str(e)
                if attempt == 1:
                    raise StoryboardValidationError(
                        f"Storyboard validation failed after 2 attempts: {e}"
                    ) from e

        voice = self.voice or ElevenLabsVoiceProvider()
        voice.generate(script.full_script, audio_output_path)

        return {
            "script": script,
            "storyboard": storyboard,
            "broll": [],
            "audio_path": audio_output_path,
        }
