"""Import smoke tests: the pure modules import without Freqtrade or ccxt."""

import importlib

import pytest


@pytest.mark.parametrize(
    "module",
    [
        "sq.config",
        "sq.live.preflight",
        "sq.live.reconcile",
        "sq.research.metrics",
        "sq.research.h1",
        "sq.jev.protocol",
        "health_ping",
        "H1ChannelBreakout",
    ],
)
def test_module_imports_without_the_image(module: str) -> None:
    importlib.import_module(module)
