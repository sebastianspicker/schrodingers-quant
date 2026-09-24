# Architecture

Why the system has this shape: [ADRs](adr/README.md) (the layout is
[ADR-0006](adr/0006-one-package-one-runtime-definition.md)). What exists and
what is verified: [status](status.md).

## Principle

Freqtrade is the only order engine. It owns market data, strategy execution,
orders, fills, trade state, restart recovery, protections, the API server and
Telegram control. Project code adds strategies, configuration layers, read-only
checks, research tooling, an optional model worker and host operations around
it. Custom execution infrastructure (an intent journal, a supervisor) is added
only when a test or drill shows a gap in Freqtrade
([ADR-0001](adr/0001-evidence-first-restructure.md)).

## Components and ownership

| Component | Home | Runs | Owns / writes | Talks to |
| --- | --- | --- | --- | --- |
| Trading bot | `compose.yaml` service `freqtrade`, `config/`, `user_data/strategies/` | Freqtrade container, 24/7 | `user_data/runtime/*.sqlite` (trade DB) | Kraken, Telegram, API on `127.0.0.1:8080` |
| Strategies | `user_data/strategies/` | inside the bot and backtests | `H1JevShadow` appends `runtime/jev/candidates.jsonl` | nothing else |
| Project package `sq` | `src/sq/` | one-shot `tools`/`research` containers; `sq.jev.worker` in `jev-worker` | outputs named on the command line | see dependency rules |
| Jev worker | `sq.jev.worker`, service `jev-worker` (profile `jev`) | own container, no credentials | appends `runtime/jev/assessments.jsonl` | a model provider (none real yet) |
| Research | `research/` (records, configs, `run.sh`), `sq.research` (code) | development machine, service `research` | `research/experiments/`, `user_data/{data,backtest_results}` | Binance/Kraken public data |
| Host operations | `ops/` | Debian 13 host, systemd | `user_data/runtime/backups/` | Docker, dead-man's switch, restic |
| Demo site | `pages/`, `pages/build.sh`, `.github/workflows/pages.yml` | GitHub Pages, static | nothing at runtime | reads `research/experiments/H1/equity-curves.json` at build time |

### `sq` package

| Module | Responsibility | Entry point |
| --- | --- | --- |
| `sq.paths` | Repository layout (`REPO_ROOT`, tracked base config) | — |
| `sq.config` | Load layered Freqtrade config; tracked-default invariants; credential checks for deployable layers; strategy load | `python -m sq.config` (`make validate*`) |
| `sq.live.preflight` | Read-only: can a stake enter and still exit at the stop after fees and precision? Exit 0 feasible, 2 infeasible, 1 error | `make preflight` |
| `sq.live.reconcile` | Read-only: trade DB vs Kraken order history and balance. Exit 0 match, 3 mismatch, 1 error | `make reconcile` |
| `sq.jev.protocol` | The Jev file contract (names, JSONL, UTC timestamps, decisions) | — |
| `sq.jev.providers`, `sq.jev.worker` | Assess recorded candidates under a timeout; single instance; append assessments | `python -m sq.jev.worker` |
| `sq.jev.evaluate` | Offline baseline-vs-filter comparison using only assessments available before entry | `python -m sq.jev.evaluate` |
| `sq.research.*` | Data manifest, proxy check, backtest summary, buy-and-hold benchmark, marked-to-market drawdown, provenance, equity curves for the demo | `research/run.sh` |

### Dependency rules

Enforced by `tests/test_architecture.py`:

- `sq.jev.protocol`, `sq.jev.providers` and `sq.jev.worker` import only the
  standard library and `sq.jev`: no ccxt, Freqtrade, `sq.live` or `sq.config`.
  The worker cannot see exchange code or credentials.
- Only `sq.live` and `sq.research.proxy_check` import ccxt. No project module
  names an order-creating, -cancelling or -editing method (source guards in
  `tests/live/`).
- Strategies import Freqtrade, pandas, the standard library and each other,
  never `sq`. The trading container does not mount `src/`. That keeps the
  strategies loadable by any Freqtrade instance and keeps the class source,
  which is part of Jev's `candidate_id`, free of package changes.
- `ops/*.py` is standard library only (Debian's system Python).

`H1JevShadow` therefore keeps its own copy of the file-name constants and
readers. `tests/jev/test_protocol.py` has a contract test: the strategy's
writer and reader round-trip with the worker, and the constants and decisions
match `sq.jev.protocol`.

## Runtime definition

`compose.yaml` is the single source of the pinned image (a digest-pinned YAML
anchor) and of how every in-image command runs:

| Service | Profile | Mounts (container path) | Used by |
| --- | --- | --- | --- |
| `freqtrade` | default | `config` ro → `/freqtrade/config`, strategies ro → `/freqtrade/strategies`, `user_data/runtime` → `/freqtrade/user_data/runtime` | `make up`, systemd |
| `jev-worker` | `jev` | `src` ro, `user_data/runtime/jev` → `/jev-runtime` | opt-in shadow runs |
| `tools` | `tools` | repo ro → `/workspace`, config, strategies, runtime | `make validate*`, `preflight`, `reconcile` |
| `research` | `tools` | `src` ro, `user_data` → `/freqtrade/user_data`, `research` → `/freqtrade/research`, strategies ro | `research/run.sh` |
| `test` | `tools` | repo ro only; image built from `tests/Dockerfile` | `make test` |

Container paths are a contract: configs name `/freqtrade/strategies` and
`/freqtrade/user_data/runtime/*.sqlite`, and recorded research provenance names
`/freqtrade/strategies/…` and `/freqtrade/research/…`.

## Configuration

Freqtrade layers repeated `--config` files, later files win:
`config/base.json` → `config/local/vps.json` (from
`config/examples/vps-dryrun.example.json`) → `config/live.json` (only for the
live pilot) → `config/local/secrets.json`. Compose overlays restate the
`command` list: `compose.override.example.yaml` (local secrets),
`compose.vps.example.yaml` (VPS). The copies live at the repo root, are
ignored by git, and are selected with `COMPOSE_FILE` in `.env`.

Invariants (tests and `make validate`): base alone is spot, dry-run, stopped,
credential-free, without API/Telegram and without forced entries. A layer that
enables the API or Telegram needs non-placeholder credentials. `live.json`
contains no credentials, and default targets (`validate`, `up`, `test`) never
name it.

## Data flows and state

- **Trading:** Kraken market data → strategy on closed 4h candles →
  protections → orders → trade DB. Restart recovery is Freqtrade's.
  `initial_state: stopped` means every (re)start, including after a restore,
  waits for an explicit `/start`.
- **Jev:** `H1JevShadow.confirm_trade_entry` appends a candidate (id = hash of
  strategy class sources, pair and signal candle) → the worker appends one
  assessment per candidate (input, model/prompt version, decision, requested
  and available times, latency, error) → in filter mode the strategy allows an
  entry only on a fresh `approve`. Anything else, including errors, blocks the
  entry and never affects exits. The mode defaults to `off`.
- **Research:** Binance OHLCV (Kraken proxy, ADR-0002) → Freqtrade
  backtests → `sq.research` summaries with provenance (image, strategy file
  hash, data manifest hash) → `research/experiments/<id>/`.
- **Operations:** the health timer checks `/api/v1/health` freshness and disk
  usage, then pings a dead-man's switch. The backup timer snapshots every
  `runtime/*.sqlite` and the Jev JSONL records with `sqlite3 .backup`,
  integrity-checks them and optionally pushes them with restic. Restore puts
  one named DB back, moving the current file and its WAL aside, and leaves the
  bot stopped.

All timestamps are UTC. Secrets exist only in ignored `config/local/`,
`.env` and `/etc/schrodingers-quant/ops.env`. They are never printed; CLI
errors name only the exception class.

## Contracts for deferred execution work

Freqtrade's own order handling is used until a drill shows a gap
(ADR-0001). If an intent journal, a custom adapter or stricter pause handling
is ever built, it must satisfy these requirements, carried over from the
earlier implementation's design notes:

1. Persist intent before the first possible order send. An ambiguous
   submission is quarantined as unknown and reconciled against the exchange,
   never blindly resubmitted.
2. Protect the actual filled quantity. No strategy or model request may
   weaken protection implicitly.
3. Process liveness is not proof of readiness to take fresh risk.
4. Pause vs. in-flight work: block unsent work at the final fence and
   reconcile work that was already sent.
5. A second worker or executor instance is rejected without mutating live
   state. A crash between a model response and its record recovers durable
   answers idempotently and discloses remote ambiguity.
6. Do not reach into Freqtrade internals. The legacy code failed exactly
   there: generated configs missing schema fields, and an adapter that read
   `Trade.__dict__` and missed inherited `LocalTrade` members.
