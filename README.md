# AI Financial Video Generator

AI-powered pipeline for creating short-form financial videos from
expert-provided financial inputs.

The core V0 product principle is:

> **The expert provides the financial thinking. AI packages it into video content.**

The AI should not independently invent financial claims, facts,
statistics, recommendations, or market opinions.

---

## Product Goal

Turn expert-provided financial points into a short-form video through
a controlled generation pipeline:

```text
Expert Financial Input
        ↓
      Script
        ↓
 Script Validation
        ↓
   Approved Script
        ↓
   Storyboard
        ↓
      Voice (ElevenLabs mp3 — wired)
        ↓
 Avatar / B-roll / Final Video (scripts/ experiments only)
```

The production pipeline (`VideoPipeline.create_assets()`) currently
returns `{script, storyboard, broll: [], audio_path}`. Avatar, B-roll,
and final mp4 assembly live in isolated `scripts/` experiments (V0.4–V0.11c).

---

## Quickstart

```bash
# Install / sync deps (uv only, no pip)
uv sync

# Run the app
uv run streamlit run app.py

# Run all tests
uv run pytest
```

Requires Python 3.11+ and `ffmpeg` installed on the system.
Keys live in `.env` at the project root (`app.py` calls `load_dotenv()`).

## Environment

| Var | Required for |
| --- | --- |
| `OPENAI_API_KEY` | App + all LLM calls (`gpt-5-mini-2025-08-07`) |
| `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` | Voice stage of `create_assets()` (ElevenLabs `eleven_v3`, `mp3_44100_128`, default `output/voice.mp3`) |
| `HEYGEN_API_KEY` / `HEYGEN_AVATAR_ID` | HeyGen avatar experiments in `scripts/` only |
| `HF_API_KEY_ID` / `HF_API_KEY_SECRET` | Higgsfield B-roll experiments in `scripts/` only |

## Architecture

```
app.py                  Streamlit UI entrypoint
pipeline/
  models.py             Pydantic models (VideoRequest, Script, Storyboard, Scene)
  prompts.py            System prompts for script + storyboard generation
  validators.py         Storyboard validation (timing, coverage, sequencing)
  orchestrator.py       VideoPipeline — script → validate → storyboard (retry) → voice
providers/
  llm.py                OpenAI client (gpt-5-mini-2025-08-07), structured output
  voice.py              ElevenLabs voice provider
  avatar.py             Stub (raises NotImplementedError — do not use)
  heygen.py             HeyGen avatar adapter (upload audio → create → poll → download)
  higgsfield.py         Higgsfield B-roll adapter (submit → poll → wait)
video/
  storyboard.py         B-roll scene extraction
  compositor.py         ffmpeg vertical video render (1080x1920)
  assets.py             File download helper
scripts/                Isolated paid/local experiments (V0.4–V0.11c)
output/                 Generated artifacts (voice.mp3, timelines, mp4s — gitignored)
tests/                  Pure pytest with mocks, no API calls
```

## Pipeline flow

1. `LLMProvider.generate_script()` — Script from user key points
2. `LLMProvider.validate_script()` — fails with `ScriptValidationError` if the script adds unsupported claims; storyboard is never attempted
3. `LLMProvider.generate_storyboard()` — scenes with timing + visuals (max 2 attempts; retry keeps narration word-identical, fixes only timestamps)
4. `validate_storyboard()` — first scene at 0s, sequential IDs, `start < end`, no overlaps, nothing past requested duration
5. Voice — `ElevenLabsVoiceProvider().generate(script.full_script, audio_output_path)`

## Experiments (`scripts/`, summary only)

Isolated, deterministic experiments. No prod-code edits. Paid renders are single-shot with no retry.

| Version | Script(s) | Purpose |
| --- | --- | --- |
| V0.4 | `higgsfield_broll_smoke.py` | Higgsfield B-roll smoke test |
| V0.5 | `avatar_broll_assembly.py` | Local avatar + B-roll assembly |
| V0.5–V0.6 | `heygen_smoke.py`, `heygen_ab.py` | HeyGen smoke + Avatar III-vs-IV A/B |
| V0.7 | `v07_prompt_length_experiment.py`, `v07_assemble.py` | Prompt-length experiment + deterministic final assembly from timeline JSON |
| V0.8 | `v08_framing.py`, `v08b_avatar_crop.py`, `v08d_heygen_cover.py` | Cover framing / crop experiments |
| V0.8c/8e | `v08c_avatar_broll_assembly.py`, `v08e_native_avatar_broll_assembly.py` | Avatar + B-roll assemblies on frozen V0.7 timeline |
| V0.9 | `v09_studio_bg.py`, `v09_studio_avatar.py`, `v09_studio_captions.py` | Studio background + studio avatar render + captions + final assembly |
| V0.11 | `v11_heygen_studio_avatar.py` | HeyGen native studio background render (`remove_background`) |
| V0.11b | `v11b_studio_captions.py` | Local-only captions over V0.11 avatar |
| V0.11c | `v11c_studio_broll_captions.py` | Local-only final assembly: studio avatar + B-roll + captions |

## Testing

```bash
uv run pytest
```

- `test_validators.py` — storyboard validation rules
- `test_prompts.py` — prompt sanity checks
- `test_orchestrator.py` — retry / validation gating / lazy voice init
- `test_voice.py` — ElevenLabs request shape + pipeline wiring
- `test_heygen.py` — HeyGen upload / create / poll / error paths

## Gotchas

- `VideoPipeline()` constructs without ElevenLabs keys; `create_assets()` raises `ValueError(ELEVENLABS_*)` when they are missing.
- `providers/avatar.py` is a stub — avatar work uses `providers/heygen.py`.
- HeyGen: the configured public avatar only supports the Avatar III engine.
- `.gitignore` excludes media files (mp4, wav, mp3, png, jpg) and `output/*`, `assets/generated/*`.
