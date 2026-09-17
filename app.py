import os

import streamlit as st
from dotenv import load_dotenv

from pipeline.models import VideoRequest
from pipeline.orchestrator import VideoPipeline


load_dotenv()


st.set_page_config(
    page_title="AI Financial Video Generator",
    page_icon="🎬",
)


st.title("🎬 AI Financial Video Generator")
st.caption("V0 — turn a financial idea into a polished short-form video.")


st.divider()


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


language = st.selectbox(
    "Language",
    ["English", "Hinglish", "Hindi"],
)


duration = st.selectbox(
    "Duration",
    [30, 45, 60],
    index=2,
)


st.divider()


if st.button(
    "✨ Generate",
    type="primary",
    use_container_width=True,
):

    if not topic.strip():
        st.error("Please enter a topic.")
        st.stop()

    if not key_message.strip():
        st.error("Please enter the key points for the video.")
        st.stop()

    if not os.getenv("OPENAI_API_KEY"):
        st.error("OPENAI_API_KEY is missing from .env")
        st.stop()

    request = VideoRequest(
        topic=topic,
        key_message=key_message,
        language=language,
        duration_seconds=duration,
    )

    pipeline = VideoPipeline()

    try:

        with st.status(
            "Creating your video...",
            expanded=True,
        ) as status:

            st.write("✍️ Writing script...")

            result = pipeline.create_assets(request)

            st.write("✅ Script passed content validation.")

            st.write("🎬 Preparing storyboard...")

            status.update(
                label="M0 generation complete",
                state="complete",
            )

    except ValueError as error:

        st.error("The generated script did not pass validation.")

        st.warning(str(error))

        st.info(
            "Review the key points and make sure they contain "
            "the financial claims you want the video to communicate."
        )

        st.stop()

    st.subheader("Script")

    st.write(result["script"].full_script)


    st.subheader("Storyboard")

    for scene in result["storyboard"].scenes:

        with st.expander(
            f"Scene {scene.scene_id} — "
            f"{scene.start:.1f}s → {scene.end:.1f}s"
        ):

            st.write(scene.narration)

            st.write(
                f"**Key claim:** {scene.key_claim}"
            )

            st.write(
                f"**Visual type:** {scene.visual_type}"
            )

            if scene.visual_prompt:
                st.caption(scene.visual_prompt)

            if scene.on_screen_text:
                st.caption(
                    f"On-screen text: {scene.on_screen_text}"
                )


    if result["broll"]:

        st.subheader("B-roll jobs")

        st.json(result["broll"])