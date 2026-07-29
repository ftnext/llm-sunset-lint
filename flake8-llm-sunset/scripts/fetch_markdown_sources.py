#!/usr/bin/env python3
"""Download the Markdown sources used to maintain model lifecycle data."""

from __future__ import annotations

import os
import tempfile
import urllib.request
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIRECTORY = REPOSITORY_ROOT / "sources" / "google-cloud"
SOURCES = {
    "model-versions.md": (
        "https://docs.cloud.google.com/gemini-enterprise-agent-platform/"
        "models/model-versions.md.txt"
    ),
    "google-models.md": (
        "https://docs.cloud.google.com/gemini-enterprise-agent-platform/"
        "models/google-models.md.txt"
    ),
}


def download(url: str) -> bytes:
    """Return a Markdown document downloaded from *url*."""
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "flake8-llm-sunset-source-updater/1.0"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content = response.read()

    if not content.strip():
        raise ValueError(f"Downloaded an empty document from {url}")
    return content


def replace_file(path: Path, content: bytes) -> None:
    """Atomically replace *path* with *content*."""
    path.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
    )
    try:
        with os.fdopen(file_descriptor, "wb") as temporary_file:
            temporary_file.write(content)
        os.replace(temporary_name, path)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def main() -> None:
    for filename, url in SOURCES.items():
        destination = SOURCE_DIRECTORY / filename
        replace_file(destination, download(url))
        print(f"Updated {destination.relative_to(REPOSITORY_ROOT)}")


if __name__ == "__main__":
    main()
