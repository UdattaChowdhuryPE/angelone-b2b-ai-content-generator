"""Test-only fakes. Never imported by production code."""

import os

from pipeline.models import Scene, Script, ScriptValidation, Storyboard


def make_script(full_script="Hook. Point one. Takeaway.") -> Script:
    return Script(
        title="Test Title",
        hook="Hook.",
        body=["Point one."],
        takeaway="Takeaway.",
        full_script=full_script,
    )


def make_storyboard() -> Storyboard:
    scenes = [
        Scene(scene_id=1, start=0.0, end=10.0, narration="Hook.",
              key_claim="Hook claim", visual_type="text"),
        Scene(scene_id=2, start=10.0, end=20.0, narration="Point one.",
              key_claim="Point one claim", visual_type="diagram"),
    ]
    return Storyboard(scenes=scenes, total_duration=20.0)


class FakeLLM:
    def __init__(self, calls: list | None = None):
        self.calls = calls if calls is not None else []

    def generate_script(self, request):
        self.calls.append("generate_script")
        return make_script()

    def validate_script(self, request, script):
        self.calls.append("validate_script")
        return ScriptValidation(valid=True, unsupported_claims=[])

    def generate_storyboard(self, request, script, duration, correction_prompt=None):
        self.calls.append("generate_storyboard")
        return make_storyboard()


class FakeVoice:
    def __init__(self, calls: list | None = None):
        self.calls = calls if calls is not None else []

    def generate(self, text: str, output_path: str) -> str:
        self.calls.append("voice.generate")
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(output_path, "wb") as f:
            f.write(b"FAKE_AUDIO_FOR_TESTS")
        return output_path


class FakeAvatar:
    def __init__(self, calls: list | None = None):
        self.calls = calls if calls is not None else []

    def generate(self, audio_path: str, output_path: str) -> str:
        self.calls.append("avatar.generate")
        parent = os.path.dirname(output_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(output_path, "w") as f:
            f.write("FAKE_AVATAR_FOR_TESTS")
        return output_path


class FakeBroll:
    def __init__(self, calls: list | None = None):
        self.calls = calls if calls is not None else []

    def fetch(self, scenes, job_dir: str):
        self.calls.append("broll.fetch")
        return [{"scene": 1, "status": "fake"}]


def make_fake_compositor(calls: list | None = None):
    """Return a compositor_fn writing a plain-text test artifact (never mp4)."""
    record = calls if calls is not None else []

    def _compose(
        *,
        audio_path,
        avatar_path,
        broll,
        job_dir,
        storyboard=None,
        script_text=None,
    ):
        record.append("compositor.render")
        out = os.path.join(job_dir, "artifact.txt")
        with open(out, "w") as f:
            f.write("FAKE_ARTIFACT_FOR_TESTS")
        return out

    _compose.calls = record
    return _compose


class FakePipeline:
    """Fake pipeline honouring VideoPipeline.create_assets signature."""

    def __init__(self, calls: list | None = None):
        self.calls = calls if calls is not None else []
        self.llm = FakeLLM(self.calls)
        self.voice = FakeVoice(self.calls)

    def create_assets(self, request, audio_output_path: str = "output/voice.mp3") -> dict:
        script = self.llm.generate_script(request)
        validation = self.llm.validate_script(request, script)
        assert validation.valid
        storyboard = self.llm.generate_storyboard(request, script, request.duration_seconds)
        self.voice.generate(script.full_script, audio_output_path)
        return {"script": script, "storyboard": storyboard, "broll": [], "audio_path": audio_output_path}


class FailingPipeline(FakePipeline):
    def __init__(self, error: Exception, calls: list | None = None):
        super().__init__(calls)
        self._error = error

    def create_assets(self, request, audio_output_path: str = "output/voice.mp3"):
        self.calls.append("generate_script")
        raise self._error


class StagedFakePipeline(FakePipeline):
    """Fake honouring the staged VideoPipeline interface (script/validate/
    storyboard/voice as separate methods) for regen/retry/stage tests."""

    def __init__(
        self,
        calls: list | None = None,
        scripts: list | None = None,
        storyboards: list | None = None,
        validation_valid: bool = True,
    ):
        super().__init__(calls)
        self._scripts = list(scripts or [])
        self._storyboards = list(storyboards or [])
        self._validation_valid = validation_valid
        self.validate_calls = 0

    def create_script(self, request):
        self.calls.append("stage.create_script")
        if self._scripts:
            return self._scripts.pop(0)
        return self.llm.generate_script(request)

    def validate_script_or_raise(self, request, script):
        from pipeline.models import ScriptValidationError

        self.calls.append("stage.validate_script")
        self.validate_calls += 1
        if not self._validation_valid:
            raise ScriptValidationError("Unsupported claims: fake bad claim")
        validation = self.llm.validate_script(request, script)
        return validation

    def create_storyboard(self, request, script):
        self.calls.append("stage.create_storyboard")
        if self._storyboards:
            return self._storyboards.pop(0)
        return self.llm.generate_storyboard(
            request, script, request.duration_seconds
        )

    def create_voice(self, script, audio_output_path: str):
        self.calls.append("stage.create_voice")
        return self.voice.generate(script.full_script, audio_output_path)
