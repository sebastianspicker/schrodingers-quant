"""Provenance helpers shared by the research scripts: the pinned image
reference and file hashing (not a standalone entry point)."""

import hashlib
import os
from pathlib import Path


def image_ref() -> str:
    """The pinned Freqtrade image ref this research run used.

    Read from the SQ_FREQTRADE_IMAGE environment variable, which compose.yaml
    sets (from its single `x-freqtrade-image` anchor) for every `docker
    compose run --rm research ...` invocation, so the image digest is defined
    in exactly one place. Raises rather than silently recording an empty
    string in a provenance file if the variable is unset or empty.
    """
    value = os.environ.get("SQ_FREQTRADE_IMAGE")
    if not value:
        raise RuntimeError(
            "SQ_FREQTRADE_IMAGE is unset or empty; research provenance must not record an "
            "empty image reference. Run this via `docker compose run --rm research ...` "
            "(compose.yaml's `research` service sets SQ_FREQTRADE_IMAGE from the pinned "
            "x-freqtrade-image anchor)."
        )
    return value


def sha256_file(path: Path) -> str:
    """SHA-256 hex digest of a file's bytes, streamed to bound memory use."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()
