"""Drift guard for the figures typed into the demo page (pages/index.html).

Every element marked data-check="<run>:<metric>" must show the value of that
metric in research/experiments/H1/equity-curves.json, at the precision shown.
"""

import json
import math
import re
import statistics
from html.parser import HTMLParser

import pytest

K2_FACTOR = 0.6  # H1.md: held-out drawdown <= 0.6 x buy-and-hold's


class CheckedFigures(HTMLParser):
    """Collects (key, text) for every element with a data-check attribute."""

    def __init__(self):
        super().__init__()
        self.open: list[list] = []  # [key, depth, text parts]
        self.depth = 0
        self.found: list[tuple[str, str]] = []

    def handle_starttag(self, tag, attrs):
        self.depth += 1
        key = dict(attrs).get("data-check")
        if key:
            self.open.append([key, self.depth, []])

    def handle_endtag(self, tag):
        if self.open and self.open[-1][1] == self.depth:
            key, _, parts = self.open.pop()
            self.found.append((key, "".join(parts)))
        self.depth -= 1

    def handle_data(self, data):
        for entry in self.open:
            entry[2].append(data)


def metric(run: dict, name: str) -> float:
    returns = [t["return_pct"] for t in run["trades"]]
    top2 = sum(sorted(returns, reverse=True)[:2])
    derived = {
        "trades": len(returns),
        "winners": sum(r > 0 for r in returns),
        "trade_mean": statistics.mean(returns),
        "trade_sd": statistics.stdev(returns),
        "trade_t": statistics.mean(returns) / (statistics.stdev(returns) / math.sqrt(len(returns))),
        "trade_sum": sum(returns),
        "top2": top2,
        "rest": sum(returns) - top2,
        "k2_limit": K2_FACTOR * run["buy_hold_max_drawdown_pct"],
        "k2_margin": K2_FACTOR * run["buy_hold_max_drawdown_pct"] - run["mtm_max_drawdown_pct"],
    }
    return derived[name] if name in derived else run[name]


def shown(text: str) -> tuple[float, int]:
    match = re.search(r"[+−-]?\d+(?:\.(\d+))?", text)
    assert match, f"no number in {text!r}"
    return float(match.group(0).replace("−", "-")), len(match.group(1) or "")


@pytest.fixture
def page_and_data(repo_root):
    parser = CheckedFigures()
    parser.feed((repo_root / "pages" / "index.html").read_text())
    curves = repo_root / "research" / "experiments" / "H1" / "equity-curves.json"
    data = json.loads(curves.read_text())
    return parser.found, data["runs"]


def test_page_has_checked_figures(page_and_data):
    found, _ = page_and_data
    assert len(found) >= 20


def test_checked_figures_match_the_recorded_curves(page_and_data):
    found, runs = page_and_data
    mismatches = []
    for key, text in found:
        run_key, name = key.split(":")
        value, decimals = shown(text)
        expected = metric(runs[run_key], name)
        if abs(value - expected) > 0.5 * 10**-decimals + 1e-9:
            mismatches.append(f"{key}: page shows {text.strip()!r}, data gives {expected:.4f}")
    assert not mismatches, "\n".join(mismatches)
