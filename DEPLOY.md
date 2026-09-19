# Production deployment (Phase 2 — Railway or equivalent)

Architecture is unchanged: Streamlit frontend → FastAPI backend (1 uvicorn
worker) → in-process jobs → `OUTPUT_DIR/<job_id>/` on a persistent volume.
No Redis, Celery, Postgres, S3, Kubernetes, or autoscaling in v1.

## Services

**Backend** (`Dockerfile.backend`)

- Start: `uv run uvicorn backend.app:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1`
- Replicas: 1. Workers: 1. (JobStore is single-process in-memory.)
- Volume: mount persistent storage at `/data`.
- Env: `OUTPUT_DIR=/data/output` plus secrets below. `PORT` is platform-injected.

**Frontend** (`Dockerfile.frontend`)

- Start: `uv run streamlit run app.py --server.port ${PORT:-8501} --server.address 0.0.0.0 --server.headless true`
- No volume.
- Env: `BACKEND_URL=https://<backend-public-url>`. `PORT` is platform-injected.

## Secrets (platform secret manager — never `.env`, never baked into images)

Backend: `OPENAI_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`,
`HEYGEN_API_KEY`, `HEYGEN_AVATAR_ID`, `HF_API_KEY_ID`, `HF_API_KEY_SECRET`.

Do not set `HEYGEN_BACKGROUND_IMAGE` (canonical
`assets/backgrounds/studio_background.png` in the image is the source of truth).
Do not set `RUN_REAL_VIDEO_TEST` outside the single controlled E2E run.

## Deploy sequence

1. Deploy backend, volume mounted at `/data`.
2. `GET /healthz` → `{"ok":true}`.
3. `GET /api/preflight` → `ready:true`, zero FAIL
   (`live_provider_contract` WARNING is expected).
   Confirm `output_writable` shows `/data/output`.
4. Deploy frontend with `BACKEND_URL` set.
5. Open Streamlit → "Preflight passed. Ready to generate."
6. Rejection checks (non-paid): empty topic → 422; oversize key_message → 422.
7. Single controlled 30s paid E2E only after 1–6 are green.

## Rollback

Redeploy the previous image tag. Jobs left `processing` by a restart are
marked safely failed on boot (locks cleared, artifacts preserved) and can be
retried from the UI, reusing paid artifacts.
