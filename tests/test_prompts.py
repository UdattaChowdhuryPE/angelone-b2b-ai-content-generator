from pipeline.prompts import (
    SCRIPT_SYSTEM_PROMPT,
    STORYBOARD_SYSTEM_PROMPT,
)


def test_prompts_exist():
    assert len(SCRIPT_SYSTEM_PROMPT) > 100
    assert len(STORYBOARD_SYSTEM_PROMPT) > 100
