from pipeline.models import Storyboard, StoryboardValidationError


def validate_storyboard(storyboard: Storyboard, requested_duration: int) -> None:
    if not storyboard.scenes:
        raise StoryboardValidationError("Storyboard has no scenes")

    if storyboard.total_duration > requested_duration:
        raise StoryboardValidationError(
            f"Storyboard total duration ({storyboard.total_duration}s) "
            f"exceeds requested duration ({requested_duration}s)"
        )

    for scene in storyboard.scenes:
        if scene.start >= scene.end:
            raise StoryboardValidationError(
                f"Scene {scene.scene_id} has invalid timing: "
                f"start ({scene.start}s) must be before end ({scene.end}s)"
            )

    first = storyboard.scenes[0]
    if first.start != 0:
        raise StoryboardValidationError(
            f"First scene must start at 0s, but starts at {first.start}s"
        )

    for i in range(len(storyboard.scenes) - 1):
        current = storyboard.scenes[i]
        next_scene = storyboard.scenes[i + 1]
        if current.end > next_scene.start:
            raise StoryboardValidationError(
                f"Scenes {current.scene_id} and {next_scene.scene_id} overlap: "
                f"scene {current.scene_id} ends at {current.end}s but scene "
                f"{next_scene.scene_id} starts at {next_scene.start}s"
            )

    for scene in storyboard.scenes:
        if scene.end > requested_duration:
            raise StoryboardValidationError(
                f"Scene {scene.scene_id} ends at {scene.end}s, "
                f"which exceeds requested duration ({requested_duration}s)"
            )

    for i, scene in enumerate(storyboard.scenes):
        expected_id = i + 1
        if scene.scene_id != expected_id:
            raise StoryboardValidationError(
                f"Scene IDs are not sequential: expected scene ID {expected_id} "
                f"but got {scene.scene_id} at position {i + 1}"
            )
