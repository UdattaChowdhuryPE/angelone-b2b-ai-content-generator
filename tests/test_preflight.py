"""Preflight tests: non-paid, no secrets, no provider calls."""

import json

from fastapi.testclient import TestClient


def _names(payload):
    return {c["name"]: c for c in payload["checks"]}


def test_preflight_pass_shape(monkeypatch, tmp_path):
    import backend.preflight as pf

    for key in pf.REQUIRED_ENV_VARS:
        monkeypatch.setenv(key, "dummy")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "assets" / "backgrounds").mkdir(parents=True)
    from PIL import Image

    Image.new("RGB", (720, 1280), "white").save(
        str(tmp_path / "assets" / "backgrounds" / "studio_background.png")
    )
    monkeypatch.setattr(pf.shutil, "which", lambda name: f"/usr/bin/{name}")
    data = pf.run_preflight(output_root=str(tmp_path / "output"))
    assert set(data) == {"ready", "checks"}
    by_name = _names(data)
    assert by_name["canonical_background"]["status"] == "PASS"
    assert by_name["ffmpeg"]["status"] == "PASS"
    assert by_name["ffprobe"]["status"] == "PASS"
    assert by_name["output_writable"]["status"] == "PASS"
    # Live contract must never be reported as verified.
    live = by_name["live_provider_contract"]
    assert live["status"] == "WARNING"
    assert "not verified" in live["detail"]
    # No secret values leak.
    assert "dummy" not in json.dumps(data)


def test_preflight_fails_on_missing_env(monkeypatch, tmp_path):
    import backend.preflight as pf

    for key in pf.REQUIRED_ENV_VARS:
        monkeypatch.delenv(key, raising=False)
    data = pf.run_preflight(output_root=str(tmp_path))
    assert data["ready"] is False
    by_name = _names(data)
    assert by_name["env_credentials_configured"]["status"] == "FAIL"


def test_preflight_fails_on_missing_background(monkeypatch, tmp_path):
    import backend.preflight as pf

    monkeypatch.chdir(tmp_path)  # no assets/ dir here
    result = pf.check_canonical_background()
    assert result["status"] == "FAIL"


def test_preflight_fails_when_ffprobe_missing(monkeypatch, tmp_path):
    import backend.preflight as pf

    def _which(name):
        return None if name == "ffprobe" else "/usr/bin/ffmpeg"

    monkeypatch.setattr(pf.shutil, "which", _which)
    assert pf.check_ffprobe()["status"] == "FAIL"
    assert pf.check_ffmpeg()["status"] == "PASS"


def test_preflight_endpoint_and_no_paid_calls(monkeypatch, tmp_path):
    """GET /api/preflight works and never touches paid HTTP APIs."""
    import backend.preflight as pf
    import requests

    for key in pf.REQUIRED_ENV_VARS:
        monkeypatch.setenv(key, "dummy")
    monkeypatch.chdir(tmp_path)
    (tmp_path / "assets" / "backgrounds").mkdir(parents=True)
    from PIL import Image

    Image.new("RGB", (720, 1280), "white").save(
        str(tmp_path / "assets" / "backgrounds" / "studio_background.png")
    )
    monkeypatch.setattr(pf.shutil, "which", lambda name: f"/usr/bin/{name}")

    calls = []
    orig_get = requests.get
    orig_post = requests.post

    def _guarded_post(*a, **k):
        calls.append("post")
        raise AssertionError("paid POST must never happen in preflight")

    monkeypatch.setattr(requests, "post", _guarded_post)

    from backend.app import create_app
    from backend.store import JobStore

    app = create_app(store=JobStore(), output_root=str(tmp_path))
    client = TestClient(app)
    resp = client.get("/api/preflight")
    assert resp.status_code == 200
    body = resp.json()
    assert "ready" in body and "checks" in body
    assert calls == []
    assert "dummy" not in resp.text
    # Restore sanity (monkeypatch reverts automatically).
    assert orig_get is not None and orig_post is not None
