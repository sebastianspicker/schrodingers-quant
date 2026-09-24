"""Shared fixtures and helpers for the scaffold's contract tests."""

from pathlib import Path

import pytest

from sq.config import load_config as build_config

REPO_ROOT = Path(__file__).resolve().parent.parent

__all__ = ["REPO_ROOT", "repo_root", "build_config"]


@pytest.fixture
def repo_root() -> Path:
    """Root of the checked-out repository, as mounted into the test container."""
    return REPO_ROOT
