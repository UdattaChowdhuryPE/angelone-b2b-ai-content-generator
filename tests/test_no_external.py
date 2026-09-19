"""Guardrails: tests make no external calls and create no real videos."""

import os
import subprocess

import pytest
import requests


@pytest.fixture(autouse=True)
def _block_network_and_creds(monkeypatch):
    for key in (
        "OPENAI_API_KEY",
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID",
        "HEYGEN_API_KEY",
        "HEYGEN_AVATAR_ID",
        "HF_API_KEY_ID",
        "HF_API_KEY_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)

    def _no_net(*a, **k):
        raise AssertionError("network call blocked in tests")

    monkeypatch.setattr(requests, "post", _no_net)
    monkeypatch.setattr(requests, "get", _no_net)
    monkeypatch.setattr(subprocess, "run", _no_net)


def test_no_network_no_subprocess_env_used(tmp_path, monkeypatch):
    """Backend + fakes complete without creds, network, or ffmpeg."""
    monkeypatch.chdir(tmp_path)
    from backend.store import JobStore
    from backend.worker import run_job
    from tests.fakes import FakePipeline

    store = JobStore()
    req = {"topic": "T", "key_message": "Point one.", "language": "English", "duration_seconds": 30}
    job = store.create(req)
    final = run_job(job["job_id"], req, store,
                    pipeline_factory=lambda: FakePipeline([]),
                    avatar_provider=None,  # explicit skip (defaults are real)
                    broll_provider=None,
                    compositor_fn=None,
                    output_root="output")
    assert final["status"] == "completed"
    assert os.environ.get("OPENAI_API_KEY") is None


def test_no_real_videos_created(tmp_path):
    """No real video bytes under job output dirs.

    The worker names the avatar output avatar.mp4 even for fakes, so a
    placeholder *.mp4 may exist — but it must be fake text (FAKE marker,
    no MP4 magic), never a real encoded video.
    """
    from backend.store import JobStore
    from backend.worker import run_job
    from tests.fakes import FakeAvatar, FakeBroll, FakePipeline, make_fake_compositor

    store = JobStore()
    req = {"topic": "T", "key_message": "P.", "language": "English", "duration_seconds": 30}
    job = store.create(req)
    calls: list = []
    run_job(
        job["job_id"], req, store,
        pipeline_factory=lambda: FakePipeline(calls),
        avatar_provider=FakeAvatar(calls),
        broll_provider=FakeBroll(calls),
        compositor_fn=make_fake_compositor(calls),
        output_root=str(tmp_path),
    )
    for path in tmp_path.rglob("*.mp4"):
        with open(path, "rb") as f:
            head = f.read(12)
        assert not head.startswith(b"\x00\x00\x00\x18ftyp"), f"real MP4 found: {path}"
        with open(path, "rb") as f:
            assert b"FAKE" in f.read(), f"non-placeholder mp4 found: {path}"
    for path in tmp_path.rglob("*"):
        if path.is_file():
            with open(path, "rb") as f:
                head = f.read(12)
            assert not head.startswith(b"\x00\x00\x00\x18ftyp"), f"real MP4 found: {path}"
            assert b"FAKE" in head or path.suffix == ".json" or head.startswith(b"{")
