"""B-roll contract regression tests (no network, no ffmpeg, no paid calls).

Covers:
1. broll_required=false → avatar-only composition is valid.
2. broll_required=true + valid mapped clip → B-roll segment is used.
3. broll_required=true + missing/unmapped → CompositorError, no fallback,
   no final/.partial artifact left behind.
4. Selection contract: rescale preserves B-roll intent/IDs; worker rejects
   a clip map that does not belong to the composed storyboard.
"""

import os
from unittest.mock import patch

import pytest

from backend import worker as worker_mod
from backend.store import JobStore
from backend.worker import (
    StageFailure,
    rescale_storyboard_to_duration,
    run_job,
)
from pipeline.models import Scene, Storyboard
from tests.fakes import StagedFakePipeline, make_storyboard
from video import compositor
from video.compositor import CompositorError, compose_final


def _avatar_probe(duration=20.0):
    return {
        "format": {"duration": str(duration)},
        "streams": [
            {"codec_type": "video", "codec_name": "h264",
             "width": 720, "height": 1280},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }


def _final_probe(duration=20.0):
    return {
        "format": {"duration": str(duration)},
        "streams": [
            {"codec_type": "video", "codec_name": "h264",
             "width": 1080, "height": 1920, "pix_fmt": "yuv420p"},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }


def _run_compose(tmp_path, board, broll):
    avatar = tmp_path / "avatar.mp4"
    avatar.write_bytes(b"avatarbytes")
    audio = tmp_path / "voice.mp3"
    audio.write_bytes(b"audiobytes")
    out = str(tmp_path / "final.mp4")
    captured = {}

    def fake_probe(path):
        if str(path).endswith("avatar.mp4"):
            return _avatar_probe()
        return _final_probe()

    def fake_run(cmd, **kwargs):
        # Two-pass assembly issues one ffmpeg argv per pass; assertions
        # read the concatenated argv so shape checks keep working.
        captured.setdefault("cmds", []).append(list(cmd))
        captured["cmd"] = [a for c in captured["cmds"] for a in c]
        with open(cmd[-1], "wb") as f:
            f.write(b"videobytes")
        return None

    def fake_render(segments, cap_dir, font_path=None):
        os.makedirs(cap_dir, exist_ok=True)
        paths = []
        for i in range(len(segments)):
            p = os.path.join(cap_dir, f"cap{i:02d}.png")
            with open(p, "wb") as f:
                f.write(b"png")
            paths.append(p)
        captured["segments"] = segments
        return paths

    with (
        patch.object(compositor, "probe_media", side_effect=fake_probe),
        patch.object(compositor.subprocess, "run", side_effect=fake_run),
        patch.object(compositor, "render_all_cards", side_effect=fake_render),
    ):
        result = compose_final(
            audio_path=str(audio),
            avatar_path=str(avatar),
            broll=broll,
            storyboard=board,
            output_path=out,
            job_dir=str(tmp_path),
            script_text="Hook. Point one.",
        )
    return result, captured


def _broll_board():
    board = make_storyboard()
    board.scenes[1].broll_required = True
    board.scenes[1].visual_prompt = "Cinematic vertical clip two, 9:16."
    return board


def test_avatar_only_valid_when_no_broll_required(tmp_path):
    out, captured = _run_compose(tmp_path, make_storyboard(), {})
    assert os.path.exists(out)
    joined = " ".join(captured["cmd"])
    # No B-roll input was consumed: only avatar + caption loops.
    assert "[1:v]trim" not in joined
    assert "[0:v]trim" in joined


def test_broll_segment_used_when_required_with_valid_clip(tmp_path):
    clip = tmp_path / "scene_2.mp4"
    clip.write_bytes(b"broll")
    out, captured = _run_compose(
        tmp_path, _broll_board(), {2: str(clip)}
    )
    assert os.path.exists(out)
    joined = " ".join(captured["cmd"])
    # The B-roll input (index 1) is trimmed from 0 for the scene window.
    assert "[1:v]trim=start=0:end=10.0" in joined
    # Avatar audio still mapped; B-roll audio never mapped.
    assert "0:a" in captured["cmd"]


def test_missing_broll_clip_fails_fast_without_fallback(tmp_path):
    board = _broll_board()
    with pytest.raises(CompositorError, match="Scene 2.*B-roll"):
        _run_compose(tmp_path, board, {})
    assert not os.path.exists(str(tmp_path / "final.mp4"))
    assert not os.path.exists(str(tmp_path / "final.mp4.partial"))


def test_unmapped_broll_clip_fails_fast(tmp_path):
    board = _broll_board()
    other = tmp_path / "scene_9.mp4"
    other.write_bytes(b"broll")
    with pytest.raises(CompositorError, match="Scene 2.*B-roll"):
        _run_compose(tmp_path, board, {9: str(other)})
    assert not os.path.exists(str(tmp_path / "final.mp4"))
    assert not os.path.exists(str(tmp_path / "final.mp4.partial"))


def test_broll_required_without_visual_prompt_fails_fast(tmp_path):
    board = make_storyboard()
    board.scenes[1].broll_required = True
    board.scenes[1].visual_prompt = "   "
    clip = tmp_path / "scene_2.mp4"
    clip.write_bytes(b"broll")
    with pytest.raises(CompositorError, match="visual_prompt"):
        _run_compose(tmp_path, board, {2: str(clip)})


def test_rescale_preserves_broll_intent_and_ids():
    board = _broll_board()
    out = rescale_storyboard_to_duration(board, 10.0)
    assert [s.scene_id for s in out.scenes] == [1, 2]
    assert out.scenes[1].broll_required is True
    assert out.scenes[1].visual_prompt == "Cinematic vertical clip two, 9:16."
    assert out.scenes[1].narration == board.scenes[1].narration


def _request(**overrides):
    body = {
        "topic": "T",
        "key_message": "Point one. Point two.",
        "language": "English",
        "duration_seconds": 60,
    }
    body.update(overrides)
    return body


def test_worker_rejects_fixture_clip_map_for_other_storyboard(tmp_path):
    """Orphan scene IDs (e.g. Gate 5 fixture clips) fail the job fast."""
    store = JobStore()
    job = store.create(_request())
    orphan_clip = os.path.join(str(tmp_path), "scene_99.mp4")
    with open(orphan_clip, "wb") as f:
        f.write(b"FAKE_BROLL_ORPHAN")

    class OrphanBroll:
        def fetch(self, storyboard, job_dir):
            return {99: orphan_clip}

    final = run_job(
        job["job_id"], _request(), store,
        pipeline_factory=lambda: StagedFakePipeline([]),
        avatar_provider=None,
        broll_provider=OrphanBroll(),
        compositor_fn=None,
        output_root=str(tmp_path),
    )
    assert final["status"] == "failed"
    assert "do not match" in final["error"]
    job_dir = os.path.join(str(tmp_path), job["job_id"])
    assert not os.path.exists(os.path.join(job_dir, "final.mp4"))
