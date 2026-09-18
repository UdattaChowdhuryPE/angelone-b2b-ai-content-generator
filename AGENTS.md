# AGENTS.md

## Project overview

AI financial video generator (V0). Expert provides financial points → AI produces script + storyboard. Streamlit frontend, OpenAI for LLM, uv for deps.

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
- `OPENAI_API_KEY` is required for the app to work
- `HF_API_KEY_ID` / `HF_API_KEY_SECRET` — Higgsfield video gen (stubs only in V0)
- `ELEVENLABS_API_KEY`, `AVATAR_API_KEY` — not wired yet

## Architecture

```
app.py                  Streamlit UI entrypoint
pipeline/
  models.py             Pydantic models (VideoRequest, Script, Storyboard, Scene)
  prompts.py            System prompts for script + storyboard generation
  validators.py         Storyboard validation (timing, coverage, sequencing)
  orchestrator.py       VideoPipeline — chains script → validate → storyboard
providers/
  llm.py                OpenAI client (gpt-4o-mini-2024-07-18), structured output
  voice.py              Stub
  avatar.py             Stub
  higgsfield.py         Higgsfield API adapter (video generation)
video/
  storyboard.py         B-roll scene extraction
  compositor.py         ffmpeg vertical video render (1080x1920)
  assets.py             File download helper
tests/
  test_validators.py    Storyboard validation tests (pure, no API calls)
  test_prompts.py       Prompt existence tests
```

## Pipeline flow

`VideoPipeline.create_assets()` in `pipeline/orchestrator.py`:

1. `LLMProvider.generate_script()` — creates Script from user key points
2. `LLMProvider.validate_script()` — checks script doesn't add unsupported claims
3. `LLMProvider.generate_storyboard()` — creates scenes with timing + visuals
4. `validate_storyboard()` — enforces timing rules (no overlaps, sequential IDs, first scene at 0s, within duration)

## Storyboard validation rules (`pipeline/validators.py`)

- First scene must start at 0s
- Scene IDs must be sequential (1, 2, 3...)
- No overlapping scenes (current.end <= next.start)
- No scene can end past requested duration
- Total duration must not exceed requested duration
- Storyboard must have at least one scene

## Testing

- Tests are pure pytest, no fixtures or services needed
- `tests/test_validators.py` — comprehensive storyboard validation tests (fast, no API)
- `tests/test_prompts.py` — sanity checks on prompt strings
- No lint/typecheck tooling configured yet — just pytest

## Conventions

- Pydantic v2 for all data models
- OpenAI structured output via `client.responses.parse()` with `text_format=`
- Model: `gpt-4o-mini-2024-07-18` (hardcoded in `providers/llm.py:19`)
- Vertical video format: 1080×1920 (portrait/Reels-style)
- `.gitignore` excludes all media files (mp4, wav, mp3, png, jpg) and `output/*`, `assets/generated/*`

## Gotchas

- `app.py` calls `load_dotenv()` — keys must be in `.env` at project root
- `providers/voice.py` and `providers/avatar.py` raise `NotImplementedError` — do not call from pipeline
- `providers/higgsfield.py` will raise `ValueError` if HF env vars are empty — only use when keys are set
- `ffmpeg` must be installed on the system for `video/compositor.py` to work
- The pipeline currently stops at storyboard — `result["broll"]` is always `[]` in V0
