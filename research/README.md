# Research

Document each strategy as a falsifiable hypothesis before tuning it. Record the market and pair, candle interval, decision timing, entry and exit rules, risk limits, costs, and what would disprove the idea. Keep dated code/config versions and the data period for reproducibility. Use UTC and only features available at the decision time.

Split development and held-out periods chronologically. Include fees, spread, slippage, missed fills, exposure, drawdown, and comparisons with buy-and-hold and cash. Report losing periods as well as aggregate returns. After selection, use forward paper data before a live execution pilot; simulated fills do not establish real fill quality.

Store generated market data and backtest output in the ignored `user_data/` paths. Run expensive experiments off the VPS.

## Layout

This directory holds research records and inputs, no code. The code lives in
`src/sq/research/` (tested in `tests/research/`); H1's periods, fees, pair and
run names are defined once in `src/sq/research/h1.py`.

- `hypotheses/`: predeclared, falsifiable hypotheses (e.g. `H1.md`). Fixed once
  dated; changes after that date need a new hypothesis ID, not an edit.
- `configs/`: standalone Freqtrade config overlays for research backtests
  (e.g. `backtest.json`), not derived from the tracked `config/base.json`.
- `strategies/`: research-only strategy variants (e.g. sensitivity checks)
  that subclass a strategy in `user_data/strategies/`; loaded via
  `--strategy-path` and never used outside train/validation.
- `experiments/<id>/`: tracked experiment records and summarized JSON results
  (`record.md`, `*.json`); raw Freqtrade export data stays in the ignored
  `user_data/backtest_results/`.
- `data-manifest.json`: provenance of the proxy OHLCV data (ADR-0002).
Every research command is `make research ARGS="<subcommand>"`, which runs
`python -m sq.research <subcommand>` in the `research` Compose service (no
argument lists the subcommands). That service uses the pinned image and mounts
`research/` at `/freqtrade/research`, the container path recorded in results.
Each backtest step writes `<run>.json` (Freqtrade metrics with provenance) and
`mtm-<run>.json` (marked-to-market metrics on the fixed notional, which decide
H1's criteria). Records made before ADR-0007 name `research/run.sh
<subcommand>`, which is the same subcommand today.
