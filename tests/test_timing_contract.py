"""Timing contract: storyboard windows must tile the MEASURED narration
duration, not the requested duration.

Gate 7 evidence: 30s requested → storyboard tiled [0, 30] while ElevenLabs
spoke the approved narration in 19.6s and HeyGen rendered 19.584s, so
scene 4 [22.83, 30.0] fell outside the avatar and composition correctly
failed. The fix reconciles scene timings to the ffprobe-measured voice
duration right after the voice stage (narrations/claims untouched).

All tests use fakes or stdlib-generated local media — zero paid calls.
"""

import json
import os
import shutil

import pytest

from backend import worker as w
from backend.store import JobStore
from backend.worker import rescale_storyboard_to_duration, run_job
from pipeline.models import Scene, Storyboard
from pipeline.validators import validate_storyboard
from tests.fakes import StagedFakePipeline, make_storyboard


def _gate7_like_board() -> Storyboard:
    """Mirrors the real Gate 7 storyboard shape: 30s requested, 4 scenes."""
    return Storyboard(
        scenes=[
            Scene(scene_id=1, start=0.0, end=5.87, narration="A",
                  key_claim="a", visual_type="text"),
            Scene(scene_id=2, start=5.87, end=10.44, narration="B",
                  key_claim="b", visual_type="text"),
            Scene(scene_id=3, start=10.44, end=22.83, narration="C",
                  key_claim="c", visual_type="text", avatar_required=True),
            Scene(scene_id=4, start=22.83, end=30.0, narration="D",
                  key_claim="d", visual_type="text", avatar_required=True),
        ],
        total_duration=30.0,
    )


def test_rescale_fits_measured_audio_duration():
    out = rescale_storyboard_to_duration(_gate7_like_board(), 19.6)
    assert out.total_duration == 19.6
    assert out.scenes[0].start == 0.0
    assert out.scenes[-1].end == 19.6
    # Proportional tiling preserved (scene 4 now fits inside 19.6s).
    assert out.scenes[3].start == pytest.approx(round(22.83 * 19.6 / 30.0, 3))
    assert out.scenes[3].end == 19.6
    for scene in out.scenes:
        assert scene.start < scene.end
    for cur, nxt in zip(out.scenes, out.scenes[1:]):
        assert cur.end <= nxt.start
    # Content untouched: narrations, claims, visuals, flags, order.
    src = _gate7_like_board()
    for before, after in zip(src.scenes, out.scenes):
        assert before.scene_id == after.scene_id
        assert before.narration == after.narration
        assert before.key_claim == after.key_claim
        assert before.visual_type == after.visual_type
        assert before.avatar_required == after.avatar_required
        assert before.broll_required == after.broll_required
    # Passes structural validation against the reconciled timeline.
    validate_storyboard(out, 30)


def test_rescale_is_noop_when_already_matching():
    board = make_storyboard()
    out = rescale_storyboard_to_duration(board, board.total_duration)
    assert out.total_duration == board.total_duration
    assert [(s.start, s.end) for s in out.scenes] == [
        (s.start, s.end) for s in board.scenes
    ]


def test_rescale_rejects_degenerate_inputs():
    with pytest.raises(ValueError):
        rescale_storyboard_to_duration(
            Storyboard(scenes=[], total_duration=0.0), 10.0
        )
    with pytest.raises(ValueError):
        rescale_storyboard_to_duration(make_storyboard(), 0.0)
    with pytest.raises(ValueError):
        rescale_storyboard_to_duration(make_storyboard(), -3.0)


def _request(**overrides):
    body = {
        "topic": "T",
        "key_message": "Point one. Point two.",
        "language": "English",
        "duration_seconds": 60,
    }
    body.update(overrides)
    return body


class RealWavVoicePipeline(StagedFakePipeline):
    """Writes a real, ffprobe-measurable WAV instead of FAKE bytes."""

    DURATION_S = 5

    def create_voice(self, script, audio_output_path):
        import wave

        self.calls.append("stage.create_voice")
        parent = os.path.dirname(audio_output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with wave.open(audio_output_path, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(44100)
            wav.writeframes(b"\x00" * 44100 * 2 * self.DURATION_S)
        return audio_output_path


def test_run_job_reconciles_storyboard_to_measured_voice(tmp_path):
    if shutil.which("ffprobe") is None:
        pytest.skip("ffprobe required to measure local audio")
    store = JobStore()
    job = store.create(_request())
    calls: list = []
    final = run_job(
        job["job_id"],
        _request(),
        store,
        pipeline_factory=lambda: RealWavVoicePipeline(calls),
        avatar_provider=None,
        broll_provider=None,
        compositor_fn=None,
        output_root=str(tmp_path),
    )
    assert final["status"] == "completed"
    # Fake storyboard tiled 20s; real voice measures 5s → must rescale.
    board = final["storyboard"]
    assert board["total_duration"] == pytest.approx(5.0)
    assert board["scenes"][0]["start"] == 0.0
    assert board["scenes"][-1]["end"] == pytest.approx(5.0)
    for scene in board["scenes"]:
        assert scene["end"] <= 5.0
    disk = json.load(
        open(os.path.join(str(tmp_path), job["job_id"], "storyboard.json"))
    )
    assert disk["total_duration"] == pytest.approx(5.0)


def test_run_job_preserves_fake_timelines_byte_for_byte(tmp_path):
    """Synthetic (FAKE-marked) audio skips reconciliation entirely."""
    store = JobStore()
    job = store.create(_request())
    calls: list = []
    final = run_job(
        job["job_id"],
        _request(),
        store,
        pipeline_factory=lambda: StagedFakePipeline(calls),
        avatar_provider=None,
        broll_provider=None,
        compositor_fn=None,
        output_root=str(tmp_path),
    )
    assert final["status"] == "completed"
    board = final["storyboard"]
    assert board["total_duration"] == 20.0
    assert [(s["start"], s["end"]) for s in board["scenes"]] == [
        (0.0, 10.0),
        (10.0, 20.0),
    ]
