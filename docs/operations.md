# Operations

This page is the command reference for running the project on a development
machine. For a server, use the runbooks:
[Debian 13 VPS](../ops/README.md) (host setup, systemd, control),
[soak checklist](../ops/soak-checklist.md), [live pilot](live-pilot.md) and
[Jev](jev.md).

## In short

- Every check and tool runs through `make`, inside the pinned Freqtrade image.
- `make up` starts the bot in dry-run, and it stays `stopped` until you send
  `/start`.
- Secrets live only in ignored files under `config/local/` and in an ignored
  `compose.override.yaml`.
- Passing checks show the code and configuration behave as tested. They say
  nothing about the exchange, real orders, a VPS or profit
  ([What checks do not show](#what-checks-do-not-show)).

## Local commands

| Command | Effect |
| --- | --- |
| `make check` | Ruff lint and format check, shellcheck, Compose syntax with both overlay examples |
| `make validate` | Tracked `config/base.json` alone: invariants and strategy load |
| `make validate-local` | Also base + `config/local/secrets.json` if present |
| `make validate-live` / `make validate-vps` | Base + tracked live or VPS overlay |
| `make test` | Build the `test` image and run pytest inside it (repository mounted read-only) |
| `make ci` | All of the above |
| `make research ARGS="…"` | `python -m sq.research` subcommands in the `research` service; with no argument it lists them |
| `make preflight ARGS="--pair BTC/EUR --stake 8 --stoploss -0.20"` | Read-only Kraken feasibility check |
| `make reconcile` | Read-only comparison of the trade database with Kraken; needs secrets |
| `make backup` | `ops/backup.sh`: every `user_data/runtime/*.sqlite` plus the Jev records |
| `make retention` | `ops/retention.sh`: prunes dangling images (dry-run unless `ARGS=--apply`) |
| `make up`, `make down`, `make logs`, `make status` | Local container lifecycle |
| `make research ARGS=equity-curves`, then `sh pages/build.sh` | Rebuild the demo's data from the recorded backtests and assemble the site in `build/pages/` (published by `.github/workflows/pages.yml`) |

`make up` starts only the bot, whose state is `stopped` until `/start`. Every
other in-image command runs as `docker compose run --rm <tools|research|test> …`
(Compose profile `tools`). The Jev worker starts only with
`docker compose --profile jev up -d jev-worker`.

## Secrets and local overlays

1. Copy `config/examples/secrets.example.json` to `config/local/secrets.json`.
2. Copy `compose.override.example.yaml` to `compose.override.yaml`.

Both copies are ignored by git. Compose replaces the whole `command` list, so
the override restates it with the extra `--config`. Replace every placeholder,
including the API `jwt_secret_key`. Never commit credentials, databases,
account exports or market data, and never paste secrets into terminals,
tickets or chat.

## What checks do not show

Validation and tests do not show exchange connectivity, order acceptance,
partial-fill handling, exchange-side stops, restart reconciliation with real
orders, VPS behavior or profitability. Those need the soak and the live pilot.

## Glossary

| Term | Meaning here |
| --- | --- |
| Pinned image | The Freqtrade Docker image fixed by its digest in `compose.yaml`, so every run uses identical software. |
| Overlay | A config file layered on top of `config/base.json`; later layers override earlier ones. |
| Dry-run | Freqtrade's simulation mode: no orders are sent. |
| `stopped` state | The bot runs but opens no trades until `/start`. |
| Preflight | A read-only check that a stake can enter and still exit at the stop after fees, minimums and precision. |
| Reconciliation | A read-only comparison of the bot's trade database with the exchange. |
| Dangling image | An old, untagged Docker image layer that nothing uses. |
| Compose profile | A named group of Compose services that only start when that profile is requested. |
| Soak | A 14-day unattended dry-run on the VPS, with drills. |
