import os
import time
import requests


#: Proven Higgsfield model + payload contract (from V0.4 smoke experiment).
#: Narration is NEVER sent to the video model — visual_prompt only.
BROLL_MODEL = "alibaba/wan-3.0/text-to-video"
BROLL_DURATION = 5
BROLL_ASPECT_RATIO = "9:16"
BROLL_RESOLUTION = "720p"

#: HeyGen renders the avatar source at 720p; B-roll matches it. The final
#: composed MP4 is upscaled to 1080x1920 (see video/compositor.py).
BROLL_SOURCE_RESOLUTION = "720x1280"


def build_broll_payload(visual_prompt: str) -> dict:
    """Exact generation payload for one B-roll clip."""
    if not visual_prompt or not visual_prompt.strip():
        raise ValueError("B-roll visual_prompt must not be empty.")
    return {
        "prompt": visual_prompt,
        "duration": BROLL_DURATION,
        "aspect_ratio": BROLL_ASPECT_RATIO,
        "resolution": BROLL_RESOLUTION,
        "generate_audio": False,
        "enable_thinking": False,
    }


def extract_video_url(result: dict) -> str | None:
    """Try common Higgsfield result shapes without assuming one."""
    if not isinstance(result, dict):
        return None
    for key in ("video_url", "url", "download_url", "output_url"):
        value = result.get(key)
        if isinstance(value, str) and value.startswith("http"):
            return value
    video = result.get("video")
    if isinstance(video, dict):
        url = video.get("url")
        if isinstance(url, str) and url.startswith("http"):
            return url
    data = result.get("data")
    if isinstance(data, dict):
        for key in ("video_url", "url", "download_url", "output_url"):
            value = data.get(key)
            if isinstance(value, str) and value.startswith("http"):
                return value
        for key in ("outputs", "videos", "results"):
            items = data.get(key)
            if isinstance(items, list) and items and isinstance(items[0], str):
                if items[0].startswith("http"):
                    return items[0]
    return None


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

    def fetch(
        self,
        scenes,
        job_dir: str,
    ) -> dict[int, str]:
        """Worker-compatible B-roll fetch.

        Generates one silent clip per scene requiring B-roll (using that
        scene's visual_prompt verbatim), waits for completion, stores each
        clip at ``<job_dir>/broll/scene_<id>.mp4`` plus a ``metadata.json``
        mapping, and returns ``{scene_id: clip_path}`` in scene order.
        Single-shot per scene — no retries (paid API). Errors propagate.
        """
        import json

        from video.assets import download_file
        from video.storyboard import get_broll_scenes

        if scenes is None:
            required = []
        elif hasattr(scenes, "scenes"):
            required = get_broll_scenes(scenes)
        else:
            required = [
                s for s in scenes
                if getattr(s, "broll_required", False)
                and getattr(s, "visual_prompt", None)
            ]
        required = sorted(required, key=lambda s: getattr(s, "scene_id", 0))

        broll_dir = os.path.join(job_dir, "broll")
        os.makedirs(broll_dir, exist_ok=True)

        clips: dict[int, str] = {}
        metadata: list[dict] = []
        for scene in required:
            scene_id = getattr(scene, "scene_id")
            visual_prompt = getattr(scene, "visual_prompt")
            payload = build_broll_payload(visual_prompt)
            submitted = self.submit_video(BROLL_MODEL, payload)
            data = submitted.get("data") or {}
            request_id = (
                submitted.get("request_id")
                or submitted.get("id")
                or data.get("request_id")
                or data.get("id")
            )
            if not request_id:
                raise RuntimeError(
                    "Higgsfield submit returned no request id: "
                    f"{submitted}"
                )
            final = self.wait_for_result(str(request_id))
            video_url = extract_video_url(final)
            if not video_url:
                raise RuntimeError(
                    "Higgsfield completed with no video URL: "
                    f"{final}"
                )
            clip_path = os.path.join(broll_dir, f"scene_{scene_id}.mp4")
            download_file(video_url, clip_path)
            clips[scene_id] = clip_path
            metadata.append(
                {
                    "scene_id": scene_id,
                    "clip_path": clip_path,
                    "request_id": str(request_id),
                    "model": BROLL_MODEL,
                    "payload": payload,
                    "window": [
                        getattr(scene, "start", None),
                        getattr(scene, "end", None),
                    ],
                }
            )

        metadata_path = os.path.join(broll_dir, "metadata.json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)
        return clips
