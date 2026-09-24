"""Schrödingers Quant: the project's in-image Python package.

Package map:
  - `sq.paths`      repository/config path constants, shared by every submodule.
  - `sq.config`      Freqtrade config loading and the tracked-invariant/merged-secrets
                      checks (`python -m sq.config`); used by `make validate*`.
  - `sq.live`        read-only live-readiness tooling: preflight feasibility and
                      DB-vs-exchange reconciliation. May import ccxt and freqtrade.
  - `sq.jev`         the Jev shadow/filter experiment: `protocol` (stdlib-only file
                      contract; `H1JevShadow` keeps its own copy and a contract test
                      binds the two), `providers`, `worker` and `evaluate`. Never imports ccxt,
                      freqtrade, `sq.live` or `sq.config`: Jev must stay isolated
                      from order execution and exchange credentials (see AGENTS.md).
  - `sq.research`    research/backtest analysis scripts, run only inside the pinned
                      image on a research machine, never as part of live trading.

Dependency rules (enforced by tests/test_architecture.py):
  - `sq.jev.protocol` is stdlib-only.
  - `sq.jev.{protocol,worker,providers}` never import ccxt, freqtrade, `sq.live`
    or `sq.config`.
  - Only `sq.live` and `sq.research.proxy_check` import ccxt.
  - Strategies never import `sq`; the trading container does not mount `src/`.
"""
