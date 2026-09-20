import os
import tempfile
import time

import requests

from video.assets import download_file


#: HeyGen Avatar IV native source size. The upload background variant
#: is normalized to exactly this size (proportional cover + minimal
#: center crop) so HeyGen never has to contain/letterbox it.
UPLOAD_WIDTH = 720
UPLOAD_HEIGHT = 1280


def build_zoomed_out_background(
    source_path: str,
    output_path: str | None = None,
    scale: float = 0.9,
):
    """Derive the HeyGen upload variant of the canonical bg.

    Proportional cover to exactly 720x1280 (HeyGen Avatar IV native):

    - proportional scaling (no distortion)
    - scale = max(720/w, 1280/h) so the frame is fully covered
    - minimal center crop of only the excess dimension
    - no padding, no white/blank borders

    Source and target aspect ratios differ slightly, so a minimal
    crop is mathematically required (for 1536x2752: ~10px of height).

    The ``scale`` argument is legacy (previous 90% zoom-out) and is
    ignored for geometry; it is still validated to (0, 1) so existing
    callers/tests passing ``scale=0.9`` keep working. The canonical
    file is only read — never modified. Returns a PIL Image when
    output_path is None, else writes the PNG and returns output_path.
    """
    import math

    from PIL import Image

    if not 0 < scale < 1:
        raise ValueError(f"scale must be in (0, 1), got {scale}")
    if not os.path.exists(source_path):
        raise FileNotFoundError(
            f"Background source not found: {source_path}"
        )
    img = Image.open(source_path).convert("RGB")
    width, height = img.size
    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid source dimensions: {width}x{height}")
    factor = max(UPLOAD_WIDTH / width, UPLOAD_HEIGHT / height)
    scaled_size = (
        max(1, math.ceil(width * factor)),
        max(1, math.ceil(height * factor)),
    )
    resized = img.resize(scaled_size, Image.LANCZOS)
    left = (resized.width - UPLOAD_WIDTH) // 2
    top = (resized.height - UPLOAD_HEIGHT) // 2
    out = resized.crop(
        (left, top, left + UPLOAD_WIDTH, top + UPLOAD_HEIGHT)
    )
    if output_path is None:
        return out
    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    out.save(output_path)
    return output_path


class HeyGenAvatarProvider:
    """
    Thin adapter for HeyGen avatar video generation (V0.3 smoke test).

    Workflow: upload local audio -> create avatar video with
    audio_asset_id -> poll async job -> download MP4.
    """

    BASE_URL = "https://api.heygen.com"

    #: Single source of truth for the V0 studio background. Used for every
    #: avatar render (initial generation, regenerations, retries). Never
    #: generate or substitute experimental backgrounds from scripts/.
    #: At upload time a cover-normalized 720x1280 variant is built from
    #: this file (see build_zoomed_out_background) — the canonical file
    #: itself is never modified or replaced.
    STUDIO_BACKGROUND_PATH = "assets/backgrounds/studio_background.png"

    #: Legacy zoom argument kept for backward compatibility with existing
    #: callers/tests. Geometry is now a proportional cover to exactly
    #: 720x1280 (no shrink, no padding, no white borders, minimal center
    #: crop). HeyGen fit stays "cover".
    BACKGROUND_ZOOM = 0.9

    #: HeyGen renders the avatar source at 720p/9:16. The production
    #: compositor upscales to the 1080x1920 final — see video/compositor.py.
    SOURCE_RESOLUTION = "720p"
    SOURCE_ASPECT_RATIO = "9:16"

    #: Green-screen future-render path. A solid green background lets the
    #: local compositor chromakey the presenter and composite:
    #: studio BG -> presenter -> desk foreground -> captions.
    #: HeyGen CreateVideoFromAvatar supports background {color|image} on
    #: the Avatar IV path; color uses {"type": "color", "value": "#RRGGBB"}
    #: and must NOT set remove_background (matting is done locally).
    GREEN_SCREEN_HEX = "#00FF00"
    GREEN_SCREEN_ENGINE = {"type": "avatar_iv"}

    def __init__(
        self,
        api_key: str | None = None,
        avatar_id: str | None = None,
        background_image_path: str | None = None,
    ):
        self.api_key = api_key or os.getenv("HEYGEN_API_KEY")
        self.avatar_id = avatar_id or os.getenv("HEYGEN_AVATAR_ID")
        if background_image_path is not None:
            self.background_image_path = background_image_path
        else:
            self.background_image_path = os.getenv(
                "HEYGEN_BACKGROUND_IMAGE", self.STUDIO_BACKGROUND_PATH
            )

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
        background_asset_id: str | None = None,
        background: dict | None = None,
    ) -> dict:
        # Production engine is Avatar IV on the configured avatar.
        # Two background modes (never combined):
        # - studio-baked (legacy): background_asset_id -> fit=cover +
        #   background={type:image} + remove_background=True. Presenter is
        #   matted over the canonical studio PNG by HeyGen; the local
        #   compositor shows it full-bleed via _avatar_norm_filter().
        # - green-screen (future): background={type:color,value:#00FF00}
        #   with NO remove_background. The local compositor chromakeys the
        #   presenter and composites studio -> presenter -> desk -> captions.
        if background is not None and background_asset_id is not None:
            raise ValueError(
                "Pass either background (color dict) or "
                "background_asset_id (image), never both."
            )
        payload = {
            "type": "avatar",
            "avatar_id": self.avatar_id,
            "audio_asset_id": audio_asset_id,
            "title": title,
            "aspect_ratio": aspect_ratio,
            "resolution": resolution,
            "engine": engine or {"type": "avatar_iv"},
        }
        if background_asset_id is not None:
            # Native HeyGen studio-background path (proven in V0.11):
            # matte the avatar over the canonical studio PNG.
            payload["fit"] = "cover"
            payload["background"] = {
                "type": "image",
                "asset_id": background_asset_id,
            }
            payload["remove_background"] = True
        elif background is not None:
            # Green-screen path: solid color, no server-side matting.
            if not isinstance(background, dict) or background.get("type") != "color":
                raise ValueError(
                    "Green-screen background must be "
                    '{"type": "color", "value": "#RRGGBB"}; '
                    f"got {background!r}."
                )
            payload["fit"] = "cover"
            payload["background"] = dict(background)
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

    def upload_image(self, image_path: str) -> str:
        """Upload a background image asset, returning its asset_id."""
        if not os.path.exists(image_path):
            raise FileNotFoundError(
                f"Background image not found: {image_path}"
            )
        if os.path.getsize(image_path) == 0:
            raise ValueError(f"Background image is empty: {image_path}")
        with open(image_path, "rb") as f:
            response = requests.post(
                f"{self.BASE_URL}/v3/assets",
                headers=self.headers,
                files={
                    "file": (
                        os.path.basename(image_path),
                        f,
                        "image/png",
                    )
                },
                timeout=120,
            )
        response.raise_for_status()
        data = response.json().get("data", {})
        asset_id = data.get("asset_id")
        if not asset_id:
            raise RuntimeError(
                "HeyGen image upload returned no asset_id: "
                f"{response.text}"
            )
        return asset_id

    def _prepare_background_upload(self) -> str:
        """Build the cover-normalized 720x1280 upload variant.

        Returns the temp PNG path (caller deletes it after upload).
        The canonical file on disk is never modified or replaced.
        """
        bg_path = self.background_image_path
        if not bg_path or not os.path.exists(bg_path):
            raise FileNotFoundError(
                "Canonical studio background missing: "
                f"{bg_path!r}. Expected at "
                f"{self.STUDIO_BACKGROUND_PATH}."
            )
        fd, tmp = tempfile.mkstemp(
            prefix="studio_bg_upload_", suffix=".png"
        )
        os.close(fd)
        return build_zoomed_out_background(
            bg_path, tmp, scale=self.BACKGROUND_ZOOM
        )

    def generate(
        self,
        audio_path: str,
        output_path: str,
        poll_interval: int = 10,
        timeout: int = 600,
    ) -> str:
        """Worker-compatible avatar render: audio -> studio-bg avatar MP4.

        Uploads the narration audio AND a cover-normalized 720x1280
        variant of the canonical studio background, creates the avatar
        video (Avatar IV, 9:16, 720p source), waits for completion,
        downloads the MP4 to output_path. Returns output_path.
        API errors propagate — never swallowed, never faked.
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        bg_path = self.background_image_path
        if not bg_path or not os.path.exists(bg_path):
            raise FileNotFoundError(
                "Canonical studio background missing: "
                f"{bg_path!r}. Expected at "
                f"{self.STUDIO_BACKGROUND_PATH}."
            )

        audio_asset_id = self.upload_audio(audio_path)
        upload_bg_path = self._prepare_background_upload()
        try:
            bg_asset_id = self.upload_image(upload_bg_path)
        finally:
            if upload_bg_path != bg_path and os.path.exists(upload_bg_path):
                os.remove(upload_bg_path)
        created = self.create_video(
            audio_asset_id,
            background_asset_id=bg_asset_id,
        )
        video_id = created.get("data", {}).get("video_id")
        if not video_id:
            raise RuntimeError(
                f"HeyGen create returned no video_id: {created}"
            )
        final = self.wait_for_result(
            video_id, poll_interval=poll_interval, timeout=timeout
        )
        data = final.get("data", {})
        video_url = data.get("video_url") or data.get("url")
        if not video_url:
            raise RuntimeError(
                f"HeyGen completed with no video_url: {final}"
            )
        return self.download_video(video_url, output_path)

    @classmethod
    def green_background(cls, hex_color: str | None = None) -> dict:
        """Solid-color background payload for the green-screen path."""
        value = hex_color or cls.GREEN_SCREEN_HEX
        if (
            not isinstance(value, str)
            or not value.startswith("#")
            or len(value) != 7
        ):
            raise ValueError(
                "Green-screen color must look like '#00FF00'; "
                f"got {value!r}."
            )
        return {"type": "color", "value": value}

    def generate_green(
        self,
        audio_path: str,
        output_path: str,
        poll_interval: int = 10,
        timeout: int = 600,
    ) -> str:
        """Worker-compatible avatar render: audio -> green-screen MP4.

        Future-render path for the local studio composite. Uploads ONLY
        the narration audio, creates the avatar video (Avatar IV, 9:16,
        720p) over a solid green background with NO server-side matting,
        waits for completion, downloads the MP4. The local compositor
        (video/compositor.py studio path) chromakeys green and composites:
        canonical studio BG -> presenter -> desk foreground -> captions.
        API errors propagate — never swallowed, never faked.
        """
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file not found: {audio_path}")
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        audio_asset_id = self.upload_audio(audio_path)
        created = self.create_video(
            audio_asset_id,
            background=self.green_background(),
        )
        video_id = created.get("data", {}).get("video_id")
        if not video_id:
            raise RuntimeError(
                f"HeyGen create returned no video_id: {created}"
            )
        final = self.wait_for_result(
            video_id, poll_interval=poll_interval, timeout=timeout
        )
        data = final.get("data", {})
        video_url = data.get("video_url") or data.get("url")
        if not video_url:
            raise RuntimeError(
                f"HeyGen completed with no video_url: {final}"
            )
        return self.download_video(video_url, output_path)
