"""Streamlit frontend for the real end-to-end video pipeline.

Test the REAL generation path without touching the API manually:
submit -> poll staged progress -> script/storyboard -> captioned
1080x1920 MP4 preview -> local download, plus script/storyboard
regeneration and retry of failed stages.
"""

import os
import uuid

import requests
import streamlit as st
from dotenv import load_dotenv


load_dotenv()

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")
POLL_INTERVAL_S = 2
LONG_TIMEOUT_S = 1200
# Short timeout for mutations: regen/retry now enqueue background work and
# return immediately; the job monitor polls for completion.
MUTATION_TIMEOUT_S = 30

STAGES = [
    ("queued", "Queued"),
    ("script", "Writing script"),
    ("script_validation", "Validating script"),
    ("storyboard", "Creating storyboard"),
    ("voice", "Generating voice"),
    ("avatar", "Generating avatar"),
    ("broll", "Generating B-roll"),
    ("composition", "Rendering final video"),
    ("completed", "Completed"),
]

TERMINAL = {"completed", "failed"}


def _init_state() -> None:
    st.session_state.setdefault("job_id", None)
    st.session_state.setdefault("last_job", None)
    st.session_state.setdefault("busy", False)
    st.session_state.setdefault("submit_error", None)
    st.session_state.setdefault("video_bytes", None)
    st.session_state.setdefault("last_request", None)
    st.session_state.setdefault("preflight", None)
    st.session_state.setdefault("preflight_error", None)
    # One idempotency key per Generate intent: reused across reruns/polls
    # so a duplicate submit can never mint a second paid job. Rotated
    # only after a successful new submission (see submit handler).
    st.session_state.setdefault("idem_key", uuid.uuid4().hex)


def _api_get_preflight() -> dict:
    resp = requests.get(f"{BACKEND_URL}/api/preflight", timeout=30)
    resp.raise_for_status()
    return resp.json()


def _check_preflight(force: bool = False) -> dict | None:
    if st.session_state.get("preflight") is not None and not force:
        return st.session_state["preflight"]
    try:
        data = _api_get_preflight()
    except requests.RequestException as error:
        st.session_state["preflight"] = None
        st.session_state["preflight_error"] = str(error)
        return None
    st.session_state["preflight"] = data
    st.session_state["preflight_error"] = None
    return data


def _render_preflight_gate() -> bool:
    """Render the non-paid preflight gate. Returns True if generation allowed."""
    col1, col2 = st.columns([3, 1])
    with col1:
        st.markdown("**Readiness** (non-paid preflight — no credits consumed)")
    with col2:
        if st.button("Re-check", key="preflight_recheck_btn"):
            _check_preflight(force=True)
    data = _check_preflight()
    if data is None:
        err = st.session_state.get("preflight_error") or "unknown error"
        st.warning(f"Could not reach preflight at {BACKEND_URL}. {err}")
        return False
    checks = data.get("checks", [])
    failed = [c for c in checks if c.get("status") == "FAIL"]
    if data.get("ready") and not failed:
        st.success("Preflight passed. Ready to generate.")
        with st.expander("Preflight checks"):
            for item in checks:
                st.write(f"{item.get('status')}: {item.get('name')}")
                if item.get("detail"):
                    st.caption(str(item["detail"])[:300])
        return True
    st.error("Preflight failed. Fix the issue before generating a video.")
    for item in failed:
        detail = item.get("detail", "")
        st.write(f"❌ {item.get('name')}: {detail}"[:300])
    with st.expander("All preflight checks"):
        for item in checks:
            st.write(f"{item.get('status')}: {item.get('name')}")
    return False


def _api_post(path: str, timeout: int = 30) -> dict:
    resp = requests.post(f"{BACKEND_URL}{path}", timeout=timeout)
    if resp.status_code == 404:
        raise LookupError("Job not found (404).")
    if resp.status_code == 409:
        raise RuntimeError(_detail(resp, "Job is not ready for that action (409)."))
    if resp.status_code == 422:
        raise ValueError(_detail(resp, "Request rejected (422)."))
    resp.raise_for_status()
    return resp.json()


def _api_get_job(job_id: str) -> dict:
    resp = requests.get(f"{BACKEND_URL}/api/videos/{job_id}", timeout=30)
    if resp.status_code == 404:
        raise LookupError("Job not found (404).")
    resp.raise_for_status()
    return resp.json()


def _detail(resp: requests.Response, fallback: str) -> str:
    try:
        data = resp.json()
        detail = data.get("detail", data)
        return str(detail)
    except Exception:
        return f"{fallback} {resp.text[:300]}"


def _submit_video(payload: dict) -> dict:
    resp = requests.post(f"{BACKEND_URL}/api/videos", json=payload, timeout=30)
    if resp.status_code == 422:
        raise ValueError(_detail(resp, "Backend rejected the request (422)."))
    if resp.status_code == 409:
        raise ValueError(_detail(resp, "Backend is busy (409)."))
    resp.raise_for_status()
    return resp.json()


def _stage_index(stage: str | None) -> int:
    order = [key for key, _ in STAGES]
    if stage in TERMINAL:
        return len(order)
    try:
        return order.index(stage or "queued")
    except ValueError:
        return 0


def _render_progress(job: dict) -> None:
    stage = job.get("current_stage") or job.get("status")
    detail = job.get("status_detail") or ""
    status = job.get("status")
    if status == "failed":
        st.error(f"Generation failed. {detail}")
        return
    idx = _stage_index(stage if status != "completed" else "completed")
    for i, (key, label) in enumerate(STAGES):
        if key == "completed":
            continue
        if i < idx:
            st.write(f"✅ {label}")
        elif i == idx and status != "completed":
            st.write(f"⏳ {label} — {detail}" if detail else f"⏳ {label}")
        else:
            st.write(f"⬜ {label}")
    if status == "completed":
        st.write("✅ Completed")


def _render_script(job: dict) -> None:
    script = job.get("script") or {}
    result = job.get("result") or {}
    if not script and not result.get("script_preview"):
        return
    st.subheader("Script")
    if script:
        if script.get("title"):
            st.markdown(f"**{script['title']}**")
        if script.get("hook"):
            st.markdown(f"*Hook:* {script['hook']}")
        for para in script.get("body") or []:
            st.write(para)
        if script.get("takeaway"):
            st.markdown(f"*Takeaway:* {script['takeaway']}")
        with st.expander("Full script"):
            st.write(script.get("full_script", ""))
    else:
        st.write(result.get("script_preview", ""))


def _render_storyboard(job: dict) -> None:
    storyboard = job.get("storyboard") or {}
    scenes = storyboard.get("scenes") or []
    if not scenes:
        return
    st.subheader(f"Storyboard ({len(scenes)} scenes)")
    for scene in scenes:
        title = (
            f"Scene {scene.get('scene_id')}: "
            f"{scene.get('start', '?')}s → {scene.get('end', '?')}s"
        )
        with st.expander(title):
            st.write(f"**Narration:** {scene.get('narration', '')}")
            st.write(f"**Key claim:** {scene.get('key_claim', '')}")
            st.write(f"**Visual:** {scene.get('visual_type', '')}")
            if scene.get("visual_prompt"):
                st.write(f"**Visual prompt:** {scene['visual_prompt']}")
            if scene.get("on_screen_text"):
                st.write(f"**On-screen text:** {scene['on_screen_text']}")
            flags = []
            if scene.get("avatar_required"):
                flags.append("avatar")
            if scene.get("broll_required"):
                flags.append("B-roll")
            st.caption(f"Requires: {', '.join(flags) or '—'}")


def _render_error(job: dict) -> None:
    error = job.get("error")
    failed_stage = job.get("failed_stage")
    if failed_stage:
        stage_labels = {
            "script": "Script generation failed.",
            "script_validation": "Script validation failed.",
            "storyboard": "Storyboard generation failed.",
            "voice": "Voice generation failed.",
            "avatar": "Avatar generation failed.",
            "broll": "B-roll generation failed.",
            "composition": "Final video rendering failed.",
        }
        st.error(stage_labels.get(failed_stage, "Video generation failed."))
    elif not error:
        st.error("Video generation failed with no further detail.")
        return
    else:
        st.error(_friendly_error(error))
    with st.expander("Technical details"):
        details = {
            "stage": failed_stage or job.get("current_stage"),
            "status": job.get("status"),
            "operation": job.get("operation"),
            "error_type": job.get("error_type"),
            "error_message": error,
            "completed_artifacts": job.get("completed_artifacts"),
            "skipped_stages": job.get("skipped_stages"),
        }
        st.code(str(details))


def _friendly_error(error: str) -> str:
    lowered = error.lower()
    if "elevenlabs" in lowered:
        return "Voice generation failed. Check ElevenLabs credentials and try Retry."
    if "heygen" in lowered:
        return "Presenter-video generation failed (HeyGen). No video was charged twice — use Retry to resume without regenerating earlier stages."
    if "higgsfield" in lowered:
        return "B-roll generation failed (Higgsfield). Use Retry to resume."
    if "ffmpeg" in lowered or "ffprobe" in lowered or "1080x1920" in lowered:
        return "Final video rendering/validation failed. Use Retry."
    if "unsupported claims" in lowered:
        return (
            "The generated script added financial claims beyond your key "
            "points, so it was rejected. Edit your key points to include "
            "everything the video should say, then generate again."
        )
    if "storyboard validation" in lowered:
        return "Storyboard timing was invalid after retry. Try generating again."
    return "Video generation failed. See technical details, then Retry if safe."


@st.fragment(run_every=POLL_INTERVAL_S)
def _job_monitor() -> None:
    job_id = st.session_state.get("job_id")
    if not job_id:
        return
    try:
        job = _api_get_job(job_id)
    except LookupError:
        st.error("Job not found on the backend. It may have been restarted.")
        st.session_state["job_id"] = None
        return
    except requests.RequestException as error:
        st.warning(f"Could not reach backend ({error}). Retrying…")
        return
    st.session_state["last_job"] = job
    status = job.get("status")

    st.divider()
    st.markdown(f"**Job `{job_id}`** — status: `{status}`")
    _render_progress(job)

    if status == "failed":
        _render_error(job)
        col1, col2 = st.columns(2)
        with col1:
            if st.button("🔁 Retry", key="retry_btn"):
                _do_retry(job_id)
        with col2:
            if st.button("✍️ Regenerate Script", key="regen_script_failed_btn"):
                _do_regen("script", job_id)
        return

    _render_script(job)
    _render_storyboard(job)

    if status == "completed":
        video_url = job.get("video_url")
        if video_url:
            st.subheader("Final video")
            st.video(f"{BACKEND_URL}{video_url}")
            if st.button("⬇️ Fetch MP4 for download", key="fetch_mp4_btn"):
                _fetch_video_bytes(job_id)
            if st.session_state.get("video_bytes"):
                st.download_button(
                    "💾 Download MP4",
                    data=st.session_state["video_bytes"],
                    file_name=f"{job_id}_final.mp4",
                    mime="video/mp4",
                )
        else:
            st.caption("No video file for this job (audio/script artifacts only).")

        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("✍️ Regenerate Script", key="regen_script_btn"):
                _do_regen("script", job_id)
        with col2:
            if st.button("🎬 Regenerate Storyboard", key="regen_board_btn"):
                _do_regen("storyboard", job_id)
        with col3:
            if st.button("🔁 Retry", key="retry_done_btn"):
                _do_retry(job_id)


def _do_regen(kind: str, job_id: str) -> None:
    st.session_state["video_bytes"] = None
    with st.spinner("Starting regeneration…"):
        try:
            job = _api_post(
                f"/api/videos/{job_id}/{kind}/regenerate",
                timeout=MUTATION_TIMEOUT_S,
            )
        except ValueError as error:
            st.error(str(error))
            st.info(
                "The previous valid video is preserved. Adjust your key "
                "points if the script added unsupported claims."
            )
            return
        except (RuntimeError, LookupError) as error:
            st.error(str(error))
            return
        except requests.RequestException as error:
            st.error(f"Regeneration request failed: {error}")
            return
    st.session_state["last_job"] = job
    if job.get("status") == "failed":
        st.error("Regeneration failed.")
        _render_error(job)
    else:
        st.success("Regeneration started — polling…")


def _do_retry(job_id: str) -> None:
    with st.spinner("Starting retry…"):
        try:
            job = _api_post(
                f"/api/videos/{job_id}/retry", timeout=MUTATION_TIMEOUT_S
            )
        except (RuntimeError, LookupError, ValueError) as error:
            st.error(str(error))
            return
        except requests.RequestException as error:
            st.error(f"Retry request failed: {error}")
            return
    st.session_state["last_job"] = job
    if job.get("status") == "failed":
        st.error("Retry failed again.")
        _render_error(job)
    else:
        st.success("Retry started — polling…")


def _fetch_video_bytes(job_id: str) -> None:
    with st.spinner("Downloading final MP4…"):
        try:
            resp = requests.get(
                f"{BACKEND_URL}/api/videos/{job_id}/file",
                timeout=LONG_TIMEOUT_S,
            )
        except requests.RequestException as error:
            st.error(f"Download failed: {error}")
            return
    if resp.status_code == 409:
        st.error("Video file is not ready yet.")
        return
    if resp.status_code == 404:
        st.error("Job not found.")
        return
    try:
        resp.raise_for_status()
    except requests.RequestException as error:
        st.error(f"Download failed: {error}")
        return
    content_type = resp.headers.get("content-type", "")
    if "video/mp4" not in content_type:
        st.error(f"Unexpected content type: {content_type}")
        return
    st.session_state["video_bytes"] = resp.content
    st.success(f"Ready: {len(resp.content) / 1e6:.1f} MB.")


st.set_page_config(
    page_title="AI Financial Video Generator",
    page_icon="🎬",
)

_init_state()

st.title("🎬 AI Financial Video Generator")
st.caption("V0 — turn a financial idea into a polished short-form video.")

st.divider()

preflight_ok = _render_preflight_gate()

st.divider()

with st.form("generate_form"):
    topic = st.text_input(
        "What should the video be about?",
        placeholder="Why are Indian markets volatile right now?",
    )
    key_message = st.text_area(
        "Key points for the video",
        placeholder=(
            "Add the financial points, facts, opinions or "
            "explanations you want the video to communicate."
        ),
    )
    st.caption(
        "The AI will use only these points to create the script. "
        "It will not introduce new financial claims, facts or recommendations."
    )
    language = st.selectbox("Language", ["English", "Hinglish", "Hindi"])
    duration = st.selectbox("Duration", [30, 45, 60], index=2)
    submitted = st.form_submit_button(
        "✨ Generate",
        type="primary",
        use_container_width=True,
        disabled=st.session_state["busy"] or not preflight_ok,
    )

if submitted and not st.session_state["busy"]:
    if not preflight_ok:
        st.error("Preflight failed. Fix the issue before generating a video.")
        st.stop()
    if not (topic or "").strip():
        st.error("Please enter a topic.")
        st.stop()
    if not (key_message or "").strip():
        st.error("Please enter the key points for the video.")
        st.stop()
    payload = {
        "topic": topic.strip(),
        "key_message": key_message.strip(),
        "language": language,
        "duration_seconds": duration,
        "idempotency_key": st.session_state["idem_key"],
    }
    st.session_state["busy"] = True
    st.session_state["video_bytes"] = None
    try:
        created = _submit_video(payload)
    except ValueError as error:
        st.error("Backend rejected the request.")
        st.warning(str(error))
        st.session_state["busy"] = False
        st.stop()
    except requests.RequestException as error:
        st.error(f"Could not reach backend at {BACKEND_URL}. Is it running?")
        with st.expander("Technical details"):
            st.code(str(error))
        st.session_state["busy"] = False
        st.stop()
    st.session_state["job_id"] = created["job_id"]
    st.session_state["last_request"] = payload
    st.session_state["busy"] = False
    # Fresh key for the next Generate intent; the used key stays mapped
    # server-side for 24h so a repeated submit returns the same job.
    st.session_state["idem_key"] = uuid.uuid4().hex
    st.caption(f"Job `{created['job_id']}` submitted.")

_job_monitor()
