# Jev (optional model filter)

Code: `src/sq/jev/` (protocol, providers, worker, evaluate, prompts) and
`user_data/strategies/H1JevShadow.py`. Architecture and dependency rules:
[architecture](architecture.md).

Jev is a prospective optional observation and entry filter. It is not called
by the current tracked default (`H1ChannelBreakout` stays the dry-run
strategy) and has no role in order execution. No model key or exchange
credential belongs in this directory or in Freqtrade config; a key, when one
exists, comes from the `JEV_API_KEY` environment variable only (see
`src/sq/jev/providers.py` and the `jev-worker` service in `compose.yaml`).

Begin by recording Jev's assessment alongside baseline decisions without
changing orders. If useful, test a predeclared filter against the *same*
baseline on chronological held-out and forward periods. For each assessment,
retain the source input and its publication time, decision time, time the
model result became available, model and prompt versions, output, and
latency. Use only assessments actually available before the simulated
decision; historical model knowledge may still leak future information.

Compare after fees, spread, slippage, and missed fills, and report return,
drawdown, exposure, and skipped trades. A model confidence field is not a
calibrated probability of trade profit. If a later live design requires a
Jev assessment and it is missing or expired, block only new entries.
Deterministic risk limits and exits must continue without Jev.

## Status

- **Shadow mode and the offline-comparison and live-filter mechanisms are
  built** (`sq.jev`, `user_data/strategies/H1JevShadow.py`). No real model
  calls exist: the only working provider is `NullProvider`, which always
  abstains. `JevProvider` is a documented stub that raises
  `NotImplementedError`; this repository has no Jev API access or
  documentation.
- **The offline matched comparison needs real recorded assessments** on
  forward (post-shadow-run) data before it says anything. `sq.jev.evaluate`
  runs today against `NullProvider` output, but an all-abstain assessments
  file trivially filters every trade out; that is not evidence of anything.
- **The live filter is a separate, evidence-based decision**, gated on a
  real provider existing and a matched offline comparison showing the filter
  is worth the entries it blocks. Nothing in the code flips that switch by
  itself: `mode` defaults to `"off"`.

## Modes (`H1JevShadow`, `confirm_trade_entry` only)

Set via the merged Freqtrade config's `"jev"` key (falls back to
`SQ_JEV_MODE`/`SQ_JEV_DIR`/`SQ_JEV_TTL_MINUTES` env vars if that key is ever
rejected by a future Freqtrade config schema; see the module docstring in
`user_data/strategies/H1JevShadow.py` for why the config key currently
survives Freqtrade's schema validation):

```json
{
  "jev": {
    "mode": "off",
    "assessment_ttl_minutes": 60,
    "dir": "/freqtrade/user_data/runtime/jev"
  }
}
```

- `"off"` (default): identical to `H1ChannelBreakout`. No file I/O.
- `"shadow"`: records each entry candidate to `candidates.jsonl`; always
  returns `True` (approves the entry). Orders are never affected.
- `"filter"`: records the candidate, then blocks the entry unless an
  assessment for it exists, is not expired (`available_at + ttl >= now`),
  and its `decision` is `"approve"`. Any internal error (missing
  DataProvider, unreadable files, a malformed assessment line, ...) is
  caught inside the strategy and treated as a block. See the "why never
  raise" section of the module docstring: Freqtrade's own
  `strategy_safe_wrapper` defaults an *uncaught* exception here to
  `default_retval=True` (allow), which would silently defeat a filter.

### Confirm-trade-entry retry window

A rejected `confirm_trade_entry` is retried on every subsequent main-loop
iteration (every `internals.process_throttle_secs`, 5s in
`config/base.json`) for as long as the same closed candle's entry signal
persists: for this 4h strategy, up to just under 4 hours. A real worker's
end-to-end latency (poll interval + provider call + write) must stay well
inside that window, or an eventual `approve` will arrive too late for any
retry to see it, and that entry opportunity is gone. See the full sourced
finding (file/line references into `freqtrade/freqtradebot.py`) in the
`H1JevShadow` module docstring.

## File formats

`<dir>` defaults to `/freqtrade/user_data/runtime/jev` inside the trading
container (`user_data/runtime/jev` on the host; ignored by git, like the rest
of `user_data/runtime/`).

**`candidates.jsonl`** (written by `H1JevShadow`, one line per candidate,
deduplicated by `candidate_id`):

```json
{"candidate_id": "<sha256 hex>", "pair": "BTC/EUR", "signal_candle_time": "2026-09-24T12:00:00+00:00", "rate": 61234.5, "observed_at": "2026-09-24T12:00:03.123456+00:00", "strategy_name": "H1JevShadow", "strategy_version": "<sha256 hex>"}
```

`candidate_id` is a stable sha256 of the strategy name, pair, signal candle
UTC time and a "strategy file identity" hash (sha256 of this file's and the
inherited `H1ChannelBreakout`'s source, excluding Freqtrade's own code). See
the module docstring for the exact construction.

**`assessments.jsonl`** (written by `sq.jev.worker`, at most one query per
`candidate_id`, ever):

```json
{"candidate_id": "<sha256 hex>", "input": {"...": "the exact candidate payload sent"}, "input_timestamp": "2026-09-24T12:00:03.123456+00:00", "model": "null", "model_version": "v0", "prompt_version": "sha256:<hex prefix>", "decision": "abstain", "rationale": "...", "confidence": null, "requested_at": "2026-09-24T12:05:00+00:00", "available_at": "2026-09-24T12:05:00.010000+00:00", "latency_ms": 10.0, "error": null}
```

`decision` is one of `approve`/`reject`/`abstain`. `confidence`, if present,
is the provider's own confidence in its decision, **not** a calibrated
probability of trade profit. A provider exception or timeout is recorded as
`decision: "abstain"` with `error` set to the exception's class name (or
`"TimeoutError"`); the worker never crashes or silently drops a candidate.

**`worker_cursor.txt`**: a persisted byte offset into `candidates.jsonl`, so
restarts resume without rescanning the whole file. `worker.lock`: an
exclusive lock file for the single-instance guarantee; present only while a
worker run is in progress.

## Running shadow mode

Shadow mode never affects orders; it is safe to run against the tracked
dry-run bot. Create a local overlay (gitignored, not committed):

```json
// config/local/jev-shadow.json
{
  "strategy": "H1JevShadow",
  "jev": {"mode": "shadow"}
}
```

Then layer it in, e.g. via `compose.override.yaml`
(see `compose.override.example.yaml`) adding
`--config /freqtrade/config/local/jev-shadow.json` to the `command` list, or
directly with `docker compose run`:

```sh
docker compose run --rm freqtrade trade \
  --config /freqtrade/config/base.json \
  --config /freqtrade/config/local/jev-shadow.json
```

To also run the worker (writes only `NullProvider` abstains until a real
provider exists):

```sh
docker compose --profile jev up -d jev-worker
```

The file names, JSONL format and decisions are defined once in
`sq.jev.protocol`. `H1JevShadow` keeps its own copy, because strategies never
import `sq`, and `tests/jev/test_protocol.py` checks that the two agree.

## Evaluation (offline comparison)

```sh
docker compose run --rm research python -m sq.jev.evaluate \
  --trades /freqtrade/user_data/backtest_results/<result>.zip \
  --assessments /freqtrade/user_data/runtime/jev/assessments.jsonl \
  --wallet 8 \
  --candles /freqtrade/user_data/data/binance --pair BTC/EUR --timeframe 4h
```

Reports, for the baseline (all trades) and the filtered set (only trades
whose candidate had a timely `approve`): trade count, net return on the
fixed notional, and, if `--candles` is given, marked-to-market drawdown
(`sq.research.mtm_drawdown.equity_curve`), plus `skipped_trades` and
`trades_lost_for_lack_of_timely_assessment` (the subset of skipped trades
caused by a missing or late assessment rather than an explicit reject).

Freqtrade's own trade records carry no `candidate_id` (`confirm_trade_entry`
runs before the `Trade` exists, so it cannot attach one). `sq.jev.evaluate`
therefore matches each trade to the nearest *preceding* recorded candidate
for the same pair, within `--max-lag-hours` (default 8h = 2x H1's
timeframe); only assessments with `available_at` strictly before the trade's
entry time are used, so a forward-run backtest export cannot leak a
same-run assessment into its own comparison. Treat this matching as an
approximation: a future exact link (e.g. via a persisted per-trade tag) is
future work if the approximation proves too coarse, not something this
harness invents now.

## What is blocked

- **A real provider** needs actual Jev API access and documentation (none
  exist here): endpoint, auth, request/response schema, pricing, rate limits,
  and a spending cap enforced by the worker. `sq.jev.providers.JevProvider`
  documents exactly what is missing and raises `NotImplementedError` rather
  than guessing.
- **The offline comparison** needs assessments recorded by shadow mode
  running on real forward (post-implementation) data; nothing here backfills
  or simulates that. It must also compare the model against a deterministic
  rule on the same candidates before attributing any value to the model.
- **Live filter mode** is a separate decision gated on that evidence, per the
  project rules in AGENTS.md ("Compare the same baseline with and without
  Jev ... Paper fills are not evidence of real execution quality"). The code
  makes filter mode possible to switch on later; it does not recommend
  switching it on.
