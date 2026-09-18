import os
import requests


def download_file(
    url: str,
    output_path: str,
):

    os.makedirs(
        os.path.dirname(output_path) or ".",
        exist_ok=True,
    )

    response = requests.get(
        url,
        stream=True,
        timeout=120,
    )

    response.raise_for_status()

    with open(output_path, "wb") as file:

        for chunk in response.iter_content(
            chunk_size=8192,
        ):
            if chunk:
                file.write(chunk)

    return output_path
