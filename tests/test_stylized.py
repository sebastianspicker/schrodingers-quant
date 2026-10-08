"""Stylized-fact diagnostics recover known answers on synthetic series."""

import math

import numpy as np
import pytest

from sq.research import h1
from sq.research.proxy_data import load_candles
from sq.research.stylized import (
    acf,
    dfa,
    hill_tail_index,
    hurst_report,
    log_returns,
    moments,
    mrw_intermittency,
    permutation_entropy,
    permutation_entropy_report,
    stylized_facts,
)

N = 5000


def gaussian(seed: int = 1, n: int = N) -> np.ndarray:
    return np.random.RandomState(seed).standard_normal(n)


def persistent(seed: int = 2, n: int = N, beta: float = 0.6) -> np.ndarray:
    """Fractional Gaussian noise by spectral synthesis: power ~ f^-beta, H = (beta + 1) / 2."""
    rng = np.random.RandomState(seed)
    freqs = np.fft.rfftfreq(n)[1:]
    spectrum = np.zeros(n // 2 + 1, dtype=complex)
    amp = freqs ** (-beta / 2)
    spectrum[1:] = amp * (rng.standard_normal(len(freqs)) + 1j * rng.standard_normal(len(freqs)))
    return np.fft.irfft(spectrum, n)


def test_log_returns_length() -> None:
    close = np.array([100.0, 101.0, 99.0, 102.0])
    r = log_returns(close)
    assert len(r) == len(close) - 1
    assert r[0] == pytest.approx(math.log(1.01))


def test_gaussian_moments_tail_and_hurst() -> None:
    r = gaussian()
    m = moments(r)
    assert abs(m["skewness"]) < 0.3
    assert abs(m["excess_kurtosis"]) < 0.3
    assert m["annualised_volatility_pct"] == pytest.approx(
        m["sd"] * math.sqrt(6 * 365) * 100, rel=1e-12
    )
    tail = hill_tail_index(r)
    assert tail["k"] == 250
    assert tail["left"] > 4 and tail["right"] > 4
    h = hurst_report(r, resamples=50)
    low, high = h["shuffle_range95"]
    assert 0.4 < h["returns"] < 0.6
    assert low <= h["returns"] <= high
    assert permutation_entropy_report(r, resamples=20)["value"] > 0.97


def test_student_t3_tail_index() -> None:
    r = np.random.RandomState(3).standard_t(3, N)
    tail = hill_tail_index(r)
    assert 2 < tail["left"] < 4.5
    assert 2 < tail["right"] < 4.5


def test_persistent_series_has_high_hurst() -> None:
    x = persistent()
    h = hurst_report(x, resamples=50)
    assert h["returns"] > 0.6
    assert h["returns"] > h["shuffle_range95"][1]
    value, detail = dfa(x)
    assert value == pytest.approx(h["returns"])
    assert len(detail["scales"]) == len(detail["fluctuations"])


def test_sine_has_low_permutation_entropy() -> None:
    assert permutation_entropy(np.sin(np.linspace(0, 40 * np.pi, N))) < 0.6


def test_acf() -> None:
    pattern = np.tile([1.0, -1.0, 0.0], 100)
    assert acf(pattern, [3])["3"] == pytest.approx(1.0, abs=0.02)
    noise = acf(gaussian(4), [1, 2, 6, 30])
    assert all(abs(v) < 0.1 for v in noise.values())


def test_mrw_intermittency() -> None:
    assert abs(mrw_intermittency(gaussian())["lambda2"]) < 0.02
    rng = np.random.RandomState(5)
    # omega: slowly decaying correlation, built as a moving average of white noise
    window = 200
    noise = rng.standard_normal(N + window - 1)
    omega = np.convolve(noise, np.ones(window) / math.sqrt(window), "valid")
    r = rng.standard_normal(N) * np.exp(omega)
    out = mrw_intermittency(r)
    assert out["lambda2"] > 0
    assert out["integral_scale_candles"] is not None


def _leaves(v):
    if isinstance(v, dict):
        for x in v.values():
            yield from _leaves(x)
    elif isinstance(v, list):
        for x in v:
            yield from _leaves(x)
    else:
        yield v


def test_stylized_facts_on_tracked_candles() -> None:
    candles = load_candles()
    periods = {
        "train": (h1.TRAIN_START, "2023-01-01"),
        "validation": ("2023-01-01", "2024-07-01"),
        "heldout": ("2024-07-01", "2026-09-20"),
    }
    first = stylized_facts(candles, periods)
    assert list(first) == ["train", "validation", "heldout", "full"]
    keys = {
        "start",
        "end",
        "candles",
        "annualised_volatility_pct",
        "skewness",
        "excess_kurtosis",
        "tail_index",
        "acf_returns",
        "acf_abs_returns",
        "hurst",
        "permutation_entropy",
        "mrw_lambda2",
        "mrw_integral_scale_candles",
    }
    for name, block in first.items():
        assert set(block) == keys, name
        numbers = {k: x for k, x in block.items() if k not in ("start", "end")}
        for v in _leaves(numbers):
            assert v is not None and math.isfinite(v), (name, v)
    assert first["full"]["candles"] == len(candles)
    assert stylized_facts(candles, periods) == first
