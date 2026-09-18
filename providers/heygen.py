import os
import time

import requests

from video.assets import download_file


class HeyGenAvatarProvider:
    """
    Thin adapter for HeyGen avatar video generation (V0.3 smoke test).

    Workflow: upload local audio -> create avatar video with
    audio_asset_id -> poll async job -> download MP4.
    """

    BASE_URL = "https://api.heygen.com"

    def __init__(
        self,
        api_key: str | None = None,
        avatar_id: str | None = None,
    ):
        self.api_key = api_key or os.getenv("HEYGEN_API_KEY")
        self.avatar_id = avatar_id or os.getenv("HEYGEN_AVATAR_ID")

        if not self.api_key:
            raise ValueError("HEYGEN_API_KEY is missing from .env")
        if not self.avatar_id:
            raise ValueError("HEYGEN_AVATAR_ID is missing from .env")

    @property
    def headers(self) -> dict:
        return {"x-api-key": self.api_key}

    def upload_audio(self, audio_path: str) -> str:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        if os.path.getsize(audio_path) == 0:
            raise ValueError(f"Audio file is empty: {audio_path}")

        with open(audio_path, "rb") as f:
            response = requests.post(
                f"{self.BASE_URL}/v3/assets",
                headers=self.headers,
                files={"file": (os.path.basename(audio_path), f)},
                timeout=120,
            )

        response.raise_for_status()
        data = response.json().get("data", {})
        asset_id = data.get("asset_id")
        if not asset_id:
            raise RuntimeError(f"HeyGen asset upload returned no asset_id: {response.text}")
        return asset_id

    def create_video(
        self,
        audio_asset_id: str,
        title: str = "V0.3 smoke test",
        aspect_ratio: str = "9:16",
        resolution: str = "720p",
        engine: dict | None = None,
    ) -> dict:
        # NOTE: the configured public avatar (Aditya_public_*) only
        # supports the Avatar III engine — Avatar IV (API default)
        # and Avatar V both return 400 for it.
        payload = {
            "type": "avatar",
            "avatar_id": self.avatar_id,
            "audio_asset_id": audio_asset_id,
            "title": title,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "engine": engine or {"type": "avatar_iii"},
        }
        response = requests.post(
            f"{self.BASE_URL}/v3/videos",
            headers={**self.headers, "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        response.raise_for_status()
        body = response.json()
        video_id = body.get("data", {}).get("video_id")
        if not video_id:
            raise RuntimeError(f"HeyGen create returned no video_id: {response.text}")
        return body

    def get_status(self, video_id: str) -> dict:
        response = requests.get(
            f"{self.BASE_URL}/v3/videos/{video_id}",
            headers=self.headers,
            timeout=30,
        )
        response.raise_for_status()
        return response.json()

    def wait_for_result(
        self,
        video_id: str,
        poll_interval: int = 10,
        timeout: int = 600,
    ) -> dict:
        terminal_states = {"completed", "failed", "error", "cancelled"}
        started = time.time()
        last: dict = {}
        while time.time() - started < timeout:
            last = self.get_status(video_id)
            status = last.get("data", {}).get("status", "")
            if status == "completed":
                return last
            if status in terminal_states:
                raise RuntimeError(f"HeyGen generation failed with status '{status}': {last}")
            time.sleep(poll_interval)
        raise TimeoutError(f"HeyGen generation timed out after {timeout}s: {last}")

    def download_video(self, video_url: str, output_path: str) -> str:
        return download_file(video_url, output_path)
