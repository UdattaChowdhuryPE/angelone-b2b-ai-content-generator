# AGENTS.md

## Project overview

AI financial video generator (V0). Expert provides financial points → AI produces script + storyboard + voice mp3. Streamlit frontend, OpenAI for LLM, uv for deps.

Avatar / B-roll / final video assembly live only in isolated `scripts/` experiments — not in the production pipeline.

**Core constraint:** The AI must NEVER invent financial claims, facts, stats, recommendations, or market opinions. User-supplied key points are the sole source of truth for all financial content.

## Quick commands

```bash
# Run the app
uv run streamlit run app.py

# Run all tests
uv run pytest

# Run a single test file
uv run pytest tests/test_validators.py

# Install / sync deps
uv sync
```

## Environment

- Python 3.11+ required (enforced by `pyproject.toml`)
- `uv` is the package manager — do not use pip directly
- `.env` file has API keys; never commit or read `.env` in code
- `OPENAI_API_KEY` — required for the app to work
- `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` — required at the voice stage of `create_assets()` ( ElevenLabs `eleven_v3`, `mp3_44100_128` )
- `HEYGEN_API_KEY` / `HEYGEN_AVATAR_ID` — HeyGen avatar experiments in `scripts/` only, not called from pipeline
- `HF_API_KEY_ID` / `HF_API_KEY_SECRET` — Higgsfield B-roll experiments in `scripts/` only, not called from pipeline

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
  voice.py              ElevenLabs voice provider (VoiceProvider base + ElevenLabsVoiceProvider)
  avatar.py             Stub (raises NotImplementedError — do not use)
  heygen.py             HeyGen avatar adapter (upload audio → create → poll → download)
  higgsfield.py         Higgsfield B-roll adapter (submit → poll → wait)
video/
  storyboard.py         B-roll scene extraction
  compositor.py         ffmpeg vertical video render (1080x1920)
  assets.py             File download helper
scripts/                Isolated paid/local experiments (V0.4–V0.11c, never edit prod code)
output/                 Generated artifacts (voice.mp3, timelines, mp4s — gitignored)
tests/
  test_validators.py    Storyboard validation tests (pure, no API calls)
  test_prompts.py       Prompt existence tests
  test_orchestrator.py  Pipeline retry / validation gating / lazy voice init (mocked)
  test_voice.py         ElevenLabs request shape + pipeline voice wiring (mocked)
  test_heygen.py        HeyGen upload / create / poll / error paths (mocked)
```

## Pipeline flow

`VideoPipeline.create_assets()` in `pipeline/orchestrator.py`:

1. `LLMProvider.generate_script()` — creates Script from user key points
2. `LLMProvider.validate_script()` — checks script doesn't add unsupported claims; raises `ScriptValidationError` on failure, storyboard is never attempted
3. `LLMProvider.generate_storyboard()` — creates scenes with timing + visuals (max 2 attempts; 2nd attempt appends a `correction_prompt` that keeps narration word-identical and fixes only timestamps)
4. `validate_storyboard()` — enforces timing rules (no overlaps, sequential IDs, first scene at 0s, within duration)
5. Voice — `self.voice or ElevenLabsVoiceProvider()` then `voice.generate(script.full_script, audio_output_path)` (default `output/voice.mp3`). Returns `{script, storyboard, broll: [], audio_path}`.

HeyGen / Higgsfield / `video/compositor.py` are NOT called from the orchestrator — see `scripts/` experiments.

## Storyboard validation rules (`pipeline/validators.py`)

- Storyboard must have at least one scene
- Each scene must satisfy `start < end`
- First scene must start at 0s
- Scene IDs must be sequential (1, 2, 3...)
- No overlapping scenes (current.end <= next.start)
- No scene can end past requested duration
- Total duration must not exceed requested duration

## Experiments (`scripts/`, summary only)

Isolated, deterministic experiments. No LLM rewrites, no prod-code edits, no `pipeline/` / `providers/` changes unless stated. Paid renders are single-shot with no retry.

| Version | Script(s) | Purpose |
| --- | --- | --- |
| V0.4 | `higgsfield_broll_smoke.py` | Higgsfield B-roll smoke test |
| V0.5 | `avatar_broll_assembly.py` | Local avatar + B-roll assembly (continuous avatar audio, B-roll video-only) |
| V0.5–V0.6 | `heygen_smoke.py`, `heygen_ab.py` | HeyGen smoke + Avatar III-vs-IV A/B |
| V0.7 | `v07_prompt_length_experiment.py`, `v07_assemble.py` | Prompt-length experiment + deterministic final assembly from timeline JSON |
| V0.8 | `v08_framing.py`, `v08b_avatar_crop.py`, `v08d_heygen_cover.py` | Cover framing / crop experiments (paid + local) |
| V0.8c/8e | `v08c_avatar_broll_assembly.py`, `v08e_native_avatar_broll_assembly.py` | Avatar + B-roll assemblies on frozen V0.7 timeline |
| V0.9 | `v09_studio_bg.py`, `v09_studio_avatar.py`, `v09_studio_captions.py` | Studio background gen + studio avatar render + captions + final assembly |
| V0.11 | `v11_heygen_studio_avatar.py` | HeyGen native studio background render (`remove_background`) |
| V0.11b | `v11b_studio_captions.py` | Local-only captions over V0.11 avatar |
| V0.11c | `v11c_studio_broll_captions.py` | Local-only final assembly: studio avatar + B-roll + captions |

## Testing

- Tests are pure pytest with mocks, no fixtures, services, or API calls needed
- `tests/test_validators.py` — comprehensive storyboard validation tests (fast, no API)
- `tests/test_prompts.py` — sanity checks on prompt strings
- `tests/test_orchestrator.py` — storyboard retry success/exhaustion, script-gating, lazy voice init
- `tests/test_voice.py` — ElevenLabs request shape, env auth, file write, pipeline voice wiring
- `tests/test_heygen.py` — HeyGen upload/create/poll/timeout/error paths
- No lint/typecheck tooling configured yet — just pytest

## Conventions

- Pydantic v2 for all data models
- OpenAI structured output via `client.responses.parse()` with `text_format=`
- Model: `gpt-5-mini-2025-08-07` (hardcoded default in `providers/llm.py:19`)
- Vertical video format: 1080×1920 (portrait/Reels-style); HeyGen experiments use 720x1280
- `.gitignore` excludes all media files (mp4, wav, mp3, png, jpg) and `output/*`, `assets/generated/*`
- `scripts/` must stay isolated: local-only unless explicitly a paid smoke/render script

## Gotchas

- `app.py` calls `load_dotenv()` — keys must be in `.env` at project root
- `VideoPipeline()` constructs without ElevenLabs keys (lazy init); `create_assets()` raises `ValueError(ELEVENLABS_*)` when voice creds are missing
- `providers/avatar.py` still raises `NotImplementedError` — avatar work uses `providers/heygen.py` instead
- `providers/higgsfield.py` will raise `ValueError` if HF env vars are empty — only use when keys are set
- HeyGen: the configured public avatar only supports the Avatar III engine — Avatar IV/V return 400 for it
- `ffmpeg` must be installed on the system for `video/compositor.py` and `scripts/` assemblies to work
- The production pipeline returns `result["broll"] = []` and `result["audio_path"]` — B-roll / avatar / final mp4 only exist as `scripts/` + `output/` experiments in V0
