# Schrödingers Quant — repository guidance

## Purpose and current scope

Build a dependable 24/7 cryptocurrency spot bot using Freqtrade, initially on
Kraken with a €10 live execution experiment. The deployment target is Debian 13,
2 shared vCPU, 3 GB RAM, and 30 GB SSD. Keep research and optimization off the VPS.

The scope is the complete unattended trading system. The €10 starting budget
limits the first live execution experiment, not the product's functionality.
Justify replacing or deferring a capability by evidence, dependencies, or
resource cost. Deferred capabilities and their triggers are listed in
`docs/status.md`.

Tracked defaults are dry-run only and start stopped. The tracked strategy is
`H1ChannelBreakout`, GO by a narrow margin (docs/adr/0005): that permits forward
paper trading and the €10 execution pilot, not more capital. Its file is frozen
(SHA-256 in the experiment record); changes need a new hypothesis. Jev is an
optional shadow/filter experiment with no real provider yet. Reliable operation and
positive returns after costs are separate goals. Never promise constant growth.

## Map

Architecture, boundaries and dependency rules: `docs/architecture.md`.

- `compose.yaml`: the only place the Freqtrade image digest is pinned; services
  `freqtrade` (bot), `jev-worker` (profile `jev`), and `tools`/`research`/`test`
  (profile `tools`) for every other in-image command.
- `config/`: Freqtrade layers. `base.json` holds the tracked dry-run defaults,
  `live.json` the live overlay (applied only explicitly), `examples/` the
  templates. Secrets and local overlays go in the ignored `config/local/`.
- `user_data/strategies/`: Freqtrade strategies. They are self-contained and
  never import `sq`. `H1ChannelBreakout.py` is frozen, and `H1JevShadow.py`'s
  source is part of Jev's candidate id.
- `src/sq/`: the project package, run in the pinned image. `config` covers
  layered config and validation, `live` the read-only preflight and
  reconciliation, `jev` the credential-free worker and its file protocol,
  `research` the H1 research pipeline (`make research ARGS=…`, experiment
  definition in `sq/research/h1.py`), metrics and the offline Jev evaluation.
- `research/`: hypotheses, research configs, experiment records and
  research-only strategies (data, no code).
- `ops/`: everything on the Debian host: health ping, backup/restore,
  retention, systemd units, runbook, soak checklist. It uses stdlib Python and
  POSIX sh.
- `pages/`: the static GitHub Pages demo; `pages/build.sh` adds the recorded
  H1 equity curves (`make research ARGS=equity-curves`).
- `tests/`: pytest in the pinned image (`tests/Dockerfile`); mirrors the code.
- `docs/`: `status.md` (state, open work), `architecture.md`, `operations.md`,
  `live-pilot.md`, `jev.md`, `adr/`.
- `user_data/{runtime,data,backtest_results}`: ignored state and generated data.

Put new project Python in `src/sq/` and respect the dependency rules
(`tests/test_architecture.py`). Nothing outside Freqtrade may place or cancel
orders. Do not introduce a second order engine, an agent framework, Redis, or a
database server without a concrete need. Keep dependencies small and runtime
versions pinned. Update documentation with behavior.

## Trading and data contracts

- Keep committed defaults in spot dry-run mode, without exchange credentials.
  Repository work does not authorize live orders or deployment to a VPS.
- Never commit secrets, account exports, databases, or generated market data.
  Never print secrets while inspecting configuration or logs.
- Before a live pilot, validate pair availability, amount/cost minimums, precision,
  fees, exit sizing, and stop-order support against the actual exchange account.
- Account for partial fills, fees, dust, stale data, and order reconciliation.
  After an ambiguous submission, check exchange state before retrying an order.
- Use UTC timestamps and closed candles. Historical features must use only data
  available at the simulated decision time, including publication delays.
- Risk limits and protective exits are deterministic and independent of Jev.
  A missing or expired required model assessment blocks new entries, not exits.
- Jev never receives exchange credentials. Record its input timestamp, model and
  prompt versions, output, latency, and the time the result became available.
- Compare the same baseline with and without Jev on chronological held-out and
  forward data. Include fees, spread, slippage, and missed fills; report drawdown
  and exposure as well as returns. Treat historical model knowledge as a possible
  source of leakage. Paper fills are not evidence of real execution quality.
- Do not add leverage, martingale sizing, automatic deposits, self-modifying live
  strategies, or automatic capital increases as routine implementation choices.

## Development and verification

- `uv sync --locked --group dev`: install development tooling (Ruff).
- `make check`: Ruff lint and format check, shellcheck, and Compose configuration
  checks (with both overlay examples). It needs no image.
- `make validate`: tracked base config invariants and strategy loading in the
  pinned image. `validate-live` / `validate-vps` check the overlays.
- `make test`: pytest inside the pinned image. `make ci` runs all of the above.
- Research: predeclare a hypothesis in `research/hypotheses/` before looking at
  evaluation data; run held-out data once; compare drawdown marked to market.
- `make up`, `make logs`, `make down`: operate the local dry-run bot.
- Before finishing changes, run checks relevant to the changed behavior. Add
  meaningful tests for trading logic, recovery, and integration contracts;
  do not add tests just to mirror placeholder code.
- Running this idle setup does not validate exchange connectivity, order
  execution, recovery, or profitability. State those limits in reports.
- Docker bind mounts on macOS occasionally serve a stale file right after an
  edit; rerun once before debugging a parse error.

## Collaboration

The main agent owns requirements, architecture, and integration. Delegate useful
independent implementation or review to `sol_worker` or `sol_reviewer`; keep small
tasks local. Give concurrent editors disjoint ownership and preserve others' edits.
Use the user's global delegation guidance for child roles and concurrency limits.
Keep durable guidance here and temporary task progress in the conversation.
Record decisions as ADRs in `docs/adr/`. When delivering code, keep
`docs/status.md` aligned with what is actually implemented and verified.

## Working together

The user, Claude Code and Codex share this repository, and this file is the
guide both agents read.

- **Roles.** The user sets scope, approves irreversible or outward-facing
  actions, and commits. The agent the user starts implements; the other
  agent reviews. No agent accepts its own work.
- **One writer at a time.** Only one agent edits this checkout at a time.
  Before editing, read `.agents/handoff.md` if it exists and run
  `git status`; preserve changes you did not make.
- **Handoff.** When you stop with work in progress or ask for review,
  overwrite `.agents/handoff.md` (ignored by git, never committed) with:
  status (`in-progress`, `ready-for-review`, `changes-requested` or
  `accepted`), date, goal, files changed, checks run with exit codes, and
  open questions or risks.
- **Review.** Review the uncommitted diff against the goal in the handoff
  note. Report each finding as `file:line`, defect and evidence, and record
  the verdict in the handoff note. Do not rewrite the change unless asked.
- **Ready for review** means `make ci` exits 0 (Docker required); if Docker
  is unavailable, `make check` must exit 0 and the skipped checks are named. Paste
  failures verbatim and name any check you skipped.
