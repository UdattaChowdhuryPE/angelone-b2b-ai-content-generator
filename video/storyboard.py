from pipeline.models import Storyboard


def get_broll_scenes(storyboard: Storyboard):
    return [
        scene
        for scene in storyboard.scenes
        if scene.broll_required and scene.visual_prompt
    ]
