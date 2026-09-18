import os

import requests


class VoiceProvider:
    def generate(
        self,
        text: str,
        output_path: str,
    ):
        raise NotImplementedError


class ElevenLabsVoiceProvider(VoiceProvider):
    BASE_URL = "https://api.elevenlabs.io/v1/text-to-speech"

    def __init__(
        self,
        api_key: str | None = None,
        voice_id: str | None = None,
        model_id: str = "eleven_v3",
    ):
        self.api_key = api_key or os.getenv("ELEVENLABS_API_KEY")
        self.voice_id = voice_id or os.getenv("ELEVENLABS_VOICE_ID")
        self.model_id = model_id

        if not self.api_key:
            raise ValueError("ELEVENLABS_API_KEY is missing from .env")
        if not self.voice_id:
            raise ValueError("ELEVENLABS_VOICE_ID is missing from .env")

    def generate(
        self,
        text: str,
        output_path: str,
    ) -> str:
        if not text or not text.strip():
            raise ValueError("Text for voice generation must not be empty.")

        response = requests.post(
            f"{self.BASE_URL}/{self.voice_id}",
            params={"output_format": "mp3_44100_128"},
            headers={
                "xi-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            json={
                "text": text,
                "model_id": self.model_id,
            },
            timeout=60,
        )

        response.raise_for_status()

        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        with open(output_path, "wb") as f:
            f.write(response.content)

        return output_path
