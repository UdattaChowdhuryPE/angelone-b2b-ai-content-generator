import subprocess


def render_vertical_video(
    avatar_video: str,
    output_path: str,
):

    command = [
        "ffmpeg",
        "-y",
        "-i",
        avatar_video,
        "-vf",
        (
            "scale=1080:1920:"
            "force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
        ),
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "20",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        output_path,
    ]

    subprocess.run(
        command,
        check=True,
    )

    return output_path
