"""Research/backtest analysis, run inside the pinned Freqtrade image on a
research machine only — never as part of live trading (see AGENTS.md: "Keep
research and optimization off the VPS."). Run as `python -m sq.research
<subcommand>` (`__main__.py`); see that module's docstring for subcommands.

Module map: `h1` is the H1 experiment definition (periods, fees, pair,
strategy locations, output paths) — the single source every other module
reads. `metrics` and `provenance` are pure functions with no I/O. `pipeline`
runs the Freqtrade subprocess steps and writes the recorded summaries.
`data_manifest`, `proxy_check` and `equity_curves` are used by pipeline but
also expose their own functions directly. `jev_evaluation` is the offline
baseline-vs-filter comparison of Jev's recorded assessments. Nothing outside
this package imports it (tests/test_architecture.py).
"""
