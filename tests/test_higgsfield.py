"""HiggsfieldProvider.fetch() with mocked HTTP (no real API calls)."""

import json
import os
from unittest.mock import patch

import pytest

from providers.higgsfield import (
    BROLL_MODEL,
    build_broll_payload,
    extract_video_url,
    HiggsfieldProvider,
)
from tests.fakes import make_storyboard


def _make_provider():
    with patch.dict(
        os.environ,
        {"HF_API_KEY_ID": "id", "HF_API_KEY_SECRET": "secret"},
        clear=False,
    ):
        return HiggsfieldProvider()


def _storyboard_broll():
    board = make_storyboard()
    board.scenes[0].broll_required = True
    board.scenes[0].visual_prompt = "Cinematic vertical clip one, 9:16."
    board.scenes[1].broll_required = True
    board.scenes[1].visual_prompt = "Cinematic vertical clip two, 9:16."
    return board


def test_build_payload_shape_and_no_narration():
    payload = build_broll_payload("Some visual prompt.")
    assert payload["prompt"] == "Some visual prompt."
    assert payload["duration"] == 5
    assert payload["aspect_ratio"] == "9:16"
    assert payload["resolution"] == "720p"
    assert payload["generate_audio"] is False
    blob = json.dumps(payload)
    assert "narr" not in blob.lower()


def test_build_payload_empty_prompt_rejected():
    with pytest.raises(ValueError):
        build_broll_payload("   ")


def test_extract_video_url_shapes():
    assert extract_video_url({"video_url": "http://a/v.mp4"}) == "http://a/v.mp4"
    assert extract_video_url({"data": {"url": "http://b/v.mp4"}}) == "http://b/v.mp4"
    assert extract_video_url({"video": {"url": "http://c/v.mp4"}}) == "http://c/v.mp4"
    assert extract_video_url({"data": {"outputs": ["http://d/v.mp4"]}}) == "http://d/v.mp4"
    assert extract_video_url({}) is None
    assert extract_video_url({"data": {}}) is None


def test_fetch_happy_path_scene_order_and_metadata(tmp_path):
    provider = _make_provider()
    board = _storyboard_broll()
    job_dir = str(tmp_path)

    submitted_ids = []

    def fake_submit(model, payload):
        assert model == BROLL_MODEL
        assert payload["prompt"] in (
            "Cinematic vertical clip one, 9:16.",
            "Cinematic vertical clip two, 9:16.",
        )
        rid = f"req-{len(submitted_ids)}"
        submitted_ids.append(rid)
        return {"request_id": rid}

    def fake_wait(request_id):
        return {"status": "completed", "video_url": f"http://x/{request_id}.mp4"}

    def fake_download(url, path):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"FAKEBROLL")
        return path

    with (
        patch.object(provider, "submit_video", side_effect=fake_submit),
        patch.object(provider, "wait_for_result", side_effect=fake_wait),
        patch("video.assets.download_file", side_effect=fake_download),
    ):
        clips = provider.fetch(board, job_dir)

    assert list(clips.keys()) == [1, 2]  # deterministic scene order
    assert clips[1].endswith("broll/scene_1.mp4")
    assert clips[2].endswith("broll/scene_2.mp4")
    assert os.path.exists(clips[1]) and os.path.exists(clips[2])
    meta = json.load(open(os.path.join(job_dir, "broll", "metadata.json")))
    assert [m["scene_id"] for m in meta] == [1, 2]
    assert meta[0]["request_id"] == "req-0"
    assert meta[0]["payload"]["prompt"] == "Cinematic vertical clip one, 9:16."


def test_fetch_skips_non_broll_scenes(tmp_path):
    provider = _make_provider()
    board = make_storyboard()  # no broll_required flags
    with (
        patch.object(provider, "submit_video") as submit,
        patch.object(provider, "wait_for_result"),
    ):
        clips = provider.fetch(board, str(tmp_path))
    assert clips == {}
    submit.assert_not_called()
    assert os.path.exists(os.path.join(str(tmp_path), "broll", "metadata.json"))


def test_fetch_accepts_storyboard_or_scene_list(tmp_path):
    provider = _make_provider()
    board = _storyboard_broll()
    with (
        patch.object(
            provider, "submit_video", return_value={"request_id": "r"}
        ) as submit,
        patch.object(
            provider,
            "wait_for_result",
            return_value={"status": "completed", "video_url": "http://x/v.mp4"},
        ),
        patch("video.assets.download_file", return_value="p"),
    ):
        by_board = provider.fetch(board, str(tmp_path))
        by_list = provider.fetch(board.scenes, str(tmp_path))
    assert list(by_board.keys()) == [1, 2]
    assert list(by_list.keys()) == [1, 2]
    assert submit.call_count == 4


def test_fetch_missing_request_id_raises(tmp_path):
    provider = _make_provider()
    with patch.object(provider, "submit_video", return_value={"weird": True}):
        with pytest.raises(RuntimeError, match="no request id"):
            provider.fetch(_storyboard_broll(), str(tmp_path))


def test_fetch_missing_video_url_raises(tmp_path):
    provider = _make_provider()
    with (
        patch.object(
            provider, "submit_video", return_value={"request_id": "r"}
        ),
        patch.object(
            provider, "wait_for_result", return_value={"status": "completed"}
        ),
    ):
        with pytest.raises(RuntimeError, match="no video URL"):
            provider.fetch(_storyboard_broll(), str(tmp_path))


def test_fetch_generation_failure_propagates(tmp_path):
    provider = _make_provider()
    with (
        patch.object(
            provider, "submit_video", return_value={"request_id": "r"}
        ),
        patch.object(
            provider,
            "wait_for_result",
            side_effect=RuntimeError("Higgsfield generation failed"),
        ),
    ):
        with pytest.raises(RuntimeError, match="failed"):
            provider.fetch(_storyboard_broll(), str(tmp_path))


def test_missing_credentials_raise():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="credentials"):
            HiggsfieldProvider()
