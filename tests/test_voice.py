from unittest.mock import MagicMock, patch

import os

import pytest
import requests

from pipeline.models import (
    Scene,
    Script,
    ScriptValidation,
    Storyboard,
    VideoRequest,
)
from pipeline.orchestrator import VideoPipeline
from providers.voice import ElevenLabsVoiceProvider


def _make_provider():
    return ElevenLabsVoiceProvider(
        api_key="test-api-key",
        voice_id="test-voice-id",
    )


def test_generate_posts_to_eleven_v3_and_writes_file(tmp_path):
    provider = _make_provider()
    output_path = str(tmp_path / "nested" / "voice.mp3")

    mock_response = MagicMock()
    mock_response.content = b"fake-audio-bytes"
    mock_response.raise_for_status = MagicMock()

    with patch(
        "providers.voice.requests.post", return_value=mock_response
    ) as mock_post:
        result = provider.generate("Hello world", output_path)

    assert result == output_path

    args, kwargs = mock_post.call_args
    assert args[0] == (
        "https://api.elevenlabs.io/v1/text-to-speech/test-voice-id"
    )
    assert kwargs["params"] == {"output_format": "mp3_44100_128"}
    assert kwargs["headers"]["xi-api-key"] == "test-api-key"
    assert kwargs["json"] == {"text": "Hello world", "model_id": "eleven_v3"}

    with open(output_path, "rb") as f:
        assert f.read() == b"fake-audio-bytes"


def test_generate_uses_env_vars():
    with patch.dict(
        os.environ,
        {
            "ELEVENLABS_API_KEY": "env-key",
            "ELEVENLABS_VOICE_ID": "env-voice",
        },
        clear=False,
    ):
        provider = ElevenLabsVoiceProvider()
    assert provider.api_key == "env-key"
    assert provider.voice_id == "env-voice"
    assert provider.model_id == "eleven_v3"


def test_generate_missing_api_key_raises():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
            ElevenLabsVoiceProvider(api_key=None, voice_id="v")


def test_generate_missing_voice_id_raises():
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError, match="ELEVENLABS_VOICE_ID"):
            ElevenLabsVoiceProvider(api_key="k", voice_id=None)


def test_generate_empty_text_raises(tmp_path):
    provider = _make_provider()
    with pytest.raises(ValueError, match="must not be empty"):
        provider.generate("   ", str(tmp_path / "voice.mp3"))


def test_generate_http_error_propagates(tmp_path):
    provider = _make_provider()
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = requests.HTTPError("bad")
    with patch(
        "providers.voice.requests.post", return_value=mock_response
    ):
        with pytest.raises(requests.HTTPError):
            provider.generate("Hello", str(tmp_path / "voice.mp3"))


def _make_request():
    return VideoRequest(
        topic="Test",
        key_message="Point one.",
        language="English",
        duration_seconds=30,
    )


def _make_script():
    return Script(
        title="T",
        hook="H",
        body=["Point one."],
        takeaway="Takeaway",
        full_script="Point one spoken.",
    )


def _make_storyboard():
    scene = Scene(
        scene_id=1,
        start=0.0,
        end=10.0,
        narration="Point one spoken.",
        key_claim="Point one.",
        visual_type="diagram",
    )
    return Storyboard(scenes=[scene], total_duration=10.0)


def _make_pipeline_with_mocks():
    with patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "test-openai-key",
            "ELEVENLABS_API_KEY": "test-eleven-key",
            "ELEVENLABS_VOICE_ID": "test-voice-id",
        },
    ):
        with patch(
            "pipeline.orchestrator.LLMProvider"
        ), patch("pipeline.orchestrator.ElevenLabsVoiceProvider"):
            pipeline = VideoPipeline()
    pipeline.llm = MagicMock()
    pipeline.llm.generate_script.return_value = _make_script()
    pipeline.llm.validate_script.return_value = ScriptValidation(valid=True)
    pipeline.llm.generate_storyboard.return_value = _make_storyboard()
    pipeline.voice = MagicMock()
    return pipeline


def test_pipeline_calls_voice_with_full_script_after_storyboard(tmp_path):
    pipeline = _make_pipeline_with_mocks()
    output_path = str(tmp_path / "voice.mp3")

    result = pipeline.create_assets(_make_request(), audio_output_path=output_path)

    pipeline.voice.generate.assert_called_once_with(
        "Point one spoken.", output_path
    )
    assert result["audio_path"] == output_path
    assert result["script"].full_script == "Point one spoken."
    assert result["storyboard"].total_duration == 10.0


def test_pipeline_uses_default_audio_path():
    pipeline = _make_pipeline_with_mocks()

    result = pipeline.create_assets(_make_request())

    pipeline.voice.generate.assert_called_once_with(
        "Point one spoken.", "output/voice.mp3"
    )
    assert result["audio_path"] == "output/voice.mp3"
