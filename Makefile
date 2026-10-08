.PHONY: help check test stats physics desk format validate validate-local validate-live validate-vps validate-report ci research \
	preflight preflight-live reconcile forward-report \
	backup retention \
	pull up down logs status

help:
	@printf '%s\n' \
	  'check          Ruff lint/format, shellcheck, Compose syntax incl. overlay examples, unit tests' \
	  'test           Unit tests of the research, live-tooling and ops logic (no Docker needed)' \
	  'stats          Rebuild research/experiments/H1/statistics.json from the recorded curves (no Docker)' \
	  'physics        Rebuild research/experiments/H1/physics.json: stylized facts, null models, forward calibration (no Docker, minutes; ARGS=...)' \
	  'desk           Offline trader report: cash sleeves, monthly returns, risk and VPS economics (ARGS=...)' \
	  'format         Format Python sources' \
	  'validate       Load tracked base config and strategy using pinned Freqtrade (Docker required)' \
	  'validate-local Also validate config/base.json layered with config/local/secrets.json' \
	  'validate-live  Validate base + credential-free config/live.json overlay (no exchange contact)' \
	  'validate-vps   Validate base + VPS overlay + example secrets (tracked files only)' \
	  'validate-report Verify the forward CLI, actual ORM schema and H2 callback in the pinned image' \
	  'ci             Run check, validate, validate-live, validate-vps, and validate-report' \
	  'research       Research commands: make research ARGS="train" (see src/sq/research)' \
	  'preflight      Read-only Kraken feasibility check: make preflight ARGS="--pair BTC/EUR --stake 8 --stoploss -0.20"' \
	  'preflight-live Account-required check using base + live + local secrets (never places orders)' \
	  'reconcile      Read-only DB vs Kraken reconciliation (needs config/local/secrets.json)' \
	  'forward-report Forward paper-trading report from the dry-run DB and public Kraken candles' \
	  'backup         Consistent SQLite + config backup (ops/backup.sh)' \
	  'retention      Show the Docker image pruning retention would do; add ARGS=--apply to act' \
	  'pull           Download the pinned Freqtrade image used by all services' \
	  'up             Start the stopped, non-trading dry-run scaffold' \
	  'down           Stop and remove containers; retain bind-mounted state' \
	  'logs           Follow container logs' \
	  'status         Show container status'

check: test
	uv run --locked ruff check .
	uv run --locked ruff format --check .
	docker compose config --quiet
	docker compose -f compose.yaml -f compose.vps.example.yaml config --quiet
	docker compose -f compose.yaml -f compose.override.example.yaml config --quiet
	shellcheck ops/*.sh pages/build.sh

test:
	uv run --locked pytest

stats:
	PYTHONPATH=src uv run --locked python -m sq.research stats

physics:
	PYTHONPATH=src uv run --locked python -m sq.research physics $(ARGS)

desk:
	PYTHONPATH=src uv run --locked python -m sq.research.desk $(ARGS)

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

validate-report:
	docker compose run --rm -T tools python /workspace/tests/check_freqtrade_contract.py

ci: check validate validate-live validate-vps validate-report

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

forward-report:
	docker compose run --rm tools python -m sq.live.forward $(ARGS)

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
