"""Tests for sq.research.provenance: the pinned image reference and file
hashing."""

import hashlib

import pytest

from sq.research.provenance import image_ref, sha256_file


def test_image_ref_raises_when_unset(monkeypatch):
    monkeypatch.delenv("SQ_FREQTRADE_IMAGE", raising=False)
    with pytest.raises(RuntimeError):
        image_ref()


def test_image_ref_raises_when_empty(monkeypatch):
    monkeypatch.setenv("SQ_FREQTRADE_IMAGE", "")
    with pytest.raises(RuntimeError):
        image_ref()


def test_image_ref_returns_the_env_value(monkeypatch):
    monkeypatch.setenv("SQ_FREQTRADE_IMAGE", "freqtradeorg/freqtrade:2026.8@sha256:deadbeef")
    assert image_ref() == "freqtradeorg/freqtrade:2026.8@sha256:deadbeef"


def test_sha256_file_matches_hashlib(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"some research bytes" * 1000)

    assert sha256_file(path) == hashlib.sha256(path.read_bytes()).hexdigest()
