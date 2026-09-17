import os
import time
import requests


class HiggsfieldProvider:
    """
    Thin adapter for Higgsfield.

    Keep all Higgsfield-specific API behavior here so the application can
    switch providers later.
    """

    BASE_URL = "https://api.higgsfield.ai"

    def __init__(self):
        self.key_id = os.getenv("HF_API_KEY_ID")
        self.key_secret = os.getenv("HF_API_KEY_SECRET")

        if not self.key_id or not self.key_secret:
            raise ValueError(
                "Higgsfield credentials are missing."
            )

    @property
    def headers(self):
        return {
            "Authorization": (
                f"Key {self.key_id}:{self.key_secret}"
            ),
            "Content-Type": "application/json",
        }

    def submit_video(
        self,
        model: str,
        payload: dict,
    ) -> dict:

        response = requests.post(
            f"{self.BASE_URL}/{model}",
            headers=self.headers,
            json=payload,
            timeout=60,
        )

        response.raise_for_status()
        return response.json()

    def get_status(
        self,
        request_id: str,
    ) -> dict:

        response = requests.get(
            f"{self.BASE_URL}/requests/{request_id}/status",
            headers=self.headers,
            timeout=30,
        )

        response.raise_for_status()
        return response.json()

    def wait_for_result(
        self,
        request_id: str,
        poll_interval: int = 5,
        timeout: int = 600,
    ):

        started = time.time()

        while time.time() - started < timeout:

            result = self.get_status(request_id)
            status = result.get("status")

            if status in {"completed", "succeeded"}:
                return result

            if status in {"failed", "cancelled"}:
                raise RuntimeError(
                    f"Higgsfield generation failed: {result}"
                )

            time.sleep(poll_interval)

        raise TimeoutError(
            "Higgsfield generation timed out."
        )
