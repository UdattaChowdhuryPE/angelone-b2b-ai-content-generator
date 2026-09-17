from openai import OpenAI

from pipeline.models import (
    VideoRequest,
    Script,
    ScriptValidation,
    Storyboard,
)
from pipeline.prompts import (
    SCRIPT_SYSTEM_PROMPT,
    STORYBOARD_SYSTEM_PROMPT,
)


class LLMProvider:
    def __init__(
        self,
        api_key: str,
        model: str = "gpt-4o-mini-2024-07-18",
    ):
        self.client = OpenAI(api_key=api_key)
        self.model = model

    def generate_script(
        self,
        request: VideoRequest,
    ) -> Script:
        prompt = f"""
Topic:

{request.topic}

Key points for the video:

{request.key_message or "No key points provided."}

Language:

{request.language}

Target duration:

{request.duration_seconds} seconds.

Write the complete spoken script.

IMPORTANT:
The key points above are the sole source of truth for the
financial content of the script.

Do not introduce new financial facts, statistics, numbers,
dates, companies, events, causes, effects, recommendations,
or investment opinions that are not contained in the key points.

You may:
- improve the hook
- simplify language
- improve transitions
- reorder the supplied points
- remove repetition
- make the script conversational
- add non-financial connective language

Preserve the meaning of the supplied key points.

Do not provide personalized investment advice.
"""

        response = self.client.responses.parse(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": SCRIPT_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            text_format=Script,
        )

        return response.output_parsed

    def validate_script(
        self,
        request: VideoRequest,
        script: Script,
    ) -> ScriptValidation:
        prompt = f"""
You are validating a financial video script.

USER'S ORIGINAL KEY POINTS:

{request.key_message or "No key points provided."}

GENERATED SCRIPT:

{script.full_script}

Your task is ONLY to determine whether the generated script
introduces financial content that was not supplied in the
original key points.

Do NOT judge whether the user's original claims are factually
correct.

Check whether the generated script introduces any unsupported:

- financial facts
- statistics
- numbers
- dates
- companies
- events
- financial indicators
- causes
- effects
- recommendations
- investment opinions

Also check whether the script materially changes the meaning,
scope, certainty, or causal relationship of any supplied claim.

Normal writing changes are allowed, including:

- rephrasing
- simplification
- transitions
- hooks
- sentence restructuring
- repetition removal
- conversational language

Return:

- valid = true if every financial claim in the generated
  script is supported by the original key points and the
  meaning of the supplied claims is preserved.
- valid = false if the script introduces a new financial claim
  or materially changes the meaning of a supplied claim.

If valid is false, list each unsupported or materially changed
claim in unsupported_claims.

If valid is true, return an empty unsupported_claims list.
"""

        response = self.client.responses.parse(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": """
You are a strict financial content validator.

Compare the generated script ONLY against the user's original
key points.

Do not fact-check the user's original key points.

Do not add your own financial knowledge.

Return the requested structured validation result.
""",
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            text_format=ScriptValidation,
        )

        return response.output_parsed

    def generate_storyboard(
        self,
        request: VideoRequest,
        script: Script,
        duration: int,
    ) -> Storyboard:
        prompt = f"""
Target duration: {duration} seconds.

USER'S ORIGINAL KEY POINTS:

{request.key_message or "No key points provided."}

APPROVED GENERATED SCRIPT:

{script.full_script}

Create a scene-by-scene storyboard.

For every scene, include:

1. The narration.
2. The key financial claim being communicated.
3. The visual type.
4. A specific visual description.
5. On-screen text where useful.

IMPORTANT:

The storyboard must be derived from the original key points
and the approved generated script.

The storyboard must not introduce financial information
that is not present in the original key points.

The visual should help the viewer understand the specific
claim being communicated.

Prefer:
- simple diagrams
- arrows
- explanatory animations
- charts only when supported by the supplied information
- relevant contextual footage
- text animations
- avatar scenes when direct explanation is useful

Avoid generic financial B-roll when a more specific visual
would communicate the idea better.

SCENE COVERAGE:

Every meaningful idea in the final script must appear in
exactly one storyboard scene.

Do not omit any narration.

Do not introduce new financial claims.

Prefer one meaningful financial idea per scene.

The concatenated narration across all storyboard scenes
must cover the complete final script.
"""

        response = self.client.responses.parse(
            model=self.model,
            input=[
                {
                    "role": "system",
                    "content": STORYBOARD_SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": prompt,
                },
            ],
            text_format=Storyboard,
        )

        return response.output_parsed