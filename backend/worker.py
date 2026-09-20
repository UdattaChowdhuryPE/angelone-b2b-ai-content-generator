"""Synchronous job execution: queued -> processing -> completed/failed.

Production behavior runs the REAL chain (OpenAI script -> validation ->
storyboard -> ElevenLabs voice -> HeyGen avatar with canonical studio
background -> Higgsfield B-roll -> captioned 1080x1920 FFmpeg compose).
It NEVER invents a fake video artifact: video_url/artifact_path are set
only when the compositor returns an existing, validated final.mp4.
"""

import json
import os
import shutil
from collections.abc import Callable

from backend.store import (
    COMPLETED,
    FAILED,
    PROCESSING,
    STAGE_AVATAR,
    STAGE_BROLL,
    STAGE_COMPLETED,
    STAGE_COMPOSITION,
    STAGE_FAILED,
    STAGE_QUEUED,
    STAGE_SCRIPT,
    STAGE_SCRIPT_VALIDATION,
    STAGE_STORYBOARD,
    STAGE_VOICE,
)
from pipeline.models import Script, Storyboard, VideoRequest

#: Configurable artifact root. Local default is "output"; production mounts
#: a persistent volume and sets OUTPUT_DIR=/data/output. The per-job
#: layout ($OUTPUT_DIR/<job_id>/) is unchanged.
OUTPUT_ROOT = os.getenv("OUTPUT_DIR", "output")

SCRIPT_FILENAME = "script.json"
STORYBOARD_FILENAME = "storyboard.json"
VOICE_FILENAME = "voice.mp3"
AVATAR_FILENAME = "avatar.mp4"
BROLL_DIRNAME = "broll"
FINAL_FILENAME = "final.mp4"
RESULT_FILENAME = "result.json"

#: Sentinel for "wire the real production provider". Distinct from None
#: (which means "skip this stage" and is what smoke/fake wiring passes).
USE_REAL_PROVIDERS = object()


class JobConflictError(RuntimeError):
    """Raised when a job cannot accept a mutation (running/locked)."""


class RegenValidationError(ValueError):
    """Raised when a regenerated script fails claim validation.

    The previously stored valid script and downstream artifacts are left
    untouched — the caller must surface this as a 422, not a job failure.
    """

    def __init__(self, message: str, unsupported_claims: list | None = None):
        super().__init__(message)
        self.unsupported_claims = list(unsupported_claims or [])


class StageFailure(Exception):
    """Wraps a stage exception with the exact failed stage + operation.

    Raised internally so the outer handler can persist structured failure
    context and STOP without executing downstream stages. Never auto-retried
    (paid-provider cost safety) — the user retries explicitly.
    """

    def __init__(self, stage: str, operation: str, cause: Exception):
        super().__init__(str(cause))
        self.stage = stage
        self.operation = operation
        self.cause = cause


STAGE_DETAILS = {
    STAGE_QUEUED: "Queued — waiting to start.",
    STAGE_SCRIPT: "Writing script...",
    STAGE_SCRIPT_VALIDATION: "Validating script against key points...",
    STAGE_STORYBOARD: "Creating storyboard...",
    STAGE_VOICE: "Generating voice...",
    STAGE_AVATAR: "Generating presenter video...",
    STAGE_BROLL: "Generating B-roll...",
    STAGE_COMPOSITION: "Rendering final video...",
    STAGE_COMPLETED: "Completed.",
    STAGE_FAILED: "Failed.",
}


def default_pipeline_factory():
    from pipeline.orchestrator import VideoPipeline

    return VideoPipeline()


def default_avatar_provider():
    from providers.heygen import FullSceneAvatarProvider

    # Production default: complete-scene Photo Avatar (audio-only
    # upload, no-background Avatar IV render). The configured
    # HEYGEN_AVATAR_ID already contains presenter + studio + desk.
    return FullSceneAvatarProvider()


def default_broll_provider():
    from providers.higgsfield import HiggsfieldProvider

    return HiggsfieldProvider()


def default_compositor_fn(
    *,
    audio_path,
    avatar_path,
    broll,
    storyboard,
    job_dir,
    script_text=None,
) -> str:
    from video.compositor import compose_final

    return compose_final(
        audio_path=audio_path,
        avatar_path=avatar_path,
        broll=broll,
        storyboard=storyboard,
        output_path=os.path.join(job_dir, FINAL_FILENAME),
        job_dir=job_dir,
        script_text=script_text,
        # Production default: the HeyGen render IS the complete studio
        # scene — normalize the whole video, B-roll cuts, captions.
        # Never chromakey, never background replacement, never desk
        # foreground (see compose_final avatar_mode="full_scene").
        avatar_mode="full_scene",
    )


def _resolve_provider(value, factory):
    if value is USE_REAL_PROVIDERS:
        return factory()
    return value


def _job_dir(output_root: str, job_id: str) -> str:
    return os.path.join(output_root, job_id)


def _atomic_write_bytes(path: str, data: bytes) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


def _atomic_write_json(path: str, payload: dict) -> None:
    _atomic_write_bytes(path, json.dumps(payload, indent=2).encode())


def _set_stage(store, job_id: str, stage: str) -> None:
    store.set_stage(job_id, stage, STAGE_DETAILS.get(stage, stage))


def _persist_script(job_dir: str, script: Script) -> str:
    path = os.path.join(job_dir, SCRIPT_FILENAME)
    _atomic_write_json(path, script.model_dump())
    return path


def _persist_storyboard(job_dir: str, storyboard: Storyboard) -> str:
    path = os.path.join(job_dir, STORYBOARD_FILENAME)
    _atomic_write_json(path, storyboard.model_dump())
    return path


def load_stored_script(job_dir: str) -> Script | None:
    path = os.path.join(job_dir, SCRIPT_FILENAME)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return Script(**json.load(f))


def load_stored_storyboard(job_dir: str) -> Storyboard | None:
    path = os.path.join(job_dir, STORYBOARD_FILENAME)
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return Storyboard(**json.load(f))


def _clear_downstream(job_dir: str) -> None:
    for name in (VOICE_FILENAME, AVATAR_FILENAME, FINAL_FILENAME):
        path = os.path.join(job_dir, name)
        if os.path.exists(path):
            os.remove(path)
        partial = path + ".partial"
        if os.path.exists(partial):
            os.remove(partial)
    for name in (BROLL_DIRNAME, "captions"):
        path = os.path.join(job_dir, name)
        if os.path.isdir(path):
            shutil.rmtree(path)


def _final_is_valid(job_dir: str) -> bool:
    from video.compositor import validate_mp4

    final = os.path.join(job_dir, FINAL_FILENAME)
    if not os.path.exists(final):
        return False
    try:
        validate_mp4(final)
        return True
    except Exception:
        return False


#: Ordered paid/local stages. Used to compute skipped_stages on failure.
STAGE_ORDER = [
    STAGE_SCRIPT,
    STAGE_SCRIPT_VALIDATION,
    STAGE_STORYBOARD,
    STAGE_VOICE,
    STAGE_AVATAR,
    STAGE_BROLL,
    STAGE_COMPOSITION,
]

# Map fine-grained failure points to the canonical stage name reported
# to the frontend.
_STAGE_ALIASES = {
    STAGE_SCRIPT: STAGE_SCRIPT,
    STAGE_SCRIPT_VALIDATION: STAGE_SCRIPT_VALIDATION,
    STAGE_STORYBOARD: STAGE_STORYBOARD,
    STAGE_VOICE: STAGE_VOICE,
    STAGE_AVATAR: STAGE_AVATAR,
    STAGE_BROLL: STAGE_BROLL,
    STAGE_COMPOSITION: STAGE_COMPOSITION,
}


def _is_synthetic_test_artifact(path: str) -> bool:
    """True for FAKE-marked text placeholders used by tests only.

    Production artifacts are real encoded media and never contain the
    FAKE marker. Skipping ffprobe for synthetic placeholders keeps the
    default pytest suite network/subprocess-free; real artifacts always
    go through ffprobe validation (which fails hard without ffprobe).
    """
    try:
        with open(path, "rb") as f:
            head = f.read(4096)
        return b"FAKE" in head
    except OSError:
        return False


def _require_ffprobe(operation: str) -> None:
    if shutil.which("ffprobe") is None:
        raise RuntimeError(
            f"{operation}: ffprobe not found on PATH — artifact "
            "validation requires ffprobe and fails hard. "
            "Run GET /api/preflight first."
        )


def validate_script_artifact(script: Script) -> None:
    if script is None:
        raise ValueError("script stage produced no Script object")
    if not (script.full_script or "").strip():
        raise ValueError("script stage produced an empty full_script")


def validate_storyboard_artifact(storyboard: Storyboard, duration: int) -> None:
    from pipeline.validators import validate_storyboard

    if storyboard is None:
        raise ValueError("storyboard stage produced no Storyboard object")
    if not storyboard.scenes:
        raise ValueError("storyboard stage produced zero scenes")
    validate_storyboard(storyboard, duration)


def rescale_storyboard_to_duration(
    storyboard: Storyboard, target_duration: float
) -> Storyboard:
    """Reconcile scene timings to measured media duration (upstream fix).

    Timing contract: the LLM drafts scene windows against the *requested*
    duration, but real TTS speaks the approved narration in its own time
    (e.g. 30s requested → 19.6s of actual audio; HeyGen then renders the
    audio 1:1). The measured voice-audio duration is therefore the
    authoritative timeline — every downstream stage (avatar windows,
    B-roll metadata, captions, composition) must tile it.

    This proportionally rescales all scene start/end timestamps (and the
    total) to ``target_duration``, preserving relative proportions,
    scene order, narrations, claims, visuals, and flags. It never drops,
    merges, clamps, or ignores scenes. Callers must re-validate the
    result with ``validate_storyboard_artifact``.
    """
    from pipeline.models import Storyboard as StoryboardModel

    if storyboard is None or not storyboard.scenes:
        raise ValueError("cannot rescale an empty storyboard")
    if not target_duration or target_duration <= 0:
        raise ValueError(
            f"cannot rescale to non-positive duration: {target_duration}"
        )
    source_total = float(storyboard.total_duration or 0.0)
    if source_total <= 0:
        raise ValueError(
            f"cannot rescale from non-positive duration: {source_total}"
        )
    factor = float(target_duration) / source_total
    scenes = []
    for scene in storyboard.scenes:
        start = round(float(scene.start) * factor, 3)
        end = round(float(scene.end) * factor, 3)
        if end <= start:
            raise ValueError(
                f"rescale collapses scene {scene.scene_id} "
                f"[{scene.start}, {scene.end}] → [{start}, {end}]"
            )
        scenes.append(scene.model_copy(update={"start": start, "end": end}))
    scenes[0] = scenes[0].model_copy(update={"start": 0.0})
    scenes[-1] = scenes[-1].model_copy(
        update={"end": round(float(target_duration), 3)}
    )
    return StoryboardModel(
        scenes=scenes, total_duration=round(float(target_duration), 3)
    )


def _measure_audio_duration(audio_path: str) -> float:
    """ffprobe-measured narration duration — the authoritative timeline."""
    from video.compositor import probe_media

    try:
        info = probe_media(audio_path)
        duration = float(info.get("format", {}).get("duration", 0.0))
    except Exception as exc:
        raise RuntimeError(
            f"Could not measure voice duration ({audio_path}): {exc}"
        ) from exc
    if duration <= 1.0:
        raise ValueError(
            f"Voice duration unreasonable ({duration}s): {audio_path}"
        )
    return duration


def validate_voice_artifact(audio_path: str) -> None:
    if not audio_path or not os.path.exists(audio_path):
        raise FileNotFoundError(f"Voice audio missing: {audio_path}")
    if os.path.getsize(audio_path) == 0:
        raise ValueError(f"Voice audio is empty: {audio_path}")
    if _is_synthetic_test_artifact(audio_path):
        return  # test placeholder — no ffprobe, no network
    _require_ffprobe("voice")
    from video.compositor import probe_media

    try:
        info = probe_media(audio_path)
    except Exception as exc:
        raise RuntimeError(
            f"Voice audio failed media probe ({audio_path}): {exc}"
        ) from exc
    try:
        duration = float(info.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError):
        duration = 0.0
    if duration <= 1.0:
        raise ValueError(
            f"Voice audio duration unreasonable ({duration}s): {audio_path}"
        )


def validate_avatar_artifact(avatar_path: str | None) -> None:
    if not avatar_path or not os.path.exists(avatar_path):
        raise FileNotFoundError(f"Avatar video missing: {avatar_path}")
    if os.path.getsize(avatar_path) == 0:
        raise ValueError(f"Avatar video is empty: {avatar_path}")
    if _is_synthetic_test_artifact(avatar_path):
        return  # test placeholder — no ffprobe, no network
    _require_ffprobe("avatar")
    from video.compositor import probe_media

    try:
        info = probe_media(avatar_path)
    except Exception as exc:
        raise RuntimeError(
            f"Avatar video failed media probe ({avatar_path}): {exc}"
        ) from exc
    streams = info.get("streams", [])
    kinds = {s.get("codec_type") for s in streams}
    if "video" not in kinds:
        raise ValueError(
            f"Avatar has no video stream: {avatar_path} ({sorted(kinds)})"
        )
    video = next(s for s in streams if s.get("codec_type") == "video")
    width, height = video.get("width"), video.get("height")
    if width is not None and height is not None and height > 0:
        if width > height:
            raise ValueError(
                f"Avatar is not portrait ({width}x{height}): {avatar_path}"
            )


def validate_broll_artifact(broll_out, storyboard: Storyboard) -> None:
    from video.storyboard import get_broll_scenes

    required = get_broll_scenes(storyboard)
    if not required:
        return
    if isinstance(broll_out, dict):
        lookup = dict(broll_out)
    elif isinstance(broll_out, (list, tuple)):
        ordered = [p for p in broll_out if isinstance(p, str)]
        lookup = {
            s.scene_id: p
            for s, p in zip(
                sorted(required, key=lambda s: s.scene_id), ordered
            )
        }
    else:
        lookup = {}
    missing = [s.scene_id for s in required if s.scene_id not in lookup]
    if missing:
        raise FileNotFoundError(
            f"B-roll missing clips for scenes: {missing}"
        )
    for scene_id, clip in lookup.items():
        if not os.path.exists(clip):
            raise FileNotFoundError(
                f"B-roll clip missing for scene {scene_id}: {clip}"
            )
        if os.path.getsize(clip) == 0:
            raise ValueError(
                f"B-roll clip is empty for scene {scene_id}: {clip}"
            )
        if _is_synthetic_test_artifact(clip):
            continue
    # Real clips get a hard ffprobe duration sanity check.
    real_clips = [
        c for c in lookup.values() if not _is_synthetic_test_artifact(c)
    ]
    if real_clips:
        _require_ffprobe("broll")
        from video.compositor import probe_media

        for clip in real_clips:
            try:
                info = probe_media(clip)
                duration = float(
                    info.get("format", {}).get("duration", 0.0)
                )
            except Exception as exc:
                raise RuntimeError(
                    f"B-roll clip failed media probe ({clip}): {exc}"
                ) from exc
            if duration <= 1.0:
                raise ValueError(
                    f"B-roll clip duration unreasonable "
                    f"({duration}s): {clip}"
                )


def validate_captions_artifact(job_dir: str, script_text: str) -> None:
    from video.captions import HEIGHT as CAP_H
    from video.captions import WIDTH as CAP_W
    from video.captions import split_cards, time_cards

    if (CAP_W, CAP_H) != (1080, 1920):
        raise ValueError(
            f"Caption canvas is {CAP_W}x{CAP_H}, expected 1080x1920"
        )
    cards = split_cards(script_text)
    cap_dir = os.path.join(job_dir, "captions")
    if not os.path.isdir(cap_dir):
        raise FileNotFoundError(
            f"Caption PNG directory missing: {cap_dir}"
        )
    pngs = sorted(
        n for n in os.listdir(cap_dir) if n.endswith(".png")
    )
    if len(pngs) != len(cards):
        raise ValueError(
            f"Caption PNG count {len(pngs)} != card count {len(cards)}"
        )
    # Timing coverage: cards must tile the storyboard duration with no
    # gaps/overlaps and the cards must equal the script verbatim
    # (split_cards itself asserts verbatim equality).
    total = None
    for name in ("storyboard.json",):
        spath = os.path.join(job_dir, name)
        if os.path.exists(spath):
            with open(spath) as f:
                total = float(json.load(f).get("total_duration", 0.0) or 0.0)
    if total and total > 0:
        segments = time_cards(cards, total)
        if abs(segments[0]["start"] - 0.0) > 1e-6:
            raise ValueError("Caption timing does not start at 0s")
        if abs(segments[-1]["end"] - total) > 1e-3:
            raise ValueError("Caption timing does not cover full duration")
        for a, b in zip(segments, segments[1:]):
            if abs(a["end"] - b["start"]) > 1e-3:
                raise ValueError("Caption timing has gaps/overlaps")


def validate_final_artifact(artifact_path: str | None) -> None:
    if not artifact_path or not os.path.exists(artifact_path):
        raise RuntimeError(
            "Compositor returned no existing artifact; "
            "job is NOT marked completed."
        )
    if artifact_path.endswith(".partial"):
        raise RuntimeError(
            f"Refusing to ship a .partial file as final: {artifact_path}"
        )
    if _is_synthetic_test_artifact(artifact_path):
        return  # text placeholder from fake compositor in tests
    from video.compositor import validate_mp4

    validate_mp4(artifact_path)


def _completed_artifacts(job_dir: str) -> list:
    names = []
    for fname in (
        SCRIPT_FILENAME,
        STORYBOARD_FILENAME,
        VOICE_FILENAME,
        AVATAR_FILENAME,
    ):
        if os.path.exists(os.path.join(job_dir, fname)):
            names.append(fname)
    meta = os.path.join(job_dir, BROLL_DIRNAME, "metadata.json")
    if os.path.exists(meta):
        names.append(os.path.join(BROLL_DIRNAME, "metadata.json"))
    if os.path.exists(os.path.join(job_dir, FINAL_FILENAME)):
        names.append(FINAL_FILENAME)
    return names


def _skipped_stages(failed_stage: str) -> list:
    if failed_stage not in STAGE_ORDER:
        return []
    idx = STAGE_ORDER.index(failed_stage)
    return STAGE_ORDER[idx + 1 :]


def _build_storyboard(
    pipeline, request: VideoRequest, script: Script
) -> Storyboard:
    """Storyboard with claim-safe retry, for pipelines old and new."""
    if hasattr(pipeline, "create_storyboard"):
        return pipeline.create_storyboard(request, script)
    # Legacy fake path: single attempt + structural validation.
    from pipeline.validators import validate_storyboard

    storyboard = pipeline.llm.generate_storyboard(
        request, script, request.duration_seconds
    )
    validate_storyboard(storyboard, request.duration_seconds)
    return storyboard


def _build_script_validated(
    pipeline, request: VideoRequest
) -> tuple[Script, object]:
    """Generate + validate a script, preserving the no-invented-claims gate."""
    from pipeline.models import ScriptValidationError

    if hasattr(pipeline, "create_script"):
        script = pipeline.create_script(request)
        validation = pipeline.validate_script_or_raise(request, script)
        return script, validation
    script = pipeline.llm.generate_script(request)
    validation = pipeline.llm.validate_script(request, script)
    if not getattr(validation, "valid", False):
        raise ScriptValidationError(
            "Unsupported claims: "
            + ", ".join(getattr(validation, "unsupported_claims", []))
        )
    return script, validation


def _run_voice(
    pipeline, script: Script, audio_path: str
) -> str:
    if hasattr(pipeline, "create_voice"):
        return pipeline.create_voice(script, audio_path)
    voice = getattr(pipeline, "voice", None)
    if voice is None:
        from providers.voice import ElevenLabsVoiceProvider

        voice = ElevenLabsVoiceProvider()
    voice.generate(script.full_script, audio_path)
    return audio_path


def _reconcile_storyboard_to_audio(
    store,
    job_id: str,
    job_dir: str,
    storyboard: Storyboard,
    audio_path: str,
    requested_duration: int | None,
) -> Storyboard:
    """Fit scene windows to the measured narration duration.

    Synthetic test placeholders (FAKE marker) skip reconciliation so
    deterministic fake timelines are preserved byte-for-byte.
    """
    if _is_synthetic_test_artifact(audio_path):
        return storyboard
    audio_duration = _measure_audio_duration(audio_path)
    if abs(float(storyboard.total_duration) - audio_duration) <= 1e-3:
        return storyboard
    rescaled = rescale_storyboard_to_duration(storyboard, audio_duration)
    ceiling = rescaled.total_duration
    if requested_duration:
        ceiling = max(float(requested_duration), rescaled.total_duration)
    validate_storyboard_artifact(rescaled, ceiling)
    _persist_storyboard(job_dir, rescaled)
    store.update(job_id, storyboard=rescaled.model_dump())
    return rescaled


def _run_downstream(
    *,
    store,
    job_id: str,
    job_dir: str,
    script: Script,
    storyboard: Storyboard,
    pipeline,
    avatar_provider,
    broll_provider,
    compositor_fn,
    from_stage: str = STAGE_VOICE,
    audio_path: str | None = None,
    requested_duration: int | None = None,
) -> dict:
    """Voice -> avatar -> B-roll -> captioned compose. Returns summary."""
    order = [STAGE_VOICE, STAGE_AVATAR, STAGE_BROLL, STAGE_COMPOSITION]
    start = order.index(from_stage) if from_stage in order else 0

    audio_path = audio_path or os.path.join(job_dir, VOICE_FILENAME)
    avatar_out: str | None = None
    broll_out = None

    avatar_provider = _resolve_provider(
        avatar_provider, default_avatar_provider
    )
    broll_provider = _resolve_provider(broll_provider, default_broll_provider)
    compositor_fn = _resolve_provider(compositor_fn, lambda: default_compositor_fn)
    needs_audio = avatar_provider is not None or compositor_fn is not None

    if start <= 0:
        _set_stage(store, job_id, STAGE_VOICE)
        try:
            if not os.path.exists(audio_path):
                if hasattr(pipeline, "create_voice") or getattr(
                    pipeline, "voice", None
                ) is not None:
                    _run_voice(pipeline, script, audio_path)
                elif needs_audio:
                    # Legacy pipeline with no voice capability and no audio on
                    # disk — cannot proceed honestly.
                    _run_voice(pipeline, script, audio_path)
            if needs_audio and not os.path.exists(audio_path):
                raise FileNotFoundError(f"Voice audio missing: {audio_path}")
            if needs_audio or os.path.exists(audio_path):
                validate_voice_artifact(audio_path)
                storyboard = _reconcile_storyboard_to_audio(
                    store, job_id, job_dir, storyboard,
                    audio_path, requested_duration,
                )
        except StageFailure:
            raise
        except Exception as exc:
            raise StageFailure(
                STAGE_VOICE, "voice.generate", exc
            ) from exc

    if start <= 1:
        _set_stage(store, job_id, STAGE_AVATAR)
        try:
            if avatar_provider is not None:
                avatar_out = avatar_provider.generate(
                    audio_path, os.path.join(job_dir, AVATAR_FILENAME)
                )
            else:
                candidate = os.path.join(job_dir, AVATAR_FILENAME)
                avatar_out = (
                    candidate if os.path.exists(candidate) else None
                )
            if avatar_provider is not None:
                validate_avatar_artifact(avatar_out)
        except StageFailure:
            raise
        except Exception as exc:
            raise StageFailure(
                STAGE_AVATAR, "heygen.generate", exc
            ) from exc
    else:
        candidate = os.path.join(job_dir, AVATAR_FILENAME)
        avatar_out = candidate if os.path.exists(candidate) else None

    if start <= 2:
        _set_stage(store, job_id, STAGE_BROLL)
        try:
            if broll_provider is not None:
                fetch = getattr(broll_provider, "fetch", None) or getattr(
                    broll_provider, "generate", None
                )
                if fetch is not None:
                    broll_out = fetch(storyboard, job_dir)
            validate_broll_artifact(broll_out, storyboard)
        except StageFailure:
            raise
        except Exception as exc:
            raise StageFailure(
                STAGE_BROLL, "higgsfield.fetch", exc
            ) from exc
    if not broll_out:
        meta_path = os.path.join(job_dir, BROLL_DIRNAME, "metadata.json")
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                meta = json.load(f)
            broll_out = {
                item["scene_id"]: item["clip_path"]
                for item in meta
                if os.path.exists(item.get("clip_path", ""))
            }
        else:
            broll_out = {}

    # Same-storyboard guard: clips must belong to the composed storyboard.
    # A fixture/stale clip map for different scene IDs must never supply
    # another storyboard's composition — fail fast instead.
    if isinstance(broll_out, dict) and broll_out:
        from video.storyboard import get_broll_scenes

        composed_ids = {s.scene_id for s in (storyboard.scenes or [])}
        orphan = sorted(set(broll_out) - composed_ids)
        if orphan:
            raise StageFailure(
                STAGE_COMPOSITION,
                "compositor.compose_final",
                ValueError(
                    "B-roll clips for unknown scenes "
                    f"{orphan} do not match the composed storyboard "
                    f"(scenes {sorted(composed_ids)}) — refusing to "
                    "compose a fixture/stale clip map onto a different "
                    "storyboard."
                ),
            )

    artifact_path = None
    if start <= 3:
        _set_stage(store, job_id, STAGE_COMPOSITION)
        try:
            if compositor_fn is not None:
                artifact_path = compositor_fn(
                    audio_path=audio_path,
                    avatar_path=avatar_out,
                    broll=broll_out,
                    storyboard=storyboard,
                    job_dir=job_dir,
                    script_text=script.full_script,
                )
                # compose_final validates the MP4 itself; this is a second
                # existence gate so a compositor can never mark completion
                # without a real file on disk.
                validate_final_artifact(artifact_path)
                # Caption gate: PNGs must exist at 1080x1920 and cover the
                # timeline (skipped only for synthetic test placeholders).
                if not (
                    artifact_path
                    and _is_synthetic_test_artifact(artifact_path)
                ):
                    validate_captions_artifact(
                        job_dir, script.full_script
                    )
        except StageFailure:
            raise
        except Exception as exc:
            raise StageFailure(
                STAGE_COMPOSITION, "compositor.compose_final", exc
            ) from exc
    else:
        candidate = os.path.join(job_dir, FINAL_FILENAME)
        artifact_path = candidate if os.path.exists(candidate) else None

    if isinstance(broll_out, dict):
        broll_clips = sorted(broll_out.values())
    elif isinstance(broll_out, (list, tuple)):
        broll_clips = [p for p in broll_out if isinstance(p, str)]
    else:
        broll_clips = []

    caption_count = 0
    captions_log = os.path.join(job_dir, "captions")
    if os.path.isdir(captions_log):
        caption_count = len(
            [n for n in os.listdir(captions_log) if n.endswith(".png")]
        )

    return {
        "audio_path": audio_path,
        "avatar_path": avatar_out,
        "broll": broll_clips,
        "artifact_path": artifact_path,
        "caption_count": caption_count,
        "script_preview": script.full_script[:2000],
        "num_scenes": len(storyboard.scenes or []),
    }


def _finish_job(store, job_id: str, job_dir: str, summary: dict) -> dict:
    _atomic_write_json(os.path.join(job_dir, RESULT_FILENAME), summary)
    artifact_path = summary.get("artifact_path")
    ok = bool(artifact_path and os.path.exists(artifact_path))
    store.update(
        job_id,
        status=COMPLETED,
        error=None,
        error_type=None,
        failed_stage=None,
        operation=None,
        completed_artifacts=None,
        skipped_stages=None,
        video_url=(
            f"/api/videos/{job_id}/file" if ok else None
        ),
        artifact_path=artifact_path if ok else None,
        result=summary,
        current_stage=STAGE_COMPLETED,
        status_detail=STAGE_DETAILS[STAGE_COMPLETED],
    )
    return store.get(job_id)


def _fail_job(
    store,
    job_id: str,
    exc: Exception,
    *,
    stage: str | None = None,
    operation: str | None = None,
    job_dir: str | None = None,
) -> dict:
    """Persist a structured fail-fast failure and STOP.

    Records the exact failed stage, the operation (e.g.
    heygen.generate), the error type/message (never secrets — providers
    must not include API keys in exceptions), which artifacts completed,
    and which downstream stages were skipped. Downstream stages are never
    executed after this point.
    """
    if isinstance(exc, StageFailure):
        operation = operation or exc.operation
        stage = stage or exc.stage
        exc = exc.cause
    current = None
    try:
        current = (store.get(job_id) or {}).get("current_stage")
    except Exception:
        current = None
    failed_stage = stage or current or STAGE_FAILED
    if failed_stage not in STAGE_ORDER and failed_stage != STAGE_FAILED:
        failed_stage = STAGE_FAILED
    completed: list = []
    if job_dir is not None:
        try:
            completed = _completed_artifacts(job_dir)
        except Exception:
            completed = []
    skipped = _skipped_stages(failed_stage)
    error_type = type(exc).__name__
    store.update(
        job_id,
        status=FAILED,
        error=str(exc),
        error_type=error_type,
        failed_stage=failed_stage,
        operation=operation,
        completed_artifacts=completed,
        skipped_stages=skipped,
        current_stage=STAGE_FAILED,
        status_detail=f"{STAGE_DETAILS[STAGE_FAILED]} {exc}",
    )
    return store.get(job_id)


def run_job(
    job_id: str,
    request_dict: dict,
    store,
    pipeline_factory: Callable = default_pipeline_factory,
    avatar_provider=USE_REAL_PROVIDERS,
    broll_provider=USE_REAL_PROVIDERS,
    compositor_fn: Callable = USE_REAL_PROVIDERS,
    output_root: str = OUTPUT_ROOT,
) -> dict:
    """Execute a video job synchronously, driving queued→processing→completed/failed.

    Production behavior: runs VideoPipeline with a job-scoped audio path and
    writes script.json, storyboard.json, voice.mp3, avatar.mp4, broll/,
    captions/, final.mp4 and result.json. It NEVER invents a fake video
    artifact. video_url/artifact_path are set only when the compositor
    returns an existing, validated file.

    Provider args default to USE_REAL_PROVIDERS (real HeyGen/Higgsfield/
    compositor wiring, same as the API server). Pass None explicitly to
    skip a stage — that is what fake/test wiring does.
    """
    store.set_status(job_id, PROCESSING)
    _set_stage(store, job_id, STAGE_SCRIPT)
    job_dir = _job_dir(output_root, job_id)
    os.makedirs(job_dir, exist_ok=True)
    try:
        request = VideoRequest(**request_dict)
        pipeline = pipeline_factory()
        audio_path = os.path.join(job_dir, VOICE_FILENAME)

        if hasattr(pipeline, "create_script") and hasattr(
            pipeline, "create_storyboard"
        ):
            try:
                _set_stage(store, job_id, STAGE_SCRIPT)
                script, _ = _build_script_validated(pipeline, request)
                validate_script_artifact(script)
                _persist_script(job_dir, script)
                store.update(job_id, script=script.model_dump())
            except StageFailure:
                raise
            except Exception as exc:
                # Claim-gate failures land at script_validation so the
                # frontend can distinguish them from generation errors.
                from pipeline.models import ScriptValidationError

                stage = (
                    STAGE_SCRIPT_VALIDATION
                    if isinstance(exc, ScriptValidationError)
                    else STAGE_SCRIPT
                )
                raise StageFailure(
                    stage, "llm.generate_script", exc
                ) from exc

            _set_stage(store, job_id, STAGE_SCRIPT_VALIDATION)
            # Validation already enforced inside _build_script_validated;
            # the stage exists so the frontend can display the gate.
            try:
                _set_stage(store, job_id, STAGE_STORYBOARD)
                storyboard = _build_storyboard(pipeline, request, script)
                # NOTE: storyboard creation may use a second OpenAI call
                # for STRUCTURAL correction only (timestamps/sequencing).
                # It never regenerates financial claims and is the only
                # intentional additional LLM call — not a paid media retry.
                validate_storyboard_artifact(
                    storyboard, request.duration_seconds
                )
                _persist_storyboard(job_dir, storyboard)
                store.update(job_id, storyboard=storyboard.model_dump())
            except StageFailure:
                raise
            except Exception as exc:
                raise StageFailure(
                    STAGE_STORYBOARD, "llm.generate_storyboard", exc
                ) from exc
        else:
            # Legacy/fake pipelines honour create_assets() only.
            _set_stage(store, job_id, STAGE_SCRIPT)
            result = pipeline.create_assets(
                request, audio_output_path=audio_path
            )
            script = result.get("script")
            storyboard = result.get("storyboard")
            if script is not None:
                _persist_script(job_dir, script)
                store.update(job_id, script=script.model_dump())
            if storyboard is not None:
                _persist_storyboard(job_dir, storyboard)
                store.update(job_id, storyboard=storyboard.model_dump())

        summary = _run_downstream(
            store=store,
            job_id=job_id,
            job_dir=job_dir,
            script=script,
            storyboard=storyboard,
            pipeline=pipeline,
            avatar_provider=avatar_provider,
            broll_provider=broll_provider,
            compositor_fn=compositor_fn,
            from_stage=STAGE_VOICE,
            requested_duration=request.duration_seconds,
            audio_path=audio_path,
        )
        return _finish_job(store, job_id, job_dir, summary)
    except Exception as exc:  # failure lands in failed state, never raises
        partial = os.path.join(job_dir, FINAL_FILENAME + ".partial")
        if os.path.exists(partial):
            os.remove(partial)
        return _fail_job(store, job_id, exc, job_dir=job_dir)


def _regenerate_script_locked(
    job_id: str,
    store,
    job: dict,
    pipeline_factory: Callable = default_pipeline_factory,
    avatar_provider=USE_REAL_PROVIDERS,
    broll_provider=USE_REAL_PROVIDERS,
    compositor_fn: Callable = USE_REAL_PROVIDERS,
    output_root: str = OUTPUT_ROOT,
) -> dict:
    """Regen body. Lock is held by the caller; never raises RegenValidationError.

    Paid claim-gate failure is persisted as a failed job at script_validation
    (previous valid script + downstream artifacts preserved) so background
    callers surface it through normal job polling.
    """
    from pipeline.models import ScriptValidationError

    job_dir = _job_dir(output_root, job_id)
    try:
        store.set_status(job_id, PROCESSING)
        os.makedirs(job_dir, exist_ok=True)
        request = VideoRequest(**(job["request"] or {}))
        pipeline = pipeline_factory()

        _set_stage(store, job_id, STAGE_SCRIPT)
        try:
            script, _ = _build_script_validated(pipeline, request)
            validate_script_artifact(script)
        except ScriptValidationError as exc:
            raise RegenValidationError(
                f"Regenerated script failed validation: {exc}",
                unsupported_claims=[str(exc)],
            ) from exc

        # Validation passed: the new script becomes the starting point.
        _persist_script(job_dir, script)
        store.update(job_id, script=script.model_dump(), error=None)
        _clear_downstream(job_dir)

        _set_stage(store, job_id, STAGE_STORYBOARD)
        try:
            storyboard = _build_storyboard(pipeline, request, script)
            validate_storyboard_artifact(
                storyboard, request.duration_seconds
            )
        except Exception as exc:
            raise StageFailure(
                STAGE_STORYBOARD, "llm.generate_storyboard", exc
            ) from exc
        _persist_storyboard(job_dir, storyboard)
        store.update(job_id, storyboard=storyboard.model_dump())

        summary = _run_downstream(
            store=store,
            job_id=job_id,
            job_dir=job_dir,
            script=script,
            storyboard=storyboard,
            pipeline=pipeline,
            avatar_provider=avatar_provider,
            broll_provider=broll_provider,
            compositor_fn=compositor_fn,
            from_stage=STAGE_VOICE,
            requested_duration=request.duration_seconds,
        )
        return _finish_job(store, job_id, job_dir, summary)
    except RegenValidationError as exc:
        # Preserve the last good state; surface as a failed job (pollable),
        # never as an exception out of a background task.
        completed = _completed_artifacts(job_dir)
        store.update(
            job_id,
            status=FAILED,
            error=str(exc),
            error_type="RegenValidationError",
            failed_stage=STAGE_SCRIPT_VALIDATION,
            operation="llm.validate_script",
            completed_artifacts=completed or None,
            skipped_stages=_skipped_stages(STAGE_SCRIPT_VALIDATION),
            current_stage=STAGE_FAILED,
            status_detail=f"{STAGE_DETAILS[STAGE_FAILED]} {exc}",
        )
        return store.get(job_id)
    except Exception as exc:
        return _fail_job(store, job_id, exc, job_dir=job_dir)


def regenerate_script(
    job_id: str,
    store,
    pipeline_factory: Callable = default_pipeline_factory,
    avatar_provider=USE_REAL_PROVIDERS,
    broll_provider=USE_REAL_PROVIDERS,
    compositor_fn: Callable = USE_REAL_PROVIDERS,
    output_root: str = OUTPUT_ROOT,
) -> dict:
    """Generate a new validated script, then regenerate all downstream stages.

    Synchronous variant (direct calls). The HTTP API enqueues
    run_regenerate_script_bg instead so paid work never blocks a request.

    Provider args default to USE_REAL_PROVIDERS; pass None to skip a stage.
    """
    job = store.get(job_id)
    if job is None:
        raise KeyError(f"job not found: {job_id}")
    owner = f"regen-script-{job_id}"
    if not store.acquire_lock(job_id, owner):
        raise JobConflictError("job is currently running another operation")
    try:
        return _regenerate_script_locked(
            job_id,
            store,
            job,
            pipeline_factory,
            avatar_provider,
            broll_provider,
            compositor_fn,
            output_root,
        )
    finally:
        store.release_lock(job_id, owner)


def run_regenerate_script_bg(
    job_id: str,
    store,
    owner: str,
    pipeline_factory: Callable = default_pipeline_factory,
    avatar_provider=USE_REAL_PROVIDERS,
    broll_provider=USE_REAL_PROVIDERS,
    compositor_fn: Callable = USE_REAL_PROVIDERS,
    output_root: str = OUTPUT_ROOT,
) -> dict:
    """BackgroundTasks entry for script regeneration.

    The endpoint already holds ``owner``'s lock and marked the job
    processing. Never raises — failures persist into job state and the
    lock is always released. Paid stages are never auto-retried here.
    """
    try:
        job = store.get(job_id)
        if job is None:
            return {"job_id": job_id, "status": FAILED, "error": "job not found"}
        return _regenerate_script_locked(
            job_id,
            store,
            job,
            pipeline_factory,
            avatar_provider,
            broll_provider,
            compositor_fn,
            output_root,
        )
    except Exception as exc:  # safety net; body already maps known failures
        try:
            return _fail_job(
                store, job_id, exc, job_dir=_job_dir(output_root, job_id)
            )
        except Exception:
            return store.get(job_id)
    finally:
        store.release_lock(job_id, owner)


def _regenerate_storyboard_locked(
    job_id: str,
    store,
    job: dict,
    pipeline_factory: Callable = default_pipeline_factory,
    avatar_provider=USE_REAL_PROVIDERS,
    broll_provider=USE_REAL_PROVIDERS,
    compositor_fn: Callable = USE_REAL_PROVIDERS,
    output_root: str = OUTPUT_ROOT,
) -> dict:
    """Storyboard-regen body. Lock is held by the caller."""
    job_dir = _job_dir(output_root, job_id)
    try:
        store.set_status(job_id, PROCESSING)
        request = VideoRequest(**(job["request"] or {}))
        script = load_stored_script(job_dir)
        if script is None:
            raise RuntimeError(
                "No stored script to regenerate from; "
                "regenerate the script instead."
            )
        pipeline = pipeline_factory()

        _set_stage(store, job_id, STAGE_STORYBOARD)
        try:
            storyboard = _build_storyboard(pipeline, request, script)
            validate_storyboard_artifact(
                storyboard, request.duration_seconds
            )
        except Exception as exc:
            raise StageFailure(
                STAGE_STORYBOARD, "llm.generate_storyboard", exc
            ) from exc
        _persist_storyboard(job_dir, storyboard)
        store.update(job_id, storyboard=storyboard.model_dump(), error=None)
        _clear_downstream(job_dir)

        summary = _run_downstream(
            store=store,
            job_id=job_id,
            job_dir=job_dir,
            script=script,
            storyboard=storyboard,
            pipeline=pipeline,
            avatar_provider=avatar_provider,
            broll_provider=broll_provider,
            compositor_fn=compositor_fn,
            from_stage=STAGE_VOICE,
            requested_duration=request.duration_seconds,
        )
        return _finish_job(store, job_id, job_dir, summary)
    except Exception as exc:
        return _fail_job(store, job_id, exc, job_dir=job_dir)


def regenerate_storyboard(
    job_id: str,
    store,
    pipeline_factory: Callable = default_pipeline_factory,
    avatar_provider=USE_REAL_PROVIDERS,
    broll_provider=USE_REAL_PROVIDERS,
    compositor_fn: Callable = USE_REAL_PROVIDERS,
    output_root: str = OUTPUT_ROOT,
) -> dict:
    """New storyboard from the existing valid script; downstream regenerates.

    Synchronous variant (direct calls). The HTTP API enqueues
    run_regenerate_storyboard_bg instead so paid work never blocks a request.

    Provider args default to USE_REAL_PROVIDERS; pass None to skip a stage."""
    job = store.get(job_id)
    if job is None:
        raise KeyError(f"job not found: {job_id}")
    owner = f"regen-storyboard-{job_id}"
    if not store.acquire_lock(job_id, owner):
        raise JobConflictError("job is currently running another operation")
    try:
        return _regenerate_storyboard_locked(
            job_id,
            store,
            job,
            pipeline_factory,
            avatar_provider,
            broll_provider,
            compositor_fn,
            output_root,
        )
    finally:
        store.release_lock(job_id, owner)


def run_regenerate_storyboard_bg(
    job_id: str,
    store,
    owner: str,
    pipeline_factory: Callable = default_pipeline_factory,
    avatar_provider=USE_REAL_PROVIDERS,
    broll_provider=USE_REAL_PROVIDERS,
    compositor_fn: Callable = USE_REAL_PROVIDERS,
    output_root: str = OUTPUT_ROOT,
) -> dict:
    """BackgroundTasks entry for storyboard regeneration. Never raises."""
    try:
        job = store.get(job_id)
        if job is None:
            return {"job_id": job_id, "status": FAILED, "error": "job not found"}
        return _regenerate_storyboard_locked(
            job_id,
            store,
            job,
            pipeline_factory,
            avatar_provider,
            broll_provider,
            compositor_fn,
            output_root,
        )
    except Exception as exc:  # safety net; body already maps failures
        try:
            return _fail_job(
                store, job_id, exc, job_dir=_job_dir(output_root, job_id)
            )
        except Exception:
            return store.get(job_id)
    finally:
        store.release_lock(job_id, owner)


def _retry_locked(
    job_id: str,
    store,
    job: dict,
    pipeline_factory: Callable = default_pipeline_factory,
    avatar_provider=USE_REAL_PROVIDERS,
    broll_provider=USE_REAL_PROVIDERS,
    compositor_fn: Callable = USE_REAL_PROVIDERS,
    output_root: str = OUTPUT_ROOT,
) -> dict:
    """Retry body. Lock is held by the caller; reuses paid artifacts."""
    job_dir = _job_dir(output_root, job_id)
    try:
        if job.get("status") == COMPLETED and _final_is_valid(job_dir):
            return job
        store.set_status(job_id, PROCESSING)

        request = VideoRequest(**(job["request"] or {}))
        script = load_stored_script(job_dir)
        storyboard = load_stored_storyboard(job_dir)
        if script is None or storyboard is None:
            # Upstream artifacts missing/invalid — rerun the whole job so
            # paid stages are never rebuilt on top of corrupt inputs.
            # The caller's lock stays held (run_job takes no lock).
            return run_job(
                job_id,
                job["request"] or {},
                store,
                pipeline_factory=pipeline_factory,
                avatar_provider=avatar_provider,
                broll_provider=broll_provider,
                compositor_fn=compositor_fn,
                output_root=output_root,
            )

        pipeline = pipeline_factory()
        voice_path = os.path.join(job_dir, VOICE_FILENAME)
        avatar_path = os.path.join(job_dir, AVATAR_FILENAME)
        if not os.path.exists(voice_path):
            resume_from = STAGE_VOICE
        elif not os.path.exists(avatar_path):
            resume_from = STAGE_AVATAR
        elif not os.path.isdir(
            os.path.join(job_dir, BROLL_DIRNAME)
        ) or not [
            n
            for n in os.listdir(os.path.join(job_dir, BROLL_DIRNAME))
            if n.endswith(".mp4")
        ]:
            # Only re-fetch B-roll when the storyboard actually needs it.
            from video.storyboard import get_broll_scenes

            needs_broll = bool(get_broll_scenes(storyboard))
            resume_from = STAGE_BROLL if needs_broll else STAGE_COMPOSITION
        else:
            resume_from = STAGE_COMPOSITION

        _clear_partial_only(job_dir)
        summary = _run_downstream(
            store=store,
            job_id=job_id,
            job_dir=job_dir,
            script=script,
            storyboard=storyboard,
            pipeline=pipeline,
            avatar_provider=avatar_provider,
            broll_provider=broll_provider,
            compositor_fn=compositor_fn,
            from_stage=resume_from,
            requested_duration=request.duration_seconds,
        )
        return _finish_job(store, job_id, job_dir, summary)
    except Exception as exc:
        return _fail_job(store, job_id, exc, job_dir=job_dir)


def retry_job(
    job_id: str,
    store,
    pipeline_factory: Callable = default_pipeline_factory,
    avatar_provider=USE_REAL_PROVIDERS,
    broll_provider=USE_REAL_PROVIDERS,
    compositor_fn: Callable = USE_REAL_PROVIDERS,
    output_root: str = OUTPUT_ROOT,
) -> dict:
    """Resume from the first failed/missing stage, reusing paid artifacts.

    Synchronous variant (direct calls). The HTTP API enqueues run_retry_bg
    instead so paid work never blocks a request.

    Provider args default to USE_REAL_PROVIDERS; pass None to skip a stage."""
    job = store.get(job_id)
    if job is None:
        raise KeyError(f"job not found: {job_id}")
    owner = f"retry-{job_id}"
    if not store.acquire_lock(job_id, owner):
        raise JobConflictError("job is currently running another operation")
    try:
        return _retry_locked(
            job_id,
            store,
            job,
            pipeline_factory,
            avatar_provider,
            broll_provider,
            compositor_fn,
            output_root,
        )
    finally:
        store.release_lock(job_id, owner)


def run_retry_bg(
    job_id: str,
    store,
    owner: str,
    pipeline_factory: Callable = default_pipeline_factory,
    avatar_provider=USE_REAL_PROVIDERS,
    broll_provider=USE_REAL_PROVIDERS,
    compositor_fn: Callable = USE_REAL_PROVIDERS,
    output_root: str = OUTPUT_ROOT,
) -> dict:
    """BackgroundTasks entry for retry. Never raises; lock always released."""
    try:
        job = store.get(job_id)
        if job is None:
            return {"job_id": job_id, "status": FAILED, "error": "job not found"}
        return _retry_locked(
            job_id,
            store,
            job,
            pipeline_factory,
            avatar_provider,
            broll_provider,
            compositor_fn,
            output_root,
        )
    except Exception as exc:  # safety net; body already maps failures
        try:
            return _fail_job(
                store, job_id, exc, job_dir=_job_dir(output_root, job_id)
            )
        except Exception:
            return store.get(job_id)
    finally:
        store.release_lock(job_id, owner)


def recover_stale_jobs(store, output_root: str = OUTPUT_ROOT) -> list:
    """Mark jobs left 'processing' by a backend restart as safely failed.

    Clears their locks, preserves all on-disk artifacts (voice.mp3,
    avatar.mp4, B-roll are never deleted), and never invokes any provider.
    The operator resumes explicitly via the existing retry mechanism.
    Returns the recovered job ids.
    """
    recovered = []
    for job in store.all_jobs():
        if job.get("status") != PROCESSING:
            continue
        job_id = job["job_id"]
        prev_stage = job.get("current_stage") or STAGE_FAILED
        failed_stage = (
            prev_stage if prev_stage in STAGE_ORDER else STAGE_FAILED
        )
        job_dir = _job_dir(output_root, job_id)
        try:
            completed = _completed_artifacts(job_dir)
        except Exception:
            completed = []
        store.force_release_lock(job_id)
        store.update(
            job_id,
            status=FAILED,
            error=(
                f"Backend restarted during '{prev_stage}'. Paid stages were "
                "NOT automatically rerun. Artifacts preserved; use Retry "
                "to resume."
            ),
            error_type="RestartRecovery",
            failed_stage=failed_stage,
            operation=None,
            completed_artifacts=completed or None,
            skipped_stages=_skipped_stages(failed_stage),
            current_stage=STAGE_FAILED,
            status_detail=(
                f"{STAGE_DETAILS[STAGE_FAILED]} Backend restarted during "
                f"'{prev_stage}'."
            ),
        )
        recovered.append(job_id)
    return recovered


def _clear_partial_only(job_dir: str) -> None:
    for name in (FINAL_FILENAME + ".partial", RESULT_FILENAME + ".tmp"):
        path = os.path.join(job_dir, name)
        if os.path.exists(path):
            os.remove(path)
