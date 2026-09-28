.PHONY: help check format validate validate-local validate-live validate-vps ci research preflight preflight-live reconcile \
	backup retention \
	pull up down logs status

help:
	@printf '%s\n' \
	  'check          Ruff lint/format, shellcheck, Compose syntax incl. overlay examples' \
	  'format         Format Python sources' \
	  'validate       Load tracked base config and strategy using pinned Freqtrade (Docker required)' \
	  'validate-local Also validate config/base.json layered with config/local/secrets.json' \
	  'validate-live  Validate base + credential-free config/live.json overlay (no exchange contact)' \
	  'validate-vps   Validate base + VPS overlay + example secrets (tracked files only)' \
	  'ci             Run check, validate, validate-live, and validate-vps in order' \
	  'research       Research commands: make research ARGS="train" (see src/sq/research)' \
	  'preflight      Read-only Kraken feasibility check: make preflight ARGS="--pair BTC/EUR --stake 8 --stoploss -0.20"' \
	  'preflight-live Account-required check using base + live + local secrets (never places orders)' \
	  'reconcile      Read-only DB vs Kraken reconciliation (needs config/local/secrets.json)' \
	  'backup         Consistent SQLite + config backup (ops/backup.sh)' \
	  'retention      Show the Docker image pruning retention would do; add ARGS=--apply to act' \
	  'pull           Download the pinned Freqtrade image used by all services' \
	  'up             Start the stopped, non-trading dry-run scaffold' \
	  'down           Stop and remove containers; retain bind-mounted state' \
	  'logs           Follow container logs' \
	  'status         Show container status'

check:
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	docker compose config --quiet
	docker compose -f compose.yaml -f compose.vps.example.yaml config --quiet
	docker compose -f compose.yaml -f compose.override.example.yaml config --quiet
	shellcheck ops/*.sh pages/build.sh

format:
	uv run --locked ruff format .

validate:
	docker compose run --rm tools python -m sq.config

validate-local:
	docker compose run --rm tools python -m sq.config \
	  --config /freqtrade/config/base.json \
	  $(if $(wildcard config/local/secrets.json),--config /freqtrade/config/local/secrets.json,)

validate-live:
	docker compose run --rm tools python -m sq.config \
	  --config /freqtrade/config/base.json --config /freqtrade/config/live.json

validate-vps:
	docker compose run --rm tools python -m sq.config \
	  --config /freqtrade/config/base.json \
	  --config /freqtrade/config/examples/vps-dryrun.example.json \
	  --config /freqtrade/config/examples/secrets.example.json \
	  --allow-placeholders

ci: check validate validate-live validate-vps

research:
	docker compose run --rm research python -m sq.research $(ARGS)

preflight:
	docker compose run --rm tools python -m sq.live.preflight $(ARGS)

preflight-live:
	docker compose run --rm tools python -m sq.live.preflight \
	  --config /freqtrade/config/base.json --config /freqtrade/config/live.json \
	  --config /freqtrade/config/local/secrets.json --require-account-data $(ARGS)

reconcile:
	docker compose run --rm tools python -m sq.live.reconcile \
	  --config /freqtrade/config/base.json --config /freqtrade/config/live.json \
	  --config /freqtrade/config/local/secrets.json $(ARGS)

backup:
	sh ops/backup.sh $(ARGS)

retention:
	sh ops/retention.sh $(ARGS)

pull:
	docker compose --profile tools --profile jev pull --ignore-buildable

up:
	docker compose up -d

down:
	docker compose down

logs:
	docker compose logs --follow --tail 100

status:
	docker compose ps
