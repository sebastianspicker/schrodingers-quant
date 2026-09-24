"""Repository path constants shared by every `sq` submodule."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TRACKED_BASE_CONFIG = REPO_ROOT / "config" / "base.json"
