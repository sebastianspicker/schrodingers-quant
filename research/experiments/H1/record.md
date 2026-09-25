# H1 experiment record — channel-breakout trend following on BTC/EUR (4h)

Hypothesis: [research/hypotheses/H1.md](../../hypotheses/H1.md) (predeclared
2026-09-24). Strategy: `user_data/strategies/H1ChannelBreakout.py`. Decision:
**GO (marginal)** under the predeclared fixed-stake protocol; see
[ADR-0005](../../../docs/adr/0005-h1-go-after-sizing-correction.md).

> **Reworded 2026-09-25 for readability, at the maintainer's request.** Sections
> were reordered to put the decision first. Every number, hash, path, date and
> table value is unchanged; the JSON files in this folder remain the primary
> record. The previous wording is in `git show b753551:research/experiments/H1/record.md`
> plus the uncommitted path-mapping note of 2026-09-25.

## In short

- **Verdict: GO, marginal.** H1 passed all four predeclared criteria on the
  held-out period (2024-07-01 → 2026-09-20), with a fixed €1,000 stake.
- **The result.** +39.81 % net return at base costs, against +17.57 % for
  buy-and-hold. H1's worst fall (marked to market) was 30.37 %, against
  52.18 % for buy-and-hold.
- **The margin.** The drawdown criterion (K2) allowed at most 31.31 %; H1 passed
  it by 0.94 percentage points.
- **Why it is weak evidence.** The held-out window was run twice: a first run
  that compounded the stake, against the declared rules, failed K2. There were
  only 14 held-out trades, and a few large trends carry the result
  ([Why this is weak evidence](../../../docs/adr/0005-h1-go-after-sizing-correction.md#why-this-is-weak-evidence)).
- **What it allows.** Forward paper trading and the €10 execution pilot only.
  The held-out window is spent; further evidence must come from data after
  2026-09-24.

## Decision (K1–K4, H1.md)

| Criterion | Value | Result |
| --- | --- | --- |
| K1 held-out net return > 0 (base) | 39.81 % | pass |
| K2 held-out max DD ≤ 0.6 × B&H max DD (31.31 %) | 30.37 % | pass (margin 0.94 points) |
| K3 held-out net return > 0 (stress) | 25.55 % | pass |
| K4 validation net return > 0 (base) | 67.20 % | pass |

DD is drawdown and B&H is buy-and-hold. **GO, marginal**
([ADR-0005](../../../docs/adr/0005-h1-go-after-sizing-correction.md)).
Per H1.md, GO permits forward paper trading and the €10 execution pilot only.
The held-out window is spent; further evidence must come from forward data
collected after 2026-09-24.

## Results

All figures are for one position with a fixed €1,000 stake, marked to market
on every 4h close and net of fees (see [How the metrics are measured](#how-the-metrics-are-measured)).
Base costs are 0.5 % per side, stress costs 1.0 % per side. Buy-and-hold always
pays 0.5 %.

### Held-out (2024-07-01 → 2026-09-20)

Strategy frozen at SHA-256 `142f95a482cf41f03bf27ed071d5464c63b8abcfa64637b6a6608f5f70d7fc8e`
before the first held-out run; it was not changed afterwards.

| Run | Net return | CAGR | Max DD (marked to market) | Trades | Win rate | Avg hold | Exposure |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H1, fee 0.5 % | 39.81 % | 16.31 % | 30.37 % | 14 | 35.7 % | 535 h | 38.5 % |
| H1, fee 1.0 % | 25.55 % | 10.80 % | 36.50 % | 14 | 35.7 % | 535 h | 38.5 % |
| Buy-and-hold (0.5 %) | 17.57 % | 7.56 % | 52.18 % | — | — | — | 100 % |

A 35.7 % win rate on 14 trades means 5 winners and 9 losers. H1 was in a
position 38.5 % of the time.

#### Sizing correction (disclosed)

The first held-out run (2026-09-24) used `stake_amount: "unlimited"`, which
compounds about 99 % of the balance into every trade. H1.md specifies a fixed
stake. An independent review flagged the mismatch after that run; every period
was then re-run with the declared fixed stake, without changing the strategy,
the periods, the costs or the decision rule. The superseded compounding results
are kept in [superseded-compounding/](superseded-compounding/): held-out net
return 32.25 %, marked-to-market drawdown 35.11 %, which failed K2. The
held-out window has therefore been evaluated twice. The second run is the
protocol-conformant one, but this history weakens the result and is part of
the decision.

H1.md does not say how much idle cash sits beside the fixed stake. The account
here holds exactly one stake at the start (no idle cash), the most conservative
reading: a cash buffer would lower the percentage drawdown further.

### Validation (2023-01-01 → 2024-06-30)

| Run | Net return | CAGR | Max DD (marked to market) | Trades | Win rate | Avg hold | Exposure |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H1, fee 0.5 % | 67.20 % | 41.04 % | 20.63 % | 11 | 45.5 % | 639 h | 53.7 % |
| H1, fee 1.0 % | 55.81 % | 34.54 % | 25.93 % | 11 | 45.5 % | 639 h | 53.7 % |
| Buy-and-hold (0.5 %) | 265.23 % | 137.87 % | 20.52 % | — | — | — | 100 % |

In this strong rising market, buy-and-hold returned about four times as much
as H1, with a similar worst fall.

### Train (2020-01-01 → 2022-12-31, sanity only)

| Run | Net return | CAGR | Max DD (marked to market) | Trades | Win rate | Avg hold | Exposure |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H1, fee 0.5 % | 140.63 % | 34.10 % | 28.36 % | 16 | 50.0 % | 680 h | 42.3 % |
| H1, fee 1.0 % | 123.84 % | 30.90 % | 30.65 % | 16 | 37.5 % | 680 h | 42.3 % |
| Buy-and-hold (0.5 %) | 132.52 % | 32.59 % | 74.16 % | — | — | — | 100 % |

### Sensitivity and ETH/EUR robustness (train + validation span, fee 0.5 %; never used to select)

| Run | Net return | Max DD (marked to market) | Trades |
| --- | --- | --- | --- |
| BTC/EUR 10/5-day channels | 109.52 % | 35.84 % | 52 |
| BTC/EUR 55/20-day channels | 426.73 % | 25.14 % | 10 |
| ETH/EUR, H1's 20/10-day channels | 358.15 % | 24.29 % | 25 |

These span 2020-01 → 2024-06 as one run each, so they are not comparable
line by line with the per-period tables above. They were reported, not used to
choose H1's settings.

## How the metrics are measured

All strategy metrics are for one position of a fixed 1,000 EUR stake:
equity = 1,000 EUR + cumulative realized profit + the open trade's value on each
4h close, net of fees. Return and CAGR are relative to the 1,000 EUR notional;
drawdown is peak-to-trough of that equity. Buy-and-hold is marked to market on
the same closes with the same fee applied on entry and exit. Freqtrade's own
summary JSONs (`*-BTC_EUR.json`) report percentages of the 1,000,000 EUR backtest
wallet and closed-trade drawdown only; use the `mtm-*.json` files for decisions.

## Bias checks

Run via `freqtrade lookahead-analysis` and `freqtrade recursive-analysis` on
BTC/EUR 4h, timerange 2020-01-10 → 2024-06-30 (the train and validation span).
The start is shifted 7 days past the data's actual first candle, 2020-01-03, so
the analyses' internal short probe windows have real data; this does not change
any reported period.

- **lookahead-analysis** (does any signal use data from the future?):
  `H1ChannelBreakout: no bias detected` (20/20 signals checked; 0 biased entry
  signals, 0 biased exit signals, 0 biased indicators).
- **recursive-analysis** (do indicators change with how much history is
  loaded?): `No variance on indicator(s) found due to recursive formula.` /
  `No lookahead bias on indicators found.` (startup candles tested: 121, 199,
  399, 499, 999, 1999).

Neither check reported an issue; H1 was not modified in response (none was
needed).

## Proxy check (ADR-0002)

The backtests use Binance prices as a stand-in for Kraken, whose public API
serves only the latest 720 candles. This check compares the latest 720 Kraken 4h and 1d candles (public
API, no credentials) with the local Binance data, aligned on exact candle
timestamps. Full output: [proxy-check.json](proxy-check.json).

| Pair | Timeframe | Overlap (candles) | Return correlation | Median \|Δclose\| (bps) | p95 \|Δclose\| (bps) | OK (≥0.95 corr, ≤50bps median) |
| --- | --- | --- | --- | --- | --- | --- |
| BTC/EUR | 4h | 719 | 0.9851 | 1.06 | 4.00 | yes |
| BTC/EUR | 1d | 719 | 0.9986 | 1.98 | 15.38 | yes |
| ETH/EUR | 4h | 719 | 0.9910 | 1.36 | 5.02 | yes |
| ETH/EUR | 1d | 719 | 0.9994 | 2.13 | 15.53 | yes |

All four pair/timeframe combinations pass the gate set before the run
(correlation < 0.95 or median deviation > 50 bps would have stopped it). One
basis point (bp) is 0.01 %.

The proxy check window (2024-10 to 2026-09, limited to Kraken's public
720-candle history) overlaps the held-out period. This validates the Binance
price *data* against Kraken, not H1's strategy performance, so it is not a
held-out strategy evaluation.

## Provenance

> Paths below are as of the run (2026-09-24, before
> [ADR-0006](../../../docs/adr/0006-one-package-one-runtime-definition.md)):
> `research/scripts/*.py` now live unchanged in behavior in `src/sq/research/`,
> `Dockerfile.test` is `tests/Dockerfile`, and the restructure plan is retired.
> Since [ADR-0007](../../../docs/adr/0007-research-pipeline-in-python.md),
> `research/run.sh <subcommand>` is `make research ARGS="<subcommand>"`, and
> `research/scripts/mtm_drawdown.py` is `sq.research.metrics.mtm_report`, run
> by every backtest step.
> Container paths and file hashes recorded here are unchanged.

- **Image**: `freqtradeorg/freqtrade:2026.8@sha256:4d23160b501d2b34579e76f57ad75edfa274967cd0dd824ff1c1b86d8c166ab4`
  (pinned; matches `Dockerfile.test`).
- **Strategy file**: `user_data/strategies/H1ChannelBreakout.py`,
  SHA-256 `142f95a482cf41f03bf27ed071d5464c63b8abcfa64637b6a6608f5f70d7fc8e`.
- **Sensitivity strategy file**: `research/strategies/H1Sensitivity.py`,
  SHA-256 `f6870999abc31e859c8e524f1577e448e7538403f6e754314892b9152a5184be`.
- **Research backtest config**: `research/configs/backtest.json` (standalone;
  not derived from `config/base.json`). Fixed stake of 1,000 EUR per trade on a
  1,000,000 EUR wallet, so no trade is ever skipped for lack of funds; metrics
  are then computed against the 1,000 EUR notional by
  `research/scripts/mtm_drawdown.py --wallet 1000` (see "Sizing correction").
- **Data manifest**: [research/data-manifest.json](../../data-manifest.json),
  SHA-256 of that file `947750a0f7e93ba88bf2756ee76f29c6f97d5d763fcc46f13823fbb29cb96997`
  (Binance BTC/EUR and ETH/EUR OHLCV, 2020-01-03 onward; see ADR-0002).
- **Working tree**: the repository had no commits yet at the time of the run,
  so no commit hash is recorded.
- Every run also carries its own timerange, fee, pair, strategy name and the
  same file, image and manifest hashes in its JSON output under
  `research/experiments/H1/*.json`; this record summarizes them.

## Glossary

| Term | Meaning here |
| --- | --- |
| Held-out / validation / train | The three periods: train is a sanity check, validation a second check, held-out the one-time final test. |
| Base / stress costs | 0.5 % and 1.0 % per side (on the buy and again on the sell). |
| Net return | Gain or loss on the €1,000 stake after costs. |
| CAGR | Compound annual growth rate: the steady yearly rate that gives the same net return. |
| Max DD (marked to market) | The largest fall from a previous high, with open positions valued at every 4h close. |
| Win rate | Share of trades that closed with a profit. |
| Avg hold | Average time a position stayed open, in hours. |
| Exposure | Share of the period in which H1 held a position. |
| Notional | The reference amount (€1,000) that returns and drawdowns are measured against. |
| Compounding stake | Reinvesting (here about 99 % of) the balance in every trade, instead of a fixed amount. |
| Lookahead bias | A backtest using information that was not available at decision time. |
| Proxy data | Binance prices standing in for Kraken's. |
| Return correlation | How closely two price series move together, from 0 (unrelated) to 1 (identical). |
| bps | Basis points: hundredths of a percent. |
| SHA-256 | A fingerprint of a file; any change to the file changes it. |
