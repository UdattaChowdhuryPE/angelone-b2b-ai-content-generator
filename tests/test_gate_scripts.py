"""Gate-script guards: no gate executes without its explicit flag.

These tests never set any RUN_REAL_*_GATE flag to true (except where a
single test explicitly sets one flag to verify argument handling that
still performs zero paid calls). Any provider construction or HTTP call
raises immediately.
"""

import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest
import requests

GATES = {
    "gate_2_openai": "RUN_REAL_OPENAI_GATE",
    "gate_3_elevenlabs": "RUN_REAL_ELEVENLABS_GATE",
    "gate_4_heygen": "RUN_REAL_HEYGEN_GATE",
    "gate_5_higgsfield": "RUN_REAL_HIGGSFIELD_GATE",
    "gate_6_compositor": "RUN_REAL_COMPOSITOR_GATE",
}

ALL_FLAGS = list(GATES.values())


def _load(name):
    path = (
        pathlib.Path(__file__).resolve().parent.parent / "scripts" / f"{name}.py"
    )
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _clear_flags(monkeypatch):
    for flag in ALL_FLAGS:
        monkeypatch.delenv(flag, raising=False)


def _block_paid(monkeypatch):
    def _no_net(*a, **k):
        raise AssertionError("paid/network call blocked in gate tests")

    monkeypatch.setattr(requests, "post", _no_net)
    monkeypatch.setattr(requests, "get", _no_net)


def _no_construct(name):
    def _raise(*a, **k):
        raise AssertionError(f"{name} constructed without flag")

    return _raise


@pytest.mark.parametrize("gate", sorted(GATES))
def test_gate_refuses_without_flag(monkeypatch, tmp_path, gate, capsys):
    mod = _load(gate)
    _clear_flags(monkeypatch)
    _block_paid(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "pipeline.orchestrator.VideoPipeline", _no_construct("VideoPipeline")
    )
    monkeypatch.setattr(
        "providers.voice.ElevenLabsVoiceProvider",
        _no_construct("ElevenLabsVoiceProvider"),
    )
    monkeypatch.setattr(
        "providers.heygen.HeyGenAvatarProvider",
        _no_construct("HeyGenAvatarProvider"),
    )
    monkeypatch.setattr(
        "providers.higgsfield.HiggsfieldProvider",
        _no_construct("HiggsfieldProvider"),
    )
    argv = []
    if gate == "gate_4_heygen":
        argv = ["--audio", "whatever.mp3"]
    elif gate == "gate_6_compositor":
        argv = [
            "--voice", "v.mp3",
            "--avatar", "a.mp4",
            "--broll-dir", "b",
            "--storyboard-json", "s.json",
        ]
    assert mod.main(argv) == 2
    assert "REFUSING" in capsys.readouterr().out


def test_gate6_requires_explicit_artifacts(monkeypatch):
    mod = _load("gate_6_compositor")
    _clear_flags(monkeypatch)
    monkeypatch.setenv("RUN_REAL_COMPOSITOR_GATE", "true")
    with pytest.raises(SystemExit) as exc:
        mod.main([])
    assert exc.value.code == 2  # argparse: missing required args, no work done


def test_gate6_source_has_no_defaults_and_no_paid_imports():
    src = (
        pathlib.Path(__file__).resolve().parent.parent
        / "scripts"
        / "gate_6_compositor.py"
    ).read_text()
    assert src.count("required=True") >= 4
    for marker in (
        "providers.heygen",
        "providers.higgsfield",
        "providers.voice",
        "providers.llm",
        "openai",
        "elevenlabs",
        "requests",
    ):
        assert marker not in src


def test_gate4_missing_audio_fails_before_heygen(monkeypatch, tmp_path):
    mod = _load("gate_4_heygen")
    _clear_flags(monkeypatch)
    _block_paid(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_REAL_HEYGEN_GATE", "true")
    monkeypatch.setenv("HEYGEN_API_KEY", "dummy")
    monkeypatch.setenv("HEYGEN_AVATAR_ID", "dummy")
    monkeypatch.delenv("HEYGEN_BACKGROUND_IMAGE", raising=False)
    monkeypatch.setattr(
        "providers.heygen.HeyGenAvatarProvider",
        _no_construct("HeyGenAvatarProvider"),
    )
    assert mod.main(["--audio", str(tmp_path / "missing.mp3")]) == 1


def test_gate5_issues_exactly_one_generation(monkeypatch, tmp_path):
    mod = _load("gate_5_higgsfield")
    _clear_flags(monkeypatch)
    _block_paid(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_REAL_HIGGSFIELD_GATE", "true")
    monkeypatch.setenv("HF_API_KEY_ID", "dummy")
    monkeypatch.setenv("HF_API_KEY_SECRET", "dummy")

    fetch_calls: list = []

    class FakeHiggsfield:
        def fetch(self, scenes, job_dir):
            fetch_calls.append(scenes)
            clip = os.path.join(job_dir, "broll", "scene_1.mp4")
            os.makedirs(os.path.dirname(clip), exist_ok=True)
            with open(clip, "wb") as f:
                f.write(b"FAKE_BROLL_FOR_GATE_TESTS")
            return {1: clip}

    monkeypatch.setattr(
        "providers.higgsfield.HiggsfieldProvider", FakeHiggsfield
    )
    assert mod.main([]) == 0
    assert len(fetch_calls) == 1
    (scenes,) = fetch_calls
    broll_scenes = [s for s in scenes.scenes if s.broll_required]
    assert len(broll_scenes) == 1  # exactly one paid clip


def test_gate5_persists_storyboard_for_gate6(monkeypatch, tmp_path):
    """Regression: Gate 5 must write <output-dir>/storyboard.json so
    Gate 6's explicit --storyboard-json has a real artifact to consume.
    Mocked provider, tmp cwd — zero paid calls, zero network.
    """
    import json

    from pipeline.models import Storyboard

    mod = _load("gate_5_higgsfield")
    _clear_flags(monkeypatch)
    _block_paid(monkeypatch)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("RUN_REAL_HIGGSFIELD_GATE", "true")
    monkeypatch.setenv("HF_API_KEY_ID", "dummy")
    monkeypatch.setenv("HF_API_KEY_SECRET", "dummy")

    class FakeHiggsfield:
        def fetch(self, scenes, job_dir):
            clip = os.path.join(job_dir, "broll", "scene_1.mp4")
            os.makedirs(os.path.dirname(clip), exist_ok=True)
            with open(clip, "wb") as f:
                f.write(b"FAKE_BROLL_FOR_GATE_TESTS")
            return {1: clip}

    monkeypatch.setattr(
        "providers.higgsfield.HiggsfieldProvider", FakeHiggsfield
    )
    assert mod.main([]) == 0

    # The Gate 5 → Gate 6 contract: everything Gate 6 consumes exists.
    out_dir = tmp_path / "output" / "gates" / "gate5"
    storyboard_path = out_dir / "storyboard.json"
    clip_path = out_dir / "broll" / "scene_1.mp4"
    assert storyboard_path.is_file(), "Gate 5 must persist storyboard.json"
    assert clip_path.is_file()
    with open(storyboard_path) as f:
        reloaded = Storyboard(**json.load(f))
    assert len(reloaded.scenes) == 1
    assert reloaded.scenes[0].scene_id == 1
    assert reloaded.scenes[0].broll_required is True
    assert reloaded.scenes[0].visual_prompt == mod.FROZEN_VISUAL_PROMPT
    # Gate 6 must accept the persisted storyboard as --storyboard-json.
    gate6 = _load("gate_6_compositor")
    args = gate6.parse_args(
        [
            "--voice", "v.mp3",
            "--avatar", "a.mp4",
            "--broll-dir", str(out_dir),
            "--storyboard-json", str(storyboard_path),
        ]
    )
    assert args.storyboard_json == str(storyboard_path)


@pytest.mark.parametrize("gate", sorted(GATES))
def test_gate_has_no_automatic_retry(gate):
    src = (
        pathlib.Path(__file__).resolve().parent.parent
        / "scripts"
        / f"{gate}.py"
    ).read_text()
    assert "while " not in src
    assert "tenacity" not in src
    assert "for attempt" not in src
    assert "for retry" not in src


@pytest.mark.parametrize("gate", sorted(GATES))
def test_gate_invocable_from_repo_root_with_imports_resolving(gate):
    """Launch the real CLI (`python scripts/<gate>.py`, no flag) from the
    repo root. Exit 2 + REFUSING proves both the normal command structure
    works AND every top-level backend/pipeline/providers import resolved
    (an import failure would exit 1 with ModuleNotFoundError instead).
    No flag is set, so no paid code path is reachable.
    """
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    env = dict(os.environ)
    for flag in ALL_FLAGS:
        env.pop(flag, None)
    argv = [sys.executable, f"scripts/{gate}.py"]
    if gate == "gate_4_heygen":
        argv += ["--audio", "whatever.mp3"]
    elif gate == "gate_6_compositor":
        argv += [
            "--voice", "v.mp3",
            "--avatar", "a.mp4",
            "--broll-dir", "b",
            "--storyboard-json", "s.json",
        ]
    proc = subprocess.run(
        argv,
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 2, proc.stderr[-2000:]
    assert "REFUSING" in proc.stdout
    assert "ModuleNotFoundError" not in proc.stderr
