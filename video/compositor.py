import json
import os
import subprocess

from video.captions import (
    FPS,
    HEIGHT,
    WIDTH,
    render_all_cards,
    split_cards,
    time_cards,
)

FINAL_WIDTH = 1080
FINAL_HEIGHT = 1920
FINAL_FPS = 30

#: Bound for the full final assembly render (single-shot, never retried).
FFMPEG_TIMEOUT_S = 600
#: Bound for media probing/validation. Failing fast beats hanging the worker.
FFPROBE_TIMEOUT_S = 60


class CompositorError(RuntimeError):
    """Raised when FFmpeg assembly fails. Never mark the job completed."""


class Mp4ValidationError(ValueError):
    """Raised when the rendered MP4 fails post-render validation."""


def render_vertical_video(
    avatar_video: str,
    output_path: str,
):

    command = [
        "ffmpeg",
        "-y",
        "-i",
        avatar_video,
        "-vf",
        (
            "scale=1080:1920:"
            "force_original_aspect_ratio=increase,"
            "crop=1080:1920"
        ),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        output_path,
    ]

    subprocess.run(
        command,
        check=True,
        timeout=FFMPEG_TIMEOUT_S,
    )

    return output_path


def probe_media(path: str, timeout: int = FFPROBE_TIMEOUT_S) -> dict:
    try:
        out = subprocess.run(
            [
                "ffprobe",
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                path,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise Mp4ValidationError(
            f"ffprobe timed out after {timeout}s for {path} — "
            "refusing to hang the worker"
        ) from exc
    return json.loads(out.stdout)


def _norm_filter() -> str:
    # Shared normalization for non-background assets (B-roll conform):
    # common fps + 1080x1920 pad + 9:16 DAR. Pad color is pinned to
    # black so conform padding can never render white side borders.
    # The studio/avatar path must NOT use this filter — see
    # _avatar_norm_filter() (proportional cover, no padding).
    return (
        f"fps={FINAL_FPS},"
        f"scale={FINAL_WIDTH}:{FINAL_HEIGHT}:"
        "force_original_aspect_ratio=decrease,"
        f"pad={FINAL_WIDTH}:{FINAL_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
        "format=yuv420p,setdar=9/16"
    )


def _avatar_norm_filter() -> str:
    # Studio/avatar normalization: proportional cover to exactly
    # 1080x1920 with minimal center crop. No padding, no white, no
    # distortion. For a correct full-bleed 720x1280 HeyGen avatar this
    # is a lossless x1.5 upscale (no crop); any baked-in pillarbox is
    # cropped away rather than preserved.
    return (
        f"fps={FINAL_FPS},"
        f"scale={FINAL_WIDTH}:{FINAL_HEIGHT}:"
        "force_original_aspect_ratio=increase,"
        f"crop={FINAL_WIDTH}:{FINAL_HEIGHT},"
        "format=yuv420p,setdar=9/16"
    )


def validate_mp4(
    path: str,
    expected_duration: float | None = None,
    duration_tolerance: float = 1.0,
) -> dict:
    """ffprobe validation for the final MP4. Raises Mp4ValidationError."""
    if not os.path.exists(path):
        raise Mp4ValidationError(f"Final video missing: {path}")
    if os.path.getsize(path) == 0:
        raise Mp4ValidationError(f"Final video is empty: {path}")
    try:
        info = probe_media(path)
    except subprocess.CalledProcessError as exc:
        raise Mp4ValidationError(
            f"ffprobe failed for {path}: {exc}"
        ) from exc
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        raise Mp4ValidationError(f"No video stream in {path}")
    if audio is None:
        raise Mp4ValidationError(
            f"No audio stream in {path} — narration must be preserved"
        )
    if video.get("codec_name") != "h264":
        raise Mp4ValidationError(
            f"Expected H.264 video, got {video.get('codec_name')}"
        )
    if audio.get("codec_name") != "aac":
        raise Mp4ValidationError(
            f"Expected AAC audio, got {audio.get('codec_name')}"
        )
    width, height = video.get("width"), video.get("height")
    if (width, height) != (FINAL_WIDTH, FINAL_HEIGHT):
        raise Mp4ValidationError(
            f"Expected resolution {FINAL_WIDTH}x{FINAL_HEIGHT}, "
            f"got {width}x{height} — never ship a lower-resolution final"
        )
    try:
        duration = float(info.get("format", {}).get("duration", 0.0))
    except (TypeError, ValueError) as exc:
        raise Mp4ValidationError(
            f"Could not read duration for {path}: {exc}"
        ) from exc
    if duration <= 1.0:
        raise Mp4ValidationError(
            f"Unreasonable duration ({duration}s) for {path}"
        )
    if expected_duration is not None and abs(
        duration - expected_duration
    ) > duration_tolerance:
        raise Mp4ValidationError(
            f"Duration {duration:.2f}s differs from expected "
            f"{expected_duration:.2f}s by more than "
            f"{duration_tolerance:.2f}s"
        )
    return {
        "path": path,
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name"),
        "resolution": f"{width}x{height}",
        "duration": duration,
    }


def _broll_lookup(broll) -> dict[int, str]:
    """Normalize broll ({scene_id: path} dict) to a dict."""
    if not broll:
        return {}
    if isinstance(broll, dict):
        return dict(broll)
    return {}


def _require_broll_clip(scene, lookup: dict[int, str]) -> str:
    """Return the usable B-roll clip for a broll_required scene.

    Raises CompositorError (never falls back to avatar) when the scene
    has no usable visual_prompt, no mapped clip, or a missing file.
    """
    if not (getattr(scene, "visual_prompt", None) or "").strip():
        raise CompositorError(
            f"Scene {scene.scene_id} sets broll_required=true but has no "
            "usable visual_prompt — B-roll intent is invalid."
        )
    clip = lookup.get(scene.scene_id)
    if not isinstance(clip, str) or not clip:
        raise CompositorError(
            f"Scene {scene.scene_id} requires B-roll but no clip is mapped "
            f"for it (mapped scene IDs: {sorted(lookup)})."
        )
    if not os.path.exists(clip):
        raise CompositorError(
            f"Scene {scene.scene_id} requires B-roll but clip is missing: "
            f"{clip}."
        )
    return clip


def compose_final(
    *,
    audio_path: str,
    avatar_path: str,
    broll,
    storyboard,
    output_path: str,
    job_dir: str | None = None,
    font_path: str | None = None,
    script_text: str | None = None,
) -> str:
    """Assemble the complete final video: avatar + B-roll + captions.

    - Video base: per-scene segments from the storyboard. Scenes with an
      available B-roll clip show B-roll (video-only); all other scenes show
      the avatar. Segments are normalized to 1080x1920/30fps/yuv420p and
      concatenated with hard cuts (proven V0.7/V0.9 pattern).
    - Audio: continuous from the avatar render (B-roll audio never mapped);
      falls back to audio_path (ElevenLabs voice) when the avatar has no
      audio stream. Narration is never lost.
    - Captions: verbatim cards from the approved script, burned in last via
      PNG overlay + enable=between(t,start,end) (proven V0.9 pattern).
    - Output: H.264 + AAC, yuv420p, +faststart, exactly 1080x1920,
      validated with ffprobe. Rendered atomically via .partial + rename.

    Returns output_path. Raises CompositorError / Mp4ValidationError.
    """
    if not avatar_path or not os.path.exists(avatar_path):
        raise CompositorError(f"Avatar video missing: {avatar_path}")
    scenes = list(getattr(storyboard, "scenes", []) or [])
    if not scenes:
        raise CompositorError("Storyboard has no scenes to compose.")
    # Approved narration for captions: explicit script text first, then
    # scene narrations in order. Never invented — always pipeline-derived.
    full_script = (script_text or "").strip()
    if not full_script:
        full_script = " ".join(
            (s.narration or "").strip() for s in scenes
        ).strip()
    if not full_script:
        raise CompositorError("No narration available for captions.")
    total_duration = float(
        getattr(storyboard, "total_duration", 0.0)
        or max(s.end for s in scenes)
    )

    avatar_info = probe_media(avatar_path)
    avatar_kinds = sorted(
        s.get("codec_type") for s in avatar_info.get("streams", [])
    )
    try:
        avatar_duration = float(avatar_info["format"].get("duration", 0.0))
    except (TypeError, ValueError):
        avatar_duration = total_duration
    if "video" not in avatar_kinds:
        raise CompositorError(
            f"Avatar has no video stream: {avatar_path} ({avatar_kinds})"
        )
    use_avatar_audio = "audio" in avatar_kinds
    if not use_avatar_audio and not (
        audio_path and os.path.exists(audio_path)
    ):
        raise CompositorError(
            "Avatar has no audio stream and no fallback voice audio "
            f"at {audio_path} — narration would be lost."
        )

    lookup = _broll_lookup(broll)
    # Ordered-list form (fetch() returns scene order): align the i-th clip
    # with the i-th B-roll scene when no explicit mapping is given.
    if not lookup and isinstance(broll, (list, tuple)):
        ordered = [p for p in broll if isinstance(p, str)]
        broll_scenes = [
            s for s in scenes if getattr(s, "broll_required", False)
        ]
        lookup = {
            s.scene_id: p for s, p in zip(broll_scenes, ordered)
        }
    clip_order: list[str] = []
    for scene in scenes:
        if not getattr(scene, "broll_required", False):
            continue
        clip = _require_broll_clip(scene, lookup)
        if clip not in clip_order:
            clip_order.append(clip)

    norm = _norm_filter()
    avatar_norm = _avatar_norm_filter()
    inputs = [avatar_path]
    clip_index: dict[str, int] = {}
    for clip in clip_order:
        if clip not in clip_index:
            clip_index[clip] = len(inputs)
            inputs.append(clip)

    parts: list[str] = []
    labels: list[str] = []
    for seg, scene in enumerate(scenes):
        start, end = float(scene.start), float(scene.end)
        if getattr(scene, "broll_required", False):
            clip = _require_broll_clip(scene, lookup)
            ci = clip_index[clip]
            parts.append(
                f"[{ci}:v]trim=start=0:end={end - start},"
                f"setpts=PTS-STARTPTS,{norm}[s{seg}]"
            )
        else:
            end = min(end, avatar_duration) if avatar_duration > 0 else end
            if end <= start:
                raise CompositorError(
                    f"Scene {scene.scene_id} window [{start}, {end}] "
                    "is outside the avatar duration "
                    f"{avatar_duration:.2f}s."
                )
            parts.append(
                f"[0:v]trim=start={start}:end={end},"
                f"setpts=PTS-STARTPTS,{avatar_norm}[s{seg}]"
            )
        labels.append(f"[s{seg}]")
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vbase]")

    # Captions last (FINAL layer over the assembled base).
    cards = split_cards(full_script)
    segments = time_cards(cards, total_duration)
    work_dir = job_dir or os.path.dirname(output_path) or "."
    cap_dir = os.path.join(work_dir, "captions")
    cap_paths = render_all_cards(segments, cap_dir, font_path=font_path)
    first_cap_input = len(inputs)
    current = "vbase"
    for i, segment in enumerate(segments):
        nxt = f"cap{i}"
        parts.append(
            f"[{current}][{first_cap_input + i}:v]overlay=0:0:"
            f"enable='between(t,{segment['start']},{segment['end']})'"
            f"[{nxt}]"
        )
        current = nxt

    cmd = ["ffmpeg", "-y", "-i", avatar_path]
    for clip in clip_order:
        cmd += ["-i", clip]
    fallback_audio_index: int | None = None
    if not use_avatar_audio:
        fallback_audio_index = len(inputs)
        cmd += ["-i", audio_path]
        inputs.append(audio_path)
    for path in cap_paths:
        cmd += [
            "-loop", "1", "-framerate", str(FINAL_FPS),
            "-t", f"{total_duration + 1:.2f}", "-i", path,
        ]
    audio_map = "0:a" if use_avatar_audio else f"{fallback_audio_index}:a"
    cmd += [
        "-filter_complex", ";".join(parts),
        "-map", f"[{current}]", "-map", audio_map,
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-pix_fmt", "yuv420p", "-r", str(FINAL_FPS),
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
        "-movflags", "+faststart", "-shortest",
        # Atomic renders target <name>.partial, whose extension ffmpeg
        # cannot infer a muxer from — pin the MP4 muxer explicitly.
        "-f", "mp4",
    ]

    parent = os.path.dirname(output_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    partial = output_path + ".partial"
    try:
        subprocess.run(cmd + [partial], check=True, timeout=FFMPEG_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        try:
            if os.path.exists(partial):
                os.remove(partial)
        finally:
            raise CompositorError(
                f"FFmpeg assembly timed out after {FFMPEG_TIMEOUT_S}s; "
                "job is NOT marked completed."
            ) from exc
    except subprocess.CalledProcessError as exc:
        try:
            if os.path.exists(partial):
                os.remove(partial)
        finally:
            raise CompositorError(
                f"FFmpeg assembly failed (exit {exc.returncode}); "
                "job is NOT marked completed."
            ) from exc

    expected = min(
        avatar_duration if avatar_duration > 0 else total_duration,
        total_duration if total_duration > 0 else avatar_duration,
    )
    validate_mp4(partial, expected_duration=expected or None)
    os.replace(partial, output_path)
    return output_path
