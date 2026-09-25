# Jev (optional model filter)

Code: `src/sq/jev/` (protocol, providers, worker, prompts),
`src/sq/research/jev_evaluation.py` and `user_data/strategies/H1JevShadow.py`.
Architecture and dependency rules: [architecture](architecture.md).

## In short

- **What Jev is.** A possible future filter in which an external model looks
  at each entry the strategy wants to make and approves, rejects or abstains.
  It never touches orders and never sees exchange credentials.
- **What works today.** The mechanism: recording entry candidates, a worker
  that asks a model and records the answer, a filter mode that fails closed,
  and an offline comparison harness. The only working model is a placeholder
  that always abstains.
- **What does not exist.** A real model provider. There is no Jev API access or
  documentation, so nothing has been evaluated and there is no evidence that
  Jev helps.
- **What is safe to run.** Shadow mode: it records candidates and never changes
  a trade. Filter mode is off by default and needs a separate, evidence-based
  decision before it is switched on.

## Purpose and ground rules

Jev is a prospective, optional observation and entry filter. The current
tracked default does not call it (`H1ChannelBreakout` stays the dry-run
strategy), and it has no role in order execution. No model key or exchange
credential belongs in this directory or in the Freqtrade config. A model key,
when one exists, comes only from the `JEV_API_KEY` environment variable (see
`src/sq/jev/providers.py` and the `jev-worker` service in `compose.yaml`).

The plan is staged:

1. **Record first.** Record Jev's assessment next to the baseline's decisions
   without changing any order.
2. **Then test a filter.** If that looks useful, test a predeclared filter
   against the *same* baseline on chronological held-out and forward periods.

For each assessment, keep the source input and its publication time, the
decision time, the time the model's result became available, the model and
prompt versions, the output and the latency. Use only assessments that were
actually available before the simulated decision. Even then, the model's
knowledge of history may leak information about the future.

Compare after fees, spread, slippage and missed fills, and report return,
drawdown, exposure and skipped trades. A model's confidence field is not a
calibrated probability that a trade will be profitable. If a later live design
requires a Jev assessment and it is missing or expired, block only new
entries. Deterministic risk limits and exits must keep working without Jev.

## Status

- **Shadow mode, the offline comparison and the live-filter mechanism are
  built** (`sq.jev`, `user_data/strategies/H1JevShadow.py`). No real model is
  called: the only working provider is `NullProvider`, which always abstains.
  `JevProvider` is a documented stub that raises `NotImplementedError`; this
  repository has no Jev API access or documentation.
- **The offline matched comparison needs real recorded assessments** on forward
  data (collected after a shadow run) before it says anything.
  `make research ARGS=jev-evaluate …` runs today against `NullProvider` output,
  but an assessments file in which every answer is "abstain" trivially filters
  out every trade. That is not evidence of anything.
- **The live filter is a separate, evidence-based decision.** It is gated on a
  real provider existing and on a matched offline comparison showing that the
  filter is worth the entries it blocks. Nothing in the code flips that switch
  by itself: `mode` defaults to `"off"`.

## Modes (`H1JevShadow`, `confirm_trade_entry` only)

The mode is set with the merged Freqtrade config's `"jev"` key. If a future
Freqtrade config schema ever rejects that key, the strategy falls back to the
`SQ_JEV_MODE`, `SQ_JEV_DIR` and `SQ_JEV_TTL_MINUTES` environment variables. The
module docstring in `user_data/strategies/H1JevShadow.py` explains why the
config key currently survives Freqtrade's schema validation.

```json
{
  "jev": {
    "mode": "off",
    "assessment_ttl_minutes": 60,
    "dir": "/freqtrade/user_data/runtime/jev"
  }
}
```

- `"off"` (default): behaves exactly like `H1ChannelBreakout`. No file I/O.
- `"shadow"`: records each entry candidate to `candidates.jsonl` and always
  returns `True` (approves the entry). Orders are never affected.
- `"filter"`: records the candidate, then blocks the entry unless an assessment
  for it exists, has not expired (`available_at + ttl >= now`), and has the
  `decision` `"approve"`. Any internal error (a missing DataProvider,
  unreadable files, a malformed assessment line, …) is caught inside the
  strategy and treated as a block. This matters because Freqtrade's own
  `strategy_safe_wrapper` treats an *uncaught* exception here as
  `default_retval=True` (allow), which would silently defeat a filter; see the
  "why never raise" section of the module docstring.

### Retry window for a rejected entry

A rejected `confirm_trade_entry` is retried on every following main-loop
iteration (every `internals.process_throttle_secs`, 5 s in `config/base.json`)
for as long as the same closed candle's entry signal persists. For this 4h
strategy that is up to just under 4 hours. A real worker's end-to-end latency
(poll interval, provider call and write) must stay well inside that window.
Otherwise an eventual `approve` arrives too late for any retry to see it, and
that entry opportunity is gone. The `H1JevShadow` module docstring has the full
sourced finding, with file and line references into
`freqtrade/freqtradebot.py`.

## File formats

`<dir>` defaults to `/freqtrade/user_data/runtime/jev` inside the trading
container, which is `user_data/runtime/jev` on the host (ignored by git, like
the rest of `user_data/runtime/`).

**`candidates.jsonl`** is written by `H1JevShadow`, one line per candidate,
deduplicated by `candidate_id`:

```json
{"candidate_id": "<sha256 hex>", "pair": "BTC/EUR", "signal_candle_time": "2026-09-24T12:00:00+00:00", "rate": 61234.5, "observed_at": "2026-09-24T12:00:03.123456+00:00", "strategy_name": "H1JevShadow", "strategy_version": "<sha256 hex>"}
```

`candidate_id` is a stable SHA-256 of the strategy name, the pair, the signal
candle's UTC time and a "strategy file identity" hash (a SHA-256 of this
file's and the inherited `H1ChannelBreakout`'s source, excluding Freqtrade's
own code). The module docstring gives the exact construction.

**`assessments.jsonl`** is written by `sq.jev.worker`, which queries at most
once per `candidate_id`, ever:

```json
{"candidate_id": "<sha256 hex>", "input": {"...": "the exact candidate payload sent"}, "input_timestamp": "2026-09-24T12:00:03.123456+00:00", "model": "null", "model_version": "v0", "prompt_version": "sha256:<hex prefix>", "decision": "abstain", "rationale": "...", "confidence": null, "requested_at": "2026-09-24T12:05:00+00:00", "available_at": "2026-09-24T12:05:00.010000+00:00", "latency_ms": 10.0, "error": null}
```

`decision` is one of `approve`, `reject` or `abstain`. `confidence`, if
present, is the provider's own confidence in its decision, **not** a calibrated
probability of trade profit. A provider exception or timeout is recorded as
`decision: "abstain"` with `error` set to the exception's class name (or
`"TimeoutError"`). The worker never crashes and never silently drops a
candidate.

**`worker_cursor.txt`** stores a byte offset into `candidates.jsonl`, so a
restarted worker resumes without rescanning the whole file.
**`worker.lock`** is an exclusive lock file that guarantees a single worker
instance; it exists only while a worker run is in progress.

## Running shadow mode

Shadow mode never affects orders, so it is safe to run against the tracked
dry-run bot. Create a local overlay (ignored by git, not committed):

```json
// config/local/jev-shadow.json
{
  "strategy": "H1JevShadow",
  "jev": {"mode": "shadow"}
}
```

Then layer it in, either through `compose.override.yaml` (see
`compose.override.example.yaml`) by adding
`--config /freqtrade/config/local/jev-shadow.json` to the `command` list, or
directly with `docker compose run`:

```sh
docker compose run --rm freqtrade trade \
  --config /freqtrade/config/base.json \
  --config /freqtrade/config/local/jev-shadow.json
```

To also run the worker, which writes only `NullProvider` abstains until a real
provider exists:

```sh
docker compose --profile jev up -d jev-worker
```

The file names, JSONL format and decisions are defined once, in
`sq.jev.protocol`. `H1JevShadow` keeps its own copy, because strategies never
import `sq`; `tests/jev/test_protocol.py` checks that the two agree.

## Evaluation (offline comparison)

```sh
docker compose run --rm research python -m sq.research jev-evaluate \
  --trades /freqtrade/user_data/backtest_results/<result>.zip \
  --assessments /freqtrade/user_data/runtime/jev/assessments.jsonl \
  --wallet 8 \
  --candles /freqtrade/user_data/data/binance --pair BTC/EUR --timeframe 4h
```

The report covers the baseline (all trades) and the filtered set (only trades
whose candidate received a timely `approve`): trade count, net return on the
fixed notional and, if `--candles` is given, marked-to-market drawdown
(`sq.research.metrics.equity_curve`). It also reports `skipped_trades` and
`trades_lost_for_lack_of_timely_assessment`, the subset of skipped trades caused
by a missing or late assessment rather than an explicit reject.

Freqtrade's own trade records carry no `candidate_id`: `confirm_trade_entry`
runs before the `Trade` exists, so it cannot attach one.
`sq.research.jev_evaluation` therefore matches each trade to the nearest
*preceding* recorded candidate for the same pair, within `--max-lag-hours`
(default 8 h, twice H1's timeframe). Only assessments with `available_at`
strictly before the trade's entry time are used, so a backtest export from a
forward run cannot leak a same-run assessment into its own comparison. Treat
this matching as an approximation. An exact link (for example a persisted
per-trade tag) is future work if the approximation proves too coarse; this
harness does not invent one now.

## What is blocked

- **A real provider** needs actual Jev API access and documentation, and none
  exist here: the endpoint, authentication, request and response schema,
  pricing, rate limits, and a spending cap enforced by the worker.
  `sq.jev.providers.JevProvider` documents exactly what is missing and raises
  `NotImplementedError` rather than guessing.
- **The offline comparison** needs assessments recorded by shadow mode running
  on real forward data (collected after implementation); nothing here backfills
  or simulates that. It must also compare the model against a deterministic rule
  on the same candidates before attributing any value to the model.
- **Live filter mode** is a separate decision gated on that evidence, per the
  project rules in AGENTS.md ("Compare the same baseline with and without Jev
  ... Paper fills are not evidence of real execution quality"). The code makes
  filter mode possible to switch on later; it does not recommend switching it
  on.

## Glossary

| Term | Meaning here |
| --- | --- |
| Candidate | An entry the strategy wants to make, recorded before Freqtrade opens the trade. |
| Assessment | Jev's answer for one candidate: approve, reject or abstain, with timing and version data. |
| Provider | The code that calls a model. `NullProvider` always abstains; `JevProvider` is an unimplemented stub. |
| Shadow mode | Records candidates without affecting any trade. |
| Filter mode | Blocks an entry unless a fresh `approve` exists. |
| Fail closed | On any error, block the entry rather than allow it. |
| TTL | Time to live: how long an assessment stays valid (default 60 minutes). |
| `confirm_trade_entry` | The Freqtrade strategy hook called just before an entry order is placed. |
| Leakage | Future information reaching a test, for example through a model's knowledge of history. |
| Matched comparison | Evaluating the same baseline trades with and without the filter. |
| Calibrated probability | A number that matches observed frequencies, which a model's confidence field is not. |
| Notional | The fixed reference amount that returns are measured against. |
| JSONL | A text file with one JSON object per line. |
| Deterministic rule | A fixed, non-model rule used as a comparison before crediting the model. |
