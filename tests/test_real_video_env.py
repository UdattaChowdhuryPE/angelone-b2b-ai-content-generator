"""Regression: the real E2E test resolves credentials from the project
.env via the same load_dotenv() mechanism as app.py and scripts/* —
without weakening the credential assertion or arming paid runs.

All tests here run with RUN_REAL_VIDEO_TEST unset: zero paid calls,
zero network, and no secret value is ever printed or asserted on.
"""

import importlib
import os


def test_real_video_module_loads_dotenv_at_import(monkeypatch):
    """The module must invoke dotenv.load_dotenv() at import time (the
    same mechanism as app.py and scripts/*). load_dotenv() resolves the
    .env from the calling file's location, so the project .env is found
    regardless of pytest's cwd. Proved by intercepting the call — no
    real file is read and no secret is touched.
    """
    monkeypatch.delenv("RUN_REAL_VIDEO_TEST", raising=False)
    calls: list = []

    def _record(*a, **k):
        calls.append((a, k))
        return True

    import dotenv

    monkeypatch.setattr(dotenv, "load_dotenv", _record)
    import tests.test_real_video as mod

    importlib.reload(mod)
    assert len(calls) == 1
    assert mod.RUN_REAL is False


def test_paid_test_stays_skipped_without_flag():
    import tests.test_real_video as mod

    mark = mod.pytestmark.mark
    assert mark.name == "skipif"
    assert mark.kwargs.get("reason", "").startswith(
        "Set RUN_REAL_VIDEO_TEST=true"
    )


def test_required_credential_names_unweakened():
    import tests.test_real_video as mod

    assert mod.REQUIRED_ENV_VARS == [
        "OPENAI_API_KEY",
        "ELEVENLABS_API_KEY",
        "ELEVENLABS_VOICE_ID",
        "HEYGEN_API_KEY",
        "HEYGEN_AVATAR_ID",
        "HF_API_KEY_ID",
        "HF_API_KEY_SECRET",
    ]
