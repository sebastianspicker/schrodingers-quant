# ADR-0007 — One research pipeline in Python; `sq.jev` is only the isolated worker

**Date:** 2026-09-25. **Status:** accepted (user-authorized repository reconstruction).

## Context

ADR-0006 settled the package, runtime and host layout a day earlier, and a
fresh review confirmed most of it: Freqtrade as the only order engine,
self-contained strategies, `compose.yaml` as the single runtime definition,
config layers, `ops/`, `sq.live` and `sq.config` all fit their jobs and stay.
A second restructure this soon needed concrete defects. The review found these:

- **H1's decision metric was not reproducible from the research entry point.**
  The `mtm-*.json` records that carry the marked-to-market drawdown behind the
  GO decision (ADR-0005, criterion K2) came from hand-run `mtm_drawdown.py`
  calls. `research/run.sh` had no step for them, and unlike every other record
  they carried no provenance.
- **H1's experiment definition had no single home.** Periods, fees and run
  names lived in a 244-line shell script, and the fees were duplicated in
  `equity_curves.py`. `run.sh` started a separate container for every
  Freqtrade call and every summary step, and pre-created result directories
  from the host.
- **Two implementations of the drawdown formula** (`benchmark.py`,
  `mtm_drawdown.py`).
- **`sq.jev` mixed the credential-free worker with offline analysis.**
  `sq.jev.evaluate` imported pandas, Freqtrade and `sq.research`, so the
  worker's isolation could only be enforced file by file.
- **The order-safety guard was narrower than documented.** "No project module
  names an order-mutating method" was checked only for the two `sq.live`
  modules.

## Decision

- `sq.research` is one CLI, `python -m sq.research <subcommand>`, run as
  `make research ARGS=…` in the `research` service, with the same subcommand
  names `run.sh` had. `sq.research.h1` holds H1's pair, periods, fees,
  notional, strategies and run names. `sq.research.pipeline` runs Freqtrade
  in the research container with the same arguments as before. Every
  backtest step now writes both `<run>.json` and `mtm-<run>.json`, and both
  carry provenance. `sq.research.metrics` holds the one implementation of each
  metric. `research/run.sh` is removed, and `research/` holds data only.
- `sq.jev.evaluate` becomes `sq.research.jev_evaluation`
  (`make research ARGS="jev-evaluate …"`, same flags). Every module in `sq.jev`
  imports only the standard library and `sq.jev`. `sq.research` is a leaf,
  and no other `sq` package imports it.
- `sq.paths` is folded into `sq.config`, which owns the tracked base config
  path.
- `tests/test_architecture.py` enforces the package-wide Jev rule, the
  research leaf rule, and the order-mutation guard across `src/`, `ops/` and
  both strategy directories. The per-module guards in `tests/live/` stay.

## Consequences

- Rerunning a backtest step reproduces the decision metrics with provenance.
  The migration was checked against the recorded H1 results (see
  [status](../status.md)).
- Recorded experiment files keep their names and keys. New `mtm-*.json` files
  add a `provenance` block. Records written before this ADR name
  `research/run.sh`, and `record.md` maps those names to the new command.
- `python -m sq.jev.evaluate` no longer exists. Use
  `make research ARGS="jev-evaluate …"`.
- A future hypothesis gets its own definition module next to `h1.py`. Nothing
  is generalized for it in advance.
