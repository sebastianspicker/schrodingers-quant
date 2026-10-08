# Research

Document each strategy as a falsifiable hypothesis before tuning it. Record the market and pair, candle interval, decision timing, entry and exit rules, risk limits, costs, and what would disprove the idea. Keep dated code/config versions and the data period for reproducibility. Use UTC and only features available at the decision time.

Split development and held-out periods chronologically. Include fees, spread, slippage, missed fills, exposure, drawdown, and comparisons with buy-and-hold and cash. Report losing periods as well as aggregate returns. After selection, use forward paper data before a live execution pilot; simulated fills do not establish real fill quality.

Store generated market data and backtest output in the ignored `user_data/` paths. Run expensive experiments off the VPS.

## Layout

This directory holds research records and inputs, no code. The code lives in
`src/sq/research/`; H1's periods, fees, pair and run names are defined once in
`src/sq/research/h1.py`.

- `hypotheses/`: predeclared, falsifiable hypotheses (`H1.md`, and the
  successor `H2.md`, predeclared 2026-10-08 and not yet run). Fixed once dated;
  changes after that date need a new hypothesis ID, not an edit.
- `configs/`: standalone Freqtrade config overlays for research backtests
  (e.g. `backtest.json`), not derived from the tracked `config/base.json`.
- `strategies/`: research-only strategy variants (e.g. sensitivity checks)
  that subclass a strategy in `user_data/strategies/`; loaded via
  `--strategy-path` and never used outside train/validation.
- `experiments/<id>/`: tracked experiment records and summarized JSON results
  (`record.md`, `*.json`); raw Freqtrade export data stays in the ignored
  `user_data/backtest_results/`. `experiments/H2/` will hold H2's records, and
  does not exist yet.
- `data-manifest.json`: provenance of the proxy OHLCV data (ADR-0002), including
  the tracked copy below.
- `data/`: `binance-BTC_EUR-4h.csv.gz`, the same Binance 4h history the research
  container downloads, tracked so that `make physics` and its drift test run on
  any machine without Docker, with its own `data/manifest.json` (ADR-0008); the
  pinned `data-manifest.json` above is untouched because the H1 result files
  hash it.
- `ledger.json`: every evaluation of a rule on recorded data, in order, and
  whether it counts as a trial for the deflated Sharpe ratio (ADR-0008).
Every research command is `make research ARGS="<subcommand>"`, which runs
`python -m sq.research <subcommand>` in the `research` Compose service (no
argument lists the subcommands). That service uses the pinned image and mounts
`research/` at `/freqtrade/research`, the container path recorded in results.
Each backtest step writes `<run>.json` (Freqtrade metrics with provenance) and
`mtm-<run>.json` (marked-to-market metrics on the fixed notional, which decide
H1's criteria). Earlier records name `research/run.sh <subcommand>`, which is
the same subcommand today.

Each experiment record also carries a statistical assessment:
`experiments/H1/statistics.json` (bootstrap intervals, block-bootstrap Sharpe,
random-timing and constant-exposure benchmarks, power analysis). `make stats`
rebuilds it deterministically from `equity-curves.json` without Docker, and a
unit test fails if the tracked file drifts from a fresh build
([ADR-0006](../docs/adr/0006-evidence-standard.md)).

`experiments/H1/physics.json` is the physics-informed assessment
([ADR-0008](../docs/adr/0008-physics-informed-nulls.md), [physics](../docs/physics.md)):
stylized facts of the market, H1 replayed on surrogate and simulated markets
without an edge, the forward protocol's false-GO rate under those markets, a
first-passage check of the stop, growth and Kelly figures, and the trials
ledger. `make physics` rebuilds it from `data/` and the recorded curves without
Docker (a few minutes); a unit test rebuilds the cheap sections and a fixed
prefix of every Monte Carlo section and fails if the tracked file drifts.
