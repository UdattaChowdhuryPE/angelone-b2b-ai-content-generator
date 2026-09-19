# AI Financial Video Generator

AI-powered pipeline for creating short-form financial videos from
expert-provided financial inputs.

The core V0 product principle is:

> **The expert provides the financial thinking. AI packages it into video content.**

The AI should not independently invent financial claims, facts,
statistics, recommendations, or market opinions.

---

## Product Goal

Turn expert-provided financial points into a captioned vertical video
through a controlled generation pipeline:

```text
Expert Financial Input
        ↓
      Script
        ↓
 Script Validation (financial-claim gate — never weakened)
        ↓
   Approved Script
        ↓
   Storyboard
        ↓
  Voice (ElevenLabs mp3)
        ↓
  Avatar (HeyGen, Avatar IV, 9:16 720p source, canonical studio background)
        ↓
  B-roll (Higgsfield, per-scene, silent 720p)
        ↓
  Final MP4 (FFmpeg: avatar + B-roll + burned-in captions, 1080x1920,
             H.264 + AAC, faststart, ffprobe-validated)
        ↓
  Streamlit preview + local MP4 download
```

The production backend (`backend/app.py` + `backend/worker.py`) wires the
real providers by default. Script/storyboard regeneration and retry-from-
failed-stage are supported; every regeneration re-runs the claim gate and
rebuilds downstream artifacts (voice → avatar → B-roll → captioned MP4).

---

## Quickstart

```bash
# Install / sync deps (uv only, no pip)
uv sync

# Terminal 1 — backend (real providers)
uv run uvicorn backend.app:app --port 8000

# Terminal 2 — frontend
BACKEND_URL=http://localhost:8000 uv run streamlit run app.py

# Run all tests (no network, no credentials, no FFmpeg required)
uv run pytest
```

Requires Python 3.11+ and `ffmpeg` + `ffprobe` installed on the system
(mandatory for final composition and MP4 validation).
Keys live in `.env` at the project root. Never commit `.env`, never print
secret values.

## Environment (names only)

| Var | Required for |
| --- | --- |
| `OPENAI_API_KEY` | All LLM calls (`gpt-5-mini-2025-08-07`) |
| `ELEVENLABS_API_KEY` / `ELEVENLABS_VOICE_ID` | Voice stage (ElevenLabs `eleven_v3`, `mp3_44100_128`) |
| `HEYGEN_API_KEY` / `HEYGEN_AVATAR_ID` | Avatar stage (`HeyGenAvatarProvider.generate()`, Avatar IV, 9:16, 720p source) |
| `HEYGEN_BACKGROUND_IMAGE` | Optional override; defaults to `assets/backgrounds/studio_background.png` (single source of truth — do not substitute experimental backgrounds) |
| `HF_API_KEY_ID` / `HF_API_KEY_SECRET` | B-roll stage (`HiggsfieldProvider.fetch()`, `alibaba/wan-3.0/text-to-video`, 5s, 9:16, 720p, silent) |

## Architecture

```
app.py                  Streamlit UI (submit, stage progress, script,
                        storyboard, MP4 preview + download, regenerate, retry)
backend/
  app.py                FastAPI: POST /api/videos, GET job, GET /file (video/mp4),
                        POST script/storyboard regenerate, POST retry.
                        Production defaults wire REAL providers (sentinel
                        USE_REAL_PROVIDERS); explicit None skips a stage.
  worker.py             Staged execution with current_stage/status_detail,
                        atomic artifact persistence, regen + resume-from-failure.
  store.py              In-memory JobStore (+ stage fields, mutation locks).
  schemas.py            CreateVideoRequest/Response, JobStatusResponse
                        (current_stage, status_detail, script, storyboard…).
pipeline/
  models.py             Pydantic models (VideoRequest, Script, Storyboard, Scene)
  prompts.py            System prompts for script + storyboard generation
  validators.py         Storyboard validation (timing, coverage, sequencing)
  orchestrator.py       VideoPipeline — staged methods (create_script,
                        validate_script_or_raise, create_storyboard,
                        create_voice) + legacy create_assets() wrapper.
providers/
  llm.py                OpenAI client (gpt-5-mini-2025-08-07), structured output
  voice.py              ElevenLabs voice provider
  avatar.py             Stub (raises NotImplementedError — do not use)
  heygen.py             HeyGen adapter incl. generate() (audio + canonical
                         studio-bg upload → Avatar IV 9:16/720p → poll →
                        download). Source is 720x1280; final is 1080x1920.
  higgsfield.py         Higgsfield adapter incl. fetch() (per-scene 5s silent
                        clips → broll/scene_<id>.mp4 + metadata.json).
video/
  captions.py           V0.9-ported captions: verbatim cards, char-proportional
                        timing, Pillow PNGs (Arial Bold 46, lower-third) at
                        1080x1920, burned in via overlay+enable.
  compositor.py         compose_final() (avatar/B-roll concat + captions,
                        H.264/AAC, +faststart, .partial→rename) + ffprobe
                        validate_mp4() (exactly 1080x1920, or it fails).
  storyboard.py         B-roll scene extraction
  assets.py             File download helper
scripts/                Isolated paid/local experiments (V0.4–V0.11c, reference only)
assets/backgrounds/
  studio_background.png Canonical V0 studio background (uploaded to HeyGen
                        natively with remove_background:true for every render)
output/<job_id>/        Per-job artifacts: script.json, storyboard.json,
                        voice.mp3, avatar.mp4, broll/, captions/,
                        final.mp4, result.json (gitignored)
tests/                  pytest with mocks; real FFmpeg integration marked;
                        paid end-to-end strictly opt-in (RUN_REAL_VIDEO_TEST)
```

### Resolution contract

- Provider sources are **720x1280** (HeyGen `720p`, Higgsfield `720p`).
- The delivered final is **always exactly 1080x1920** (upscaled in FFmpeg).
- `validate_mp4()` fails the job if the final is not 1080x1920 H.264 + AAC.
  A lower-resolution file is never silently returned.

## Pipeline flow

1. `create_script()` — Script from user key points
2. `validate_script_or_raise()` — fails with `ScriptValidationError` if the script adds unsupported claims; storyboard is never attempted
3. `create_storyboard()` — scenes with timing + visuals (max 2 attempts; retry keeps narration word-identical, fixes only timestamps)
4. `validate_storyboard()` — first scene at 0s, sequential IDs, `start < end`, no overlaps, nothing past requested duration
5. Voice — ElevenLabs mp3 → `voice.mp3`
6. Avatar — HeyGen with canonical studio background → `avatar.mp4`
7. B-roll — Higgsfield per B-roll scene → `broll/scene_<id>.mp4` + `metadata.json`
8. Composition — `compose_final()` → `final.mp4` (+ ffprobe gate) → `result.json`

## Regeneration / retry

- `POST /api/videos/{id}/script/regenerate` — new validated script; on
  validation failure returns **422 and preserves the working video**;
  on success replaces the script, clears downstream, and rebuilds
  storyboard → voice → avatar → B-roll → captioned 1080x1920 MP4.
- `POST /api/videos/{id}/storyboard/regenerate` — reuses the stored
  script, replaces the storyboard, rebuilds voice → avatar → B-roll → MP4.
- `POST /api/videos/{id}/retry` — resumes from the first missing/failed
  stage, reusing earlier paid artifacts (e.g. avatar failure reuses
  script + storyboard + voice). No automatic provider retries — each paid
  call is single-shot with explicit timeouts.
- Concurrent mutations are rejected with **409** (single-process lock).

## Testing

```bash
uv run pytest
```

- Unit tests use mocks only: no network, no credentials, no paid calls,
  no FFmpeg requirement (`test_no_external.py` enforces this; production
  code never imports test/smoke fakes).
- `tests/test_compositor_integration.py` (`@pytest.mark.integration`)
  runs a real local FFmpeg assembly when ffmpeg/ffprobe are installed.
- Explicit paid end-to-end (OpenAI + ElevenLabs + HeyGen + Higgsfield +
  FFmpeg) ONLY with:

```bash
RUN_REAL_VIDEO_TEST=true uv run pytest tests/test_real_video.py -s
```

## Known limitations

- Single-process in-memory jobs (lost on backend restart); no auth, no DB.
- HeyGen source is 720p (upscaled to the 1080x1920 final); native HeyGen
  background requires the account's avatar to accept `background` +
  `remove_background` (failures surface honestly, never silently plain).
- Caption timing is character-proportional across the storyboard duration
  (no word-level forced alignment yet).
- Regeneration endpoints run synchronously — allow several minutes.
