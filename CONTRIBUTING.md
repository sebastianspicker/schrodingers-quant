# Contributing

Thanks for looking. This is a small project with strict rules about trading
safety, so please read this before opening a pull request.

## Set up

You need Docker with Compose, [uv](https://docs.astral.sh/uv/) and shellcheck.

```sh
uv sync --locked --group dev
make ci      # lint, format check, shellcheck, Compose checks, config validation, tests
```

Tests run inside the pinned Freqtrade image, so `make test` builds a small
image the first time. If Docker isn't available, run `make check` and say in
the pull request which checks you skipped.

## Rules that reviews enforce

- Tracked config stays spot, dry-run, credential-free and `stopped`.
  `tests/test_config_contract.py` checks this.
- Only Freqtrade places or cancels orders. Project code must not name an
  order-creating method, and there is a test for that too.
- Strategies in `user_data/strategies/` don't import `sq`.
  `H1ChannelBreakout.py` is frozen: its SHA-256 is part of the experiment
  record. A changed strategy is a new hypothesis, not an edit.
- New project Python goes in `src/sq/` and follows the dependency rules in
  [architecture](docs/architecture.md) (`tests/test_architecture.py`).
- Research changes start with a written hypothesis in `research/hypotheses/`
  before anyone looks at the evaluation data. Held-out data is run once.
- Keep dependencies small and versions pinned. Update the docs when behavior
  changes, and record decisions as ADRs in `docs/adr/`.
- Never commit secrets, account exports, databases or downloaded market data.

## Pull requests

Describe what changed and why, list the checks you ran with their result, and
link an issue or ADR for anything that changes trading behavior. Keep pull
requests focused; a refactor and a behavior change are easier to review
separately.

AI coding agents working in this repository also read [AGENTS.md](AGENTS.md).
