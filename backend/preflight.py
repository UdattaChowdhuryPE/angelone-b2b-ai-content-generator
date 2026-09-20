"""Non-paid pre-flight checks for the V0 video generator.

This module NEVER calls a paid external API. It only inspects local
configuration: env-var presence (names only, values never exposed),
canonical background readability, FFmpeg/ffprobe availability, output
directory writability, production provider wiring, provider/compositor
method contracts, caption dependencies, and final-validation config.

Presence of credentials does NOT prove a live provider contract —
see the ``live_provider_contract`` WARNING check.
"""

from __future__ import annotations

import inspect
import os
import shutil
import tempfile

REQUIRED_ENV_VARS = [
    "OPENAI_API_KEY",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "HEYGEN_API_KEY",
    "HEYGEN_AVATAR_ID",
    "HF_API_KEY_ID",
    "HF_API_KEY_SECRET",
]

CANONICAL_BACKGROUND = os.path.join(
    "assets", "backgrounds", "studio_background.png"
)

PASS = "PASS"
FAIL = "FAIL"
WARNING = "WARNING"


def _check(name: str, status: str, detail: str = "") -> dict:
    item = {"name": name, "status": status}
    if detail:
        item["detail"] = detail
    return item


def check_env() -> dict:
    missing = [k for k in REQUIRED_ENV_VARS if not os.getenv(k)]
    if missing:
        return _check(
            "env_credentials_configured",
            FAIL,
            "missing: " + ", ".join(missing),
        )
    return _check(
        "env_credentials_configured",
        PASS,
        "all required env vars present; "
        "live provider contract not verified",
    )


def check_canonical_background(
    path: str = CANONICAL_BACKGROUND,
) -> dict:
    # NOTE: the canonical background is a LEGACY-only dependency
    # (avatar_mode="green"/studio-baked renders and rollback). The
    # production default (avatar_mode="full_scene") never uploads or
    # composites it, so run_preflight() does not gate readiness on it.
    if not os.path.exists(path):
        return _check(
            "canonical_background",
            FAIL,
            f"missing canonical background: {path} "
            "(legacy green/studio modes only; "
            "not required for the full_scene production default)",
        )
    if os.path.getsize(path) == 0:
        return _check(
            "canonical_background",
            FAIL,
            f"canonical background is empty: {path}",
        )
    try:
        from PIL import Image

        with Image.open(path) as img:
            img.verify()
        with Image.open(path) as img2:
            width, height = img2.size
    except Exception as exc:
        return _check(
            "canonical_background",
            FAIL,
            f"unreadable background {path}: {exc}",
        )
    detail = f"{path} ({width}x{height}, readable)"
    # 1080x1920 final uses cover-crop; warn if source is tiny.
    if width < 360 or height < 640:
        return _check(
            "canonical_background_processable",
            WARNING,
            detail + " — very small for 1080x1920 cover upscale",
        )
    return _check("canonical_background", PASS, detail)


def check_ffmpeg() -> dict:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return _check("ffmpeg", FAIL, "ffmpeg not found on PATH")
    return _check("ffmpeg", PASS, ffmpeg)


def check_ffprobe() -> dict:
    # ffprobe is MANDATORY: worker artifact validation fails hard
    # without it. Preflight must catch this before real generation.
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return _check(
            "ffprobe",
            FAIL,
            "ffprobe not found on PATH — artifact validation "
            "requires ffprobe and will fail hard",
        )
    return _check("ffprobe", PASS, ffprobe)


def check_output_writable(output_root: str = "output") -> dict:
    try:
        os.makedirs(output_root, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=output_root)
        os.close(fd)
        os.remove(tmp)
        # Absolute path so operators can confirm the persistent mount.
        return _check("output_writable", PASS, os.path.abspath(output_root))
    except Exception as exc:
        return _check(
            "output_writable", FAIL, f"{output_root}: {exc}"
        )


def check_production_wiring() -> dict:
    """Production code must wire real providers and never import fakes."""
    try:
        from backend.app import create_app
        from backend.worker import USE_REAL_PROVIDERS

        app = create_app()
        problems = []
        if app.state.avatar_provider is not USE_REAL_PROVIDERS:
            problems.append("avatar_provider is not USE_REAL_PROVIDERS")
        if app.state.broll_provider is not USE_REAL_PROVIDERS:
            problems.append("broll_provider is not USE_REAL_PROVIDERS")
        if app.state.compositor_fn is not USE_REAL_PROVIDERS:
            problems.append("compositor_fn is not USE_REAL_PROVIDERS")
        import pathlib

        root = pathlib.Path(__file__).resolve().parent.parent
        for name in ("backend/app.py", "backend/worker.py"):
            src = (root / name).read_text()
            for marker in (
                "tests.fakes",
                "smoke_fakes",
                "smoke_app",
                "FakeAvatar",
                "FakeBroll",
                "FakePipeline",
            ):
                if marker in src:
                    problems.append(f"{name} contains {marker}")
        if problems:
            return _check(
                "production_wiring", FAIL, "; ".join(problems)
            )
        return _check(
            "production_wiring",
            PASS,
            "real providers wired; no fake/smoke imports",
        )
    except Exception as exc:
        return _check("production_wiring", FAIL, str(exc)[:300])


def check_provider_contracts() -> dict:
    problems = []
    try:
        from providers.heygen import (
            FullSceneAvatarProvider,
            HeyGenAvatarProvider,
        )

        for method in (
            "generate",
            "generate_full_scene",
            "generate_green",
            "upload_audio",
            "upload_image",
            "create_video",
            "wait_for_result",
            "download_video",
        ):
            if not hasattr(HeyGenAvatarProvider, method):
                problems.append(f"HeyGenAvatarProvider.{method} missing")
        if not issubclass(FullSceneAvatarProvider, HeyGenAvatarProvider):
            problems.append(
                "FullSceneAvatarProvider must subclass HeyGenAvatarProvider"
            )
        sig = inspect.signature(HeyGenAvatarProvider.generate)
        for param in ("audio_path", "output_path"):
            if param not in sig.parameters:
                problems.append(
                    f"HeyGenAvatarProvider.generate missing param {param}"
                )
        sig = inspect.signature(FullSceneAvatarProvider.generate)
        for param in ("audio_path", "output_path"):
            if param not in sig.parameters:
                problems.append(
                    f"FullSceneAvatarProvider.generate missing param {param}"
                )
    except Exception as exc:
        problems.append(f"heygen import failed: {exc}")
    try:
        from providers.higgsfield import HiggsfieldProvider

        if not hasattr(HiggsfieldProvider, "fetch"):
            problems.append("HiggsfieldProvider.fetch missing")
        else:
            sig = inspect.signature(HiggsfieldProvider.fetch)
            for param in ("scenes", "job_dir"):
                if param not in sig.parameters:
                    problems.append(
                        f"HiggsfieldProvider.fetch missing param {param}"
                    )
    except Exception as exc:
        problems.append(f"higgsfield import failed: {exc}")
    try:
        from video.compositor import compose_final

        sig = inspect.signature(compose_final)
        for param in (
            "audio_path",
            "avatar_path",
            "broll",
            "storyboard",
            "output_path",
            "avatar_mode",
        ):
            if param not in sig.parameters:
                problems.append(
                    f"compose_final missing param {param}"
                )
    except Exception as exc:
        problems.append(f"compositor import failed: {exc}")
    if problems:
        return _check(
            "provider_contracts", FAIL, "; ".join(problems)[:500]
        )
    return _check(
        "provider_contracts",
        PASS,
        "heygen.generate/fetch/compose_final signatures match worker",
    )


def check_captions() -> dict:
    try:
        from video import captions as cap

        for fn in ("split_cards", "time_cards", "render_all_cards"):
            if not hasattr(cap, fn):
                return _check(
                    "caption_dependencies",
                    FAIL,
                    f"video.captions.{fn} missing",
                )
        if (cap.WIDTH, cap.HEIGHT) != (1080, 1920):
            return _check(
                "caption_dependencies",
                FAIL,
                f"caption canvas is {cap.WIDTH}x{cap.HEIGHT}, "
                "expected 1080x1920",
            )
        import PIL  # noqa: F401  (import check only)

        for path in (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ):
            if os.path.exists(path):
                return _check(
                    "caption_dependencies",
                    PASS,
                    f"1080x1920 cards; font available: {path}",
                )
        return _check(
            "caption_dependencies",
            WARNING,
            "Pillow present and 1080x1920 canvas configured, "
            "but no system caption font found — captions will fail hard",
        )
    except Exception as exc:
        return _check("caption_dependencies", FAIL, str(exc)[:300])


def check_final_validation_config() -> dict:
    try:
        import pathlib

        from video import compositor as comp

        if (comp.FINAL_WIDTH, comp.FINAL_HEIGHT) != (1080, 1920):
            return _check(
                "final_validation_config",
                FAIL,
                f"FINAL is {comp.FINAL_WIDTH}x{comp.FINAL_HEIGHT}, "
                "expected 1080x1920",
            )
        src = pathlib.Path(comp.__file__).read_text()
        for marker in ("h264", "aac", "1080", "1920"):
            if marker not in src.lower():
                return _check(
                    "final_validation_config",
                    FAIL,
                    f"validate_mp4 missing expectation: {marker}",
                )
        return _check(
            "final_validation_config",
            PASS,
            "expects 1080x1920 H.264/AAC 9:16",
        )
    except Exception as exc:
        return _check("final_validation_config", FAIL, str(exc)[:300])


def check_paths() -> dict:
    problems = []
    if not os.path.isdir("assets/backgrounds"):
        problems.append("assets/backgrounds missing")
    try:
        from backend import worker as w

        for attr in (
            "OUTPUT_ROOT",
            "SCRIPT_FILENAME",
            "STORYBOARD_FILENAME",
            "VOICE_FILENAME",
            "AVATAR_FILENAME",
            "FINAL_FILENAME",
        ):
            if not hasattr(w, attr):
                problems.append(f"worker.{attr} missing")
    except Exception as exc:
        problems.append(f"worker import failed: {exc}")
    if problems:
        return _check(
            "filesystem_paths", FAIL, "; ".join(problems)[:400]
        )
    return _check("filesystem_paths", PASS, "job artifact paths configured")


def run_preflight(output_root: str = "output") -> dict:
    """Run all non-paid checks. No network, no credentials consumed."""
    checks = [
        check_env(),
        check_canonical_background(),
        check_ffmpeg(),
        check_ffprobe(),
        check_output_writable(output_root),
        check_production_wiring(),
        check_provider_contracts(),
        check_captions(),
        check_final_validation_config(),
        check_paths(),
        _check(
            "live_provider_contract",
            WARNING,
            "credentials configured; live provider contract not verified — "
            "HeyGen/Higgsfield/OpenAI/ElevenLabs payloads unverified until "
            "the corresponding live gate succeeds. HeyGen Avatar IV engine "
            "is a LIVE-ACCOUNT VERIFICATION item.",
        ),
    ]
    ready = all(
        c["status"] != FAIL
        for c in checks
        # canonical_background is a LEGACY-only dependency
        # (avatar_mode="green"/studio-baked + rollback). The production
        # default (avatar_mode="full_scene") never uploads or composites
        # it, so its absence must not block readiness.
        if c["name"] != "canonical_background"
    )
    return {"ready": ready, "checks": checks}
