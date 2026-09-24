# ADR-0006 — One project package, one runtime definition, one host directory

**Date:** 2026-09-24. **Status:** accepted (user-authorized repository reconstruction).

## Context

After slices A–D the repository worked (`make ci` green, 125 tests), but its
shape had grown from the build order rather than from its responsibilities:

- Project Python lived in four places with four import mechanisms:
  `scripts/` (implicit namespace package), `ops/` (a package), `experiments/jev/`
  (a try/except dual import) and `research/scripts/` (cwd-relative
  `from _common import`). Tests inserted `sys.path` entries.
- Duplicated sources of truth: the image digest in five files, `load_config`
  in four, the Jev file contract in three readers that behaved differently,
  and the trade-DB file name both in `db_url` and hardcoded in `ops/backup.sh`
  and `ops/restore.sh`.
- The last duplication was a defect: under `config/live.json` (`live.sqlite`)
  the backup snapshotted `dry-run.sqlite` or, if it was absent, created an
  empty one and reported integrity "ok". Restore also left a stale `-wal` file
  beside the restored DB.
- The pinned image ran in three different ways: the bot service with its
  entrypoint overridden and `scripts/` mounted into it, `docker build/run`, and
  `docker run` with ad-hoc mounts.
- About 1,300 lines of plan, ledger and legacy audit sat beside the
  architecture and status docs. `AGENTS.md` named the plan and the ledger as
  canonical, although their phases were done or waiting on the maintainer.

## Decision

- `src/sq/` is the only importable project package (config, live, jev,
  research). Its dependency rules are listed in
  [architecture](../architecture.md) and enforced by `tests/test_architecture.py`.
- Strategies stay self-contained Freqtrade files and do not import `sq`.
  `H1ChannelBreakout.py` and `H1JevShadow.py` are byte-identical to the
  evaluated versions, so `candidate_id` is unchanged. A contract test binds
  `H1JevShadow` to `sq.jev.protocol`.
- `compose.yaml` holds the digest once, as a YAML anchor, and defines every
  in-image command: `freqtrade`, `jev-worker` (formerly
  `compose.jev.example.yaml`), `tools`, `research` and `test` (its
  Dockerfile moved to `tests/`). The container paths used by configs and
  recorded provenance are unchanged. The bot container no longer mounts
  `scripts/`, OHLCV data, backtest or hyperopt results.
- `ops/` holds everything that runs on the host: scripts, systemd units,
  runbook, soak checklist and rehearsal record (formerly `deploy/debian13/`).
  Backups cover every `runtime/*.sqlite` and the Jev records. Restore takes an
  explicit DB name when a backup holds more than one, and moves `-wal`/`-shm`
  aside. Retention no longer prunes an OHLCV cache that the bot never writes.
- Retired: `restructure-plan.md`, `implementation-ledger.md`,
  `legacy-project-review.md`, `legacy-review-inventory.csv`,
  `BaselineStrategy`, empty artifact placeholders. Their still-relevant
  content moved to [architecture](../architecture.md) (contracts for deferred
  execution work) and [status](../status.md) (open items, decisions, gates).
  The originals and a snapshot of the previous tree were archived outside the
  repository, since nothing had been committed yet. `docs/handoff.md` became
  `docs/status.md`, so it is no longer confused with the agents'
  `.agents/handoff.md`.

## Consequences

- One way to run anything in the pinned image: `docker compose run --rm
  <tools|research|test> …`, wrapped by the unchanged `make` targets.
- The research paths recorded in `research/experiments/H1/record.md`
  (`research/scripts/…`) are historical. The same code now lives in
  `src/sq/research/`, and the record carries a note saying so.
- Restoring from a backup that holds both a dry-run and a live DB requires
  `--db <name>`.
- Operators must copy the systemd units from `ops/systemd/` instead of
  `deploy/debian13/systemd/`.
