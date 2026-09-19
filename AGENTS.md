# AGENTS.md

## Project overview

AI financial video generator (V0). Expert provides financial points → AI produces script + storyboard + voice mp3 + HeyGen avatar (canonical studio background) + Higgsfield B-roll + captioned 1080x1920 final MP4. Streamlit frontend, FastAPI backend, OpenAI for LLM, uv for deps.

**Core constraint:** The AI must NEVER invent financial claims, facts, stats, recommendations, or market opinions. User-supplied key points are the sole source of truth for all financial content. Every script (initial + regenerations) passes the claim-validation gate.

## Quick commands

```bash
# Run the backend (real providers by default)
uv run uvicorn backend.app:app --port 8000

# Run the frontend against the backend
BACKEND_URL=http://localhost:8000 uv run streamlit run app.py

# Run all tests (mocks only — no network/creds/FFmpeg required)
uv run pytest

# Opt-in paid end-to-end test only (spends real money)
RUN_REAL_VIDEO_TEST=true uv run pytest tests/test_real_video.py -s

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
app.py                  Streamlit UI (submit, staged progress, script,
                        storyboard, MP4 preview + download, regenerate, retry)
backend/
  app.py                FastAPI: videos CRUD + /file (video/mp4) + script/
                        storyboard regenerate + retry. Defaults wire REAL
                        providers (USE_REAL_PROVIDERS); None skips a stage.
                        Never import tests/fakes or smoke modules here.
  worker.py             Staged run_job + regenerate_script/storyboard +
                        retry_job; persists script.json/storyboard.json/
                        voice.mp3/avatar.mp4/broll/captions/final.mp4.
  store.py              In-memory JobStore + current_stage/status_detail/locks
pipeline/
  models.py             Pydantic models (VideoRequest, Script, Storyboard, Scene)
  prompts.py            System prompts for script + storyboard generation
  validators.py         Storyboard validation (timing, coverage, sequencing)
  orchestrator.py       VideoPipeline — staged methods + create_assets() wrapper
providers/
  llm.py                OpenAI client (gpt-5-mini-2025-08-07), structured output
  voice.py              ElevenLabs voice provider (VoiceProvider base + ElevenLabsVoiceProvider)
  avatar.py             Stub (raises NotImplementedError — do not use)
  heygen.py             HeyGen adapter incl. generate() (Avatar IV, 9:16, 720p
                        source + canonical studio bg at
                        assets/backgrounds/studio_background.png)
  higgsfield.py         Higgsfield adapter incl. fetch() (5s silent 720p clips)
video/
  captions.py           V0.9-ported captions (verbatim cards, Pillow PNGs,
                        burned in via overlay) at 1080x1920
  compositor.py         compose_final() + ffprobe validate_mp4() (exactly
                        1080x1920 H.264/AAC/faststart or fail)
  storyboard.py         B-roll scene extraction
  assets.py             File download helper
scripts/                Isolated paid/local experiments (V0.4–V0.11c, reference only)
output/<job_id>/        Per-job artifacts (gitignored)
tests/                  pytest with mocks; test_real_video.py is opt-in paid
```

## Pipeline flow

`VideoPipeline` staged methods in `pipeline/orchestrator.py` + `backend/worker.py`:

1. `create_script()` — creates Script from user key points
2. `validate_script_or_raise()` — raises `ScriptValidationError` on unsupported claims; storyboard never attempted
3. `create_storyboard()` — scenes with timing + visuals (max 2 attempts)
4. `validate_storyboard()` — timing rules (no overlaps, sequential IDs, first scene at 0s, within duration)
5. Voice → `voice.mp3` → Avatar (HeyGen + canonical bg) → B-roll (Higgsfield) → `compose_final()` → validated `final.mp4`

Regen (`script/regenerate`, `storyboard/regenerate`) re-runs the claim gate and rebuilds downstream; `retry` resumes from the failed stage reusing paid artifacts. No automatic provider retries.

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
- `tests/test_heygen.py` + `test_heygen_generate.py` — HeyGen upload/create/poll/timeout/error paths + `generate()` incl. studio-bg payload
- `tests/test_higgsfield.py` — B-roll payload shape (no narration), ordering, metadata, failure paths
- `tests/test_captions.py` + `test_compositor.py` — verbatim caption cards/1080x1920 render + FFmpeg cmd shape, failure, `validate_mp4`
- `tests/test_compositor_integration.py` (`integration` mark) — real local FFmpeg assembly when installed
- `tests/test_real_video.py` — opt-in paid end-to-end only (`RUN_REAL_VIDEO_TEST=true`)
- `tests/test_worker_stages.py`, `test_api_endpoints.py` — stage progression, persistence, regen validation-failure preservation, retry resume, file serving, no-fake-imports guard
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
- HeyGen: production renders use the Avatar IV engine (9:16, 720p source, canonical studio bg, fit cover + remove_background)
- `ffmpeg` must be installed on the system for `video/compositor.py` and `scripts/` assemblies to work
- Provider sources are 720x1280 (HeyGen/Higgsfield `720p`); the composed final is always exactly 1080x1920 (`validate_mp4` fails otherwise)
- Canonical studio background lives at `assets/backgrounds/studio_background.png` — never substitute experimental backgrounds
