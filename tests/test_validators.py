import pytest

from pipeline.models import Scene, Storyboard
from pipeline.validators import validate_storyboard


def _make_scene(scene_id: int, start: float, end: float) -> Scene:
    return Scene(
        scene_id=scene_id,
        start=start,
        end=end,
        narration=f"Narration {scene_id}",
        key_claim=f"Claim {scene_id}",
        visual_type="diagram",
    )


def _make_storyboard(scenes: list[Scene], total_duration: float) -> Storyboard:
    return Storyboard(scenes=scenes, total_duration=total_duration)


def test_valid_storyboard():
    scenes = [
        _make_scene(1, 0.0, 10.0),
        _make_scene(2, 10.0, 20.0),
        _make_scene(3, 20.0, 30.0),
    ]
    sb = _make_storyboard(scenes, total_duration=30.0)
    validate_storyboard(sb, requested_duration=30)


def test_total_duration_exceeds_requested():
    scenes = [
        _make_scene(1, 0.0, 20.0),
        _make_scene(2, 20.0, 40.0),
    ]
    sb = _make_storyboard(scenes, total_duration=40.0)
    with pytest.raises(ValueError, match="total duration"):
        validate_storyboard(sb, requested_duration=30)


def test_scene_start_equals_end():
    scenes = [
        _make_scene(1, 5.0, 5.0),
    ]
    sb = _make_storyboard(scenes, total_duration=30)
    with pytest.raises(ValueError, match="start.*must be before end"):
        validate_storyboard(sb, requested_duration=30)


def test_scene_start_greater_than_end():
    scenes = [
        _make_scene(1, 10.0, 5.0),
    ]
    sb = _make_storyboard(scenes, total_duration=30)
    with pytest.raises(ValueError, match="start.*must be before end"):
        validate_storyboard(sb, requested_duration=30)


def test_first_scene_not_at_zero():
    scenes = [
        _make_scene(1, 1.0, 10.0),
        _make_scene(2, 10.0, 20.0),
    ]
    sb = _make_storyboard(scenes, total_duration=20)
    with pytest.raises(ValueError, match="must start at 0s"):
        validate_storyboard(sb, requested_duration=20)


def test_overlapping_scenes():
    scenes = [
        _make_scene(1, 0.0, 15.0),
        _make_scene(2, 10.0, 25.0),
    ]
    sb = _make_storyboard(scenes, total_duration=25)
    with pytest.raises(ValueError, match="overlap"):
        validate_storyboard(sb, requested_duration=25)


def test_scene_exceeds_requested_duration():
    scenes = [
        _make_scene(1, 0.0, 20.0),
        _make_scene(2, 20.0, 40.0),
    ]
    sb = _make_storyboard(scenes, total_duration=40)
    with pytest.raises(ValueError, match="exceeds requested duration"):
        validate_storyboard(sb, requested_duration=30)


def test_non_sequential_scene_ids():
    scenes = [
        _make_scene(1, 0.0, 10.0),
        _make_scene(3, 10.0, 20.0),
    ]
    sb = _make_storyboard(scenes, total_duration=20)
    with pytest.raises(ValueError, match="not sequential"):
        validate_storyboard(sb, requested_duration=20)


def test_empty_storyboard():
    sb = _make_storyboard(scenes=[], total_duration=0)
    with pytest.raises(ValueError, match="no scenes"):
        validate_storyboard(sb, requested_duration=30)
