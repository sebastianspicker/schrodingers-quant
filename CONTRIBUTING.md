# Contributing

Thanks for looking. This is a small project with strict rules about trading
safety, so please read this before opening a pull request.

## Set up

You need Docker with Compose, [uv](https://docs.astral.sh/uv/) and shellcheck.

```sh
uv sync --locked --group dev
make ci      # unit tests, lint, format, shellcheck, Compose, and config validation
```

Docker runs the configuration and strategy-loading validations in the pinned
Freqtrade image. If Docker isn't available, run `make test` and the Ruff/shellcheck commands and say in the
pull request which validations you skipped. `make test` runs the unit tests
alone and needs only uv.

## Rules that reviews enforce

- Tracked config stays spot, dry-run, credential-free and `stopped`.
  `make validate` checks these invariants.
- Only Freqtrade places or cancels orders. Project exchange helpers must stay
  read-only.
- Strategies in `user_data/strategies/` don't import `sq`.
  `H1ChannelBreakout.py` is frozen: its SHA-256 is part of the experiment
  record. A changed strategy is a new hypothesis, not an edit.
- New project Python goes in `src/sq/` and follows the dependency rules in
  [architecture](docs/architecture.md).
- Changes to `src/`, `ops/*.py` or the strategies come with unit tests in
  `tests/`, and `make test` must pass.
- When `research/experiments/H1/equity-curves.json` changes, rerun
  `make stats` and commit the rebuilt `statistics.json`; the drift test fails
  otherwise.
- Research changes start with a written hypothesis in `research/hypotheses/`
  before anyone looks at the evaluation data. Held-out data is run once.
- Keep dependencies small and versions pinned. Update the user-facing docs and
  experiment record when behavior changes.
- Never commit secrets, account exports, databases or downloaded market data.

## Pull requests

Describe what changed and why, list the checks you ran with their result, and
link an issue or experiment record for anything that changes trading behavior.
Keep pull requests focused; a refactor and a behavior change are easier to
review separately.
