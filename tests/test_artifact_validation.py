"""Artifact validation unit tests (no paid calls, no network)."""

import os

import pytest

from backend import worker as w
from pipeline.models import Scene, Storyboard
from tests.fakes import make_script, make_storyboard


def test_validate_script_rejects_empty():
    script = make_script("   ")
    with pytest.raises(ValueError):
        w.validate_script_artifact(script)
    with pytest.raises(ValueError):
        w.validate_script_artifact(None)


def test_validate_storyboard_rejects_overlap():
    scenes = [
        Scene(scene_id=1, start=0.0, end=10.0, narration="A",
              key_claim="a", visual_type="text"),
        Scene(scene_id=2, start=5.0, end=15.0, narration="B",
              key_claim="b", visual_type="text"),
    ]
    board = Storyboard(scenes=scenes, total_duration=15.0)
    with pytest.raises(Exception):
        w.validate_storyboard_artifact(board, 60)


def test_validate_voice_rejects_missing_and_empty(tmp_path):
    with pytest.raises(FileNotFoundError):
        w.validate_voice_artifact(str(tmp_path / "nope.mp3"))
    empty = tmp_path / "voice.mp3"
    empty.write_bytes(b"")
    with pytest.raises(ValueError):
        w.validate_voice_artifact(str(empty))


def test_validate_voice_accepts_synthetic_placeholder(tmp_path):
    # FAKE-marked placeholders must not touch ffprobe/subprocess.
    fake = tmp_path / "voice.mp3"
    fake.write_bytes(b"FAKE_AUDIO_FOR_TESTS")
    w.validate_voice_artifact(str(fake))


def test_validate_avatar_rejects_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        w.validate_avatar_artifact(str(tmp_path / "avatar.mp4"))


def test_validate_avatar_accepts_synthetic_placeholder(tmp_path):
    fake = tmp_path / "avatar.mp4"
    fake.write_text("FAKE_AVATAR_FOR_TESTS")
    w.validate_avatar_artifact(str(fake))


def test_validate_broll_requires_every_scene(tmp_path):
    scenes = [
        Scene(scene_id=1, start=0.0, end=5.0, narration="A",
              key_claim="a", visual_type="clip",
              visual_prompt="city", broll_required=True),
        Scene(scene_id=2, start=5.0, end=10.0, narration="B",
              key_claim="b", visual_type="clip",
              visual_prompt="market", broll_required=True),
    ]
    board = Storyboard(scenes=scenes, total_duration=10.0)
    clip1 = tmp_path / "scene_1.mp4"
    clip1.write_bytes(b"FAKE_BROLL")
    with pytest.raises(FileNotFoundError):
        w.validate_broll_artifact({1: str(clip1)}, board)


def test_validate_broll_passes_when_no_broll_required():
    w.validate_broll_artifact({}, make_storyboard())


def test_validate_final_rejects_partial_and_missing(tmp_path):
    with pytest.raises(RuntimeError):
        w.validate_final_artifact(None)
    with pytest.raises(RuntimeError):
        w.validate_final_artifact(str(tmp_path / "final.mp4.partial"))


def test_validate_final_accepts_synthetic_placeholder(tmp_path):
    fake = tmp_path / "artifact.txt"
    fake.write_text("FAKE_ARTIFACT_FOR_TESTS")
    w.validate_final_artifact(str(fake))


def test_require_ffprobe_fails_hard_when_missing(monkeypatch):
    monkeypatch.setattr(w.shutil, "which", lambda *_a, **_k: None)
    with pytest.raises(RuntimeError, match="ffprobe"):
        w._require_ffprobe("voice")


def test_skipped_stages_ordering():
    assert w._skipped_stages("avatar") == ["broll", "composition"]
    assert w._skipped_stages("script")[0] == "script_validation"
    assert w._skipped_stages("composition") == []
