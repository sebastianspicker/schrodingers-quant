# Forward test protocol, version 2

Revised 2026-10-08 before deployment; [ADR-0007](adr/0007-trader-workbench.md)
supersedes the measurement and monitoring contract in ADR-0006. H1's entry/exit
rules and F1–F4 thresholds remain unchanged. A running window must retain its
protocol; archive its evidence before a deliberate new window.

## Start and daily operation

Record the UTC 4h boundary following `/start`, image, commit and config layers.
Use the same `--since` on every daily run. For example (choose the real boundary
and account capital, rather than copying this date):

```sh
# One-shot diagnostics; prints JSON, no order mutation:
make forward-report ARGS="--since 2026-10-12T00:00:00Z --initial-capital 10"
# Persistent records, window binding and STOP latch, also used by the timer:
sh ops/forward_report.sh --since 2026-10-12T00:00:00Z --initial-capital 10
```

Set `SQ_FORWARD_ARGS` to those arguments and `HEALTH_FORWARD_REPORT` to
`/opt/schrodingers-quant/user_data/runtime/reports/forward-latest.json` in the
host's ops.env. Enable `sq-forward-report.timer` with the health timer. The
health check rejects stale reports after 36 hours by default. A process
heartbeat cannot clear a latched STOP. See [operations](../ops/README.md).

Without `--since`, the window begins at the first filled trade's original
submission candle. This diagnostic mode cannot detect signals missed before
that first order. Declare the start explicitly for a real forward experiment.
Without a declared window or trades, fidelity is NOT_YET_TESTABLE.

## Durable market data

The daily report fetches public Kraken OHLC and appends **closed** candles to
`user_data/runtime/market-data.sqlite`. The archive keeps older observations
when they disappear from the public 720-candle response. No downloader daemon
or research container runs on the VPS. The normal SQLite backup includes it.

The report rejects gaps, invalid OHLCV, conflicting duplicate/revised candles,
and a last closed mark older than one additional 4h publication interval.
A revised historical candle needs investigation; it is not overwritten. A
complete, validated Kraken history can be supplied using `--candles <feather
or ccxt-row JSON>`. Add `--archive <runtime/market-data.sqlite>` to merge that
import deliberately. `--candles` alone does not modify the archive. Imports
must reach the current closed-candle horizon too. Do not mix proxy data into
Kraken forward evidence. Archive paths must differ from the trade database.

The full window and indicator warmup must remain available. A short history
cannot silently turn the experiment into a rolling window or hide an earlier
breach. Candle hashes are stored in reports. Public-feed outages beyond the
API retention window need an external verified history to repair.

## Accounting and supported fills

Read-only SQLite transactions capture trades and orders consistently.
Unfilled entry intents do not count as positions or trades. Filled order dates
measure latency and mark exposure; original entry submission determines the
signal reference and H2 information set. Canceled orders with positive fills
are recognized. Multiple filled entries, partial exits, incomplete fill
records, mixed strategy/timeframe, and overlapping positions fail explicitly.
This is a single-position Kraken 4h H1 reporter, not a DCA accounting engine.

Only events before the last closed candle's end are counted. A later exit is
still an open marked position for this report; later entries are excluded.
Both mark-to-market curves reserve exit fees, and drawdown includes initial
capital. These are candle-close measurements, not intrabar risk estimates.

- `performance`: each trade normalized to the fixed reference stake. This
  measures strategy behavior on the backtest basis, **not account return**.
- `account`: actual quantities relative to declared initial capital, with no
  deposits/withdrawals inside the window. `--initial-capital` is required for
  explicit `--since` windows and live account reporting. Only the default
  first-trade dry-run window can infer it from `dry_run_wallet`.
- `benchmark`: fully funded buy-and-hold at the first window open, reserving
  0.5% entry and liquidation fees, marked on the same 4h candles.
- `execution`: slippage relative to the reference open (same denominator on
  buys and sells), actual fill delay and fees. Dry-run fills remain simulations.
  Only signal exits enter round-trip cost F2; stop and forced exits have a
  different reference. Database-wide order counts are labeled separately.

## Decision gates

| ID | Criterion | Result |
| --- | --- | --- |
| F1 | Every entry matches its preceding closed-candle signal; every missing entry is explained by position/cooldown, still-open entry window or recorded operator acknowledgement | Any unexplained defect: STOP. Missing startup coverage: NOT_YET_TESTABLE, never PASS |
| F2 | Mean measured signal-exit round-trip cost ≤ 2% | Higher: STOP; no measurements: NOT_YET_TESTABLE. Base assumption is 1% |
| F3 | Fixed-stake marked-to-market drawdown ≤ 31.31% (tracked K2 limit) | Higher: STOP |
| F4 | At least 30 completed trades before performance judgement | Smaller samples: NOT_YET_TESTABLE |
| P1 | Fixed-stake net return > 0 | Evaluated after F4 and valid F1; failure: STOP |
| P2 | Fixed-stake drawdown ≤ 0.6 × same-window buy-and-hold drawdown | Evaluated after F4 and valid F1; failure: STOP |

A deterministic 2,000-resample trade bootstrap reports the mean's 95% interval.
It is a descriptive IID trade check, not proof of independent trades or an edge.
Positive support requires the interval's lower bound above zero and eligible
performance evidence. Capital increases are **never authorized by the report**:
live execution within base costs, statistical review and a written ADR remain
necessary. About six H1 trades per year means years, not a 14-day soak, to F4.

`--acknowledge <signal candle UTC>` records an operator-explained missing entry.
Record its reason in the soak log; acknowledgements must recur in daily args.
No signal from the candle containing the order is used to justify the entry.
F1 requires signal coverage from the candle immediately preceding the declared
start. Later matched trades cannot make up for missing startup coverage; known
defects still cause STOP. Coverage is checked at submission time, even if the
fill arrives in a later candle.

## H2 paired tracking

H2 uses the same fills normalized to €1,000, with sizing computed from the full
120-return warmup ending before original order submission. Only equity marks
are sliced to the evaluation window. The JSON reports sizing coverage, closed
sample size, eligibility and `INCONCLUSIVE`, `GO` or `NO_GO` explicitly.

F1 and F2 must pass and all sizing history must be present. Normally 30 closed
trades are required; an F3 drawdown stop permits evaluation at 20. An F1/F2
failure is invalid evidence, not an early H2 decision. Windows before H2's
2026-10-08 declaration cannot qualify. When both return/drawdown ratios are
undefined, improvement is unmeasurable and the verdict is INCONCLUSIVE.
`all_pass` only describes raw criterion arithmetic; use `judgement_allowed`
and `verdict` for interpretation. No report changes the running strategy.

## Reports, incidents and restoration

The host runner serializes invocations and writes timestamped immutable run
files, atomically publishes `forward-latest.json`, and pins `forward-window.json`
(pair, timeframe, start, protocol, strategy/config/image hashes, notional,
account capital and drawdown threshold). Contract drift produces ERROR. Each
report retains its market-data hash and explicit basis. Config hashes exclude
credentials. Exact duplicates may share candle hashes; file hashes are evidence,
not cryptographic attestation of an exchange.
Once a manifest exists, a report with neither a declared start nor filled trades
is ERROR. An empty or restored database cannot silently remove the bound window.

The first STOP is preserved as `forward-stop.json`; later CONTINUE reports do
not clear it. The runner continues returning exit 4 while latched. A report
failure publishes ERROR and exits 1, rather than leaving an old CONTINUE fresh.
A STOP alerts the operator; use `/stopentry`, investigate, and document the
incident. It does not automatically stop Freqtrade or execute any order.

After resolving the cause, archive the STOP and supporting reports outside the
active reports directory before removing the latch. Do not reset the manifest
or start date to make a drawdown disappear. A deliberate new configuration or
window needs a separately documented closure, preserved manifest and declared
new start/capital. Backups include reports and market-data.sqlite; restoring a
trade database alone does not restore evidence. Restore those matching evidence
files too, preserve the latch, and keep the bot stopped until checked.
