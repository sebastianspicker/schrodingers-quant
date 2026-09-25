"""Shared fixtures for the contract tests."""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def repo_root() -> Path:
    """Root of the checked-out repository, as mounted into the test container."""
    return REPO_ROOT
