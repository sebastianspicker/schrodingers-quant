"""Tests for sq.jev.providers: NullProvider and the JevProvider stub."""

import pytest

from sq.jev.providers import Assessment, JevProvider, NullProvider, build_provider


def test_null_provider_always_abstains():
    provider = NullProvider()
    result = provider.assess({"candidate_id": "x"}, prompt="ignored")

    assert isinstance(result, Assessment)
    assert result.decision == "abstain"
    assert result.confidence is None
    assert result.model == "null"


def test_jev_provider_stub_raises_not_implemented():
    provider = JevProvider()
    with pytest.raises(NotImplementedError):
        provider.assess({"candidate_id": "x"}, prompt="ignored")


def test_jev_provider_never_calls_out_at_construction(monkeypatch):
    monkeypatch.delenv("JEV_API_KEY", raising=False)
    provider = JevProvider()
    assert provider.api_key is None


def test_jev_provider_reads_api_key_from_env_only(monkeypatch):
    monkeypatch.setenv("JEV_API_KEY", "secret-value")
    provider = JevProvider()
    assert provider.api_key == "secret-value"


def test_build_provider_selects_by_name():
    assert isinstance(build_provider("null"), NullProvider)
    assert isinstance(build_provider("jev"), JevProvider)
    with pytest.raises(ValueError):
        build_provider("unknown")
