from unittest.mock import MagicMock, patch

import pytest

from pipeline.models import (
    Script,
    ScriptValidation,
    Storyboard,
    VideoRequest,
    ScriptValidationError,
    StoryboardValidationError,
)
from pipeline.orchestrator import VideoPipeline


def _make_request(**overrides) -> VideoRequest:
    defaults = {
        "topic": "Test topic",
        "key_message": "Key point 1. Key point 2.",
        "language": "English",
        "duration_seconds": 60,
    }
    defaults.update(overrides)
    return VideoRequest(**defaults)


def _make_script() -> Script:
    return Script(
        title="Test Title",
        hook="Test hook.",
        body=["Point one.", "Point two."],
        takeaway="Takeaway.",
        full_script="Test hook. Point one. Point two. Takeaway.",
    )


def _make_valid_storyboard() -> Storyboard:
    from pipeline.models import Scene

    scenes = [
        Scene(
            scene_id=1,
            start=0.0,
            end=20.0,
            narration="Test hook.",
            key_claim="Hook claim",
            visual_type="text",
        ),
        Scene(
            scene_id=2,
            start=20.0,
            end=40.0,
            narration="Point one.",
            key_claim="Point one claim",
            visual_type="diagram",
        ),
        Scene(
            scene_id=3,
            start=40.0,
            end=60.0,
            narration="Point two. Takeaway.",
            key_claim="Point two claim",
            visual_type="diagram",
        ),
    ]
    return Storyboard(scenes=scenes, total_duration=60.0)


def _make_invalid_storyboard() -> Storyboard:
    from pipeline.models import Scene

    scenes = [
        Scene(
            scene_id=1,
            start=0.0,
            end=42.0,
            narration="Test hook.",
            key_claim="Hook claim",
            visual_type="text",
        ),
        Scene(
            scene_id=2,
            start=42.0,
            end=84.0,
            narration="Point one.",
            key_claim="Point one claim",
            visual_type="diagram",
        ),
    ]
    return Storyboard(scenes=scenes, total_duration=84.0)


def _make_script_validation(valid: bool = True) -> ScriptValidation:
    if valid:
        return ScriptValidation(valid=True, unsupported_claims=[])
    return ScriptValidation(
        valid=False, unsupported_claims=["Unsupported claim 1"]
    )


def _patch_pipeline():
    """Return context managers patching LLM, voice, and os.getenv."""
    import os

    real_getenv = os.getenv

    def fake_getenv(key, default=None):
        if key == "OPENAI_API_KEY":
            return "test-key-not-real"
        return real_getenv(key, default)

    return (
        patch("pipeline.orchestrator.LLMProvider"),
        patch("pipeline.orchestrator.ElevenLabsVoiceProvider"),
        patch("pipeline.orchestrator.os.getenv", side_effect=fake_getenv),
    )


def test_storyboard_retry_success():
    """First storyboard invalid, second valid. Correction prompt is passed."""
    mock_llm_cls, mock_voice_cls, mock_getenv = _patch_pipeline()

    with mock_llm_cls as MockLLM, mock_voice_cls, mock_getenv:
        pipeline = VideoPipeline()
        pipeline.llm = MockLLM()

        pipeline.llm.generate_script.return_value = _make_script()
        pipeline.llm.validate_script.return_value = _make_script_validation()
        pipeline.llm.generate_storyboard.side_effect = [
            _make_invalid_storyboard(),
            _make_valid_storyboard(),
        ]

        request = _make_request()
        result = pipeline.create_assets(request)

        assert result["script"] is not None
        assert result["storyboard"] is not None
        assert result["storyboard"].total_duration == 60.0

        assert pipeline.llm.generate_storyboard.call_count == 2

        second_call_kwargs = pipeline.llm.generate_storyboard.call_args_list[
            1
        ]
        assert second_call_kwargs[1]["correction_prompt"] is not None
        assert "CRITICAL RULES FOR CORRECTION" in second_call_kwargs[1][
            "correction_prompt"
        ]
        assert "Do NOT shorten" in second_call_kwargs[1]["correction_prompt"]


def test_storyboard_retry_exhausted():
    """Both attempts invalid. Raises StoryboardValidationError."""
    mock_llm_cls, mock_voice_cls, mock_getenv = _patch_pipeline()

    with mock_llm_cls as MockLLM, mock_voice_cls, mock_getenv:
        pipeline = VideoPipeline()
        pipeline.llm = MockLLM()

        pipeline.llm.generate_script.return_value = _make_script()
        pipeline.llm.validate_script.return_value = _make_script_validation()
        pipeline.llm.generate_storyboard.return_value = (
            _make_invalid_storyboard()
        )

        request = _make_request()
        with pytest.raises(
            StoryboardValidationError,
            match="Storyboard validation failed after 2 attempts",
        ):
            pipeline.create_assets(request)

        assert pipeline.llm.generate_storyboard.call_count == 2


def test_script_validation_stops_before_storyboard():
    """Script validation fails. Storyboard is never generated."""
    mock_llm_cls, mock_voice_cls, mock_getenv = _patch_pipeline()

    with mock_llm_cls as MockLLM, mock_voice_cls, mock_getenv:
        pipeline = VideoPipeline()
        pipeline.llm = MockLLM()

        pipeline.llm.generate_script.return_value = _make_script()
        pipeline.llm.validate_script.return_value = _make_script_validation(
            valid=False
        )

        request = _make_request()
        with pytest.raises(ScriptValidationError, match="Unsupported claims"):
            pipeline.create_assets(request)

        pipeline.llm.generate_storyboard.assert_not_called()


def test_storyboard_valid_first_attempt():
    """First storyboard valid. No retry, no correction_prompt."""
    mock_llm_cls, mock_voice_cls, mock_getenv = _patch_pipeline()

    with mock_llm_cls as MockLLM, mock_voice_cls, mock_getenv:
        pipeline = VideoPipeline()
        pipeline.llm = MockLLM()

        pipeline.llm.generate_script.return_value = _make_script()
        pipeline.llm.validate_script.return_value = _make_script_validation()
        pipeline.llm.generate_storyboard.return_value = (
            _make_valid_storyboard()
        )

        request = _make_request()
        result = pipeline.create_assets(request)

        assert result["storyboard"].total_duration == 60.0
        assert pipeline.llm.generate_storyboard.call_count == 1

        call_kwargs = pipeline.llm.generate_storyboard.call_args
        assert call_kwargs[1].get("correction_prompt") is None


def test_init_without_elevenlabs_keys_succeeds():
    """VideoPipeline() constructs fine when ElevenLabs env vars are missing."""
    import os

    real_getenv = os.getenv

    def fake_getenv(key, default=None):
        if key == "OPENAI_API_KEY":
            return "test-key-not-real"
        if key == "ELEVENLABS_API_KEY":
            return None
        if key == "ELEVENLABS_VOICE_ID":
            return None
        return real_getenv(key, default)

    with patch("pipeline.orchestrator.LLMProvider"), patch(
        "pipeline.orchestrator.os.getenv", side_effect=fake_getenv
    ):
        pipeline = VideoPipeline()
        assert pipeline.voice is None


def test_create_assets_without_voice_creds_raises_clear_error():
    """Voice stage fails with ValueError when ElevenLabs creds are missing."""
    import os

    real_getenv = os.getenv

    def fake_getenv(key, default=None):
        if key == "OPENAI_API_KEY":
            return "test-key-not-real"
        if key == "ELEVENLABS_API_KEY":
            return None
        if key == "ELEVENLABS_VOICE_ID":
            return None
        return real_getenv(key, default)

    with patch("pipeline.orchestrator.LLMProvider") as MockLLM, patch(
        "pipeline.orchestrator.os.getenv", side_effect=fake_getenv
    ):
        pipeline = VideoPipeline()
        pipeline.llm = MockLLM()

        pipeline.llm.generate_script.return_value = _make_script()
        pipeline.llm.validate_script.return_value = _make_script_validation()
        pipeline.llm.generate_storyboard.return_value = (
            _make_valid_storyboard()
        )

        request = _make_request()
        with pytest.raises(
            ValueError, match="ELEVENLABS"
        ):
            pipeline.create_assets(request)

        pipeline.llm.generate_storyboard.assert_called_once()
