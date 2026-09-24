# H1 experiment record — channel-breakout trend following on BTC/EUR (4h)

Hypothesis: [research/hypotheses/H1.md](../../hypotheses/H1.md) (predeclared
2026-09-24). Strategy: `user_data/strategies/H1ChannelBreakout.py`. Decision:
**GO (marginal)** under the predeclared fixed-stake protocol — see
[ADR-0005](../../../docs/adr/0005-h1-go-after-sizing-correction.md) and the last section.

## Provenance

> Paths below are as of the run (2026-09-24, before
> [ADR-0006](../../../docs/adr/0006-one-package-one-runtime-definition.md)):
> `research/scripts/*.py` now live unchanged in behavior in `src/sq/research/`,
> `Dockerfile.test` is `tests/Dockerfile`, and the restructure plan is retired.
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
- **Working tree**: the repository has no commits yet (before the first
  commit), so no commit hash is recorded.
- Every run below also carries its own timerange, fee, pair, strategy name
  and the same file/image/manifest hashes in its JSON output under
  `research/experiments/H1/*.json`; this table summarizes them.

## Proxy check (ADR-0002)

Latest 720 Kraken 4h/1d candles (public API, no credentials) vs. the local
Binance feather data, aligned on exact candle timestamps. Full output:
[proxy-check.json](proxy-check.json).

| Pair | Timeframe | Overlap (candles) | Return correlation | Median \|Δclose\| (bps) | p95 \|Δclose\| (bps) | OK (≥0.95 corr, ≤50bps median) |
| --- | --- | --- | --- | --- | --- | --- |
| BTC/EUR | 4h | 719 | 0.9851 | 1.06 | 4.00 | yes |
| BTC/EUR | 1d | 719 | 0.9986 | 1.98 | 15.38 | yes |
| ETH/EUR | 4h | 719 | 0.9910 | 1.36 | 5.02 | yes |
| ETH/EUR | 1d | 719 | 0.9994 | 2.13 | 15.53 | yes |

All four pair/timeframe combinations pass the gate set before the run
(correlation < 0.95 or median deviation > 50 bps would have stopped it).
The proxy check window (2024-10 to 2026-09, limited to Kraken's public
720-candle history) overlaps the held-out period; this validates the Binance
price *data* against Kraken, not H1's strategy performance, so it is not a
held-out strategy evaluation.

## Bias checks

Run via `freqtrade lookahead-analysis` and `freqtrade recursive-analysis` on
BTC/EUR 4h, timerange 2020-01-10 → 2024-06-30 (train+validation span; the
start is shifted 7 days past the data's actual first candle, 2020-01-03, so
the analyses' internal short probe windows have real data — this does not
change any reported period).

- **lookahead-analysis**: `H1ChannelBreakout: no bias detected` (20/20
  signals checked; 0 biased entry signals, 0 biased exit signals, 0 biased
  indicators).
- **recursive-analysis**: `No variance on indicator(s) found due to recursive
  formula.` / `No lookahead bias on indicators found.` (startup candles
  tested: 121, 199, 399, 499, 999, 1999).

Neither check reported an issue; H1 was not modified in response (none was
needed).

## Metric basis

All strategy metrics below are for one position of a fixed 1,000 EUR stake:
equity = 1,000 EUR + cumulative realized profit + the open trade's value on each
4h close, net of fees. Return and CAGR are relative to the 1,000 EUR notional;
drawdown is peak-to-trough of that equity. Buy-and-hold is marked to market on
the same closes with the same fee applied on entry and exit. Freqtrade's own
summary JSONs (`*-BTC_EUR.json`) report percentages of the 1,000,000 EUR backtest
wallet and closed-trade drawdown only; use the `mtm-*.json` files for decisions.

## Results — train (2020-01-01 → 2022-12-31, sanity only)

| Run | Net return | CAGR | Max DD (marked to market) | Trades | Win rate | Avg hold | Exposure |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H1, fee 0.5 % | 140.63 % | 34.10 % | 28.36 % | 16 | 50.0 % | 680 h | 42.3 % |
| H1, fee 1.0 % | 123.84 % | 30.90 % | 30.65 % | 16 | 37.5 % | 680 h | 42.3 % |
| Buy-and-hold (0.5 %) | 132.52 % | 32.59 % | 74.16 % | — | — | — | 100 % |

## Results — validation (2023-01-01 → 2024-06-30)

| Run | Net return | CAGR | Max DD (marked to market) | Trades | Win rate | Avg hold | Exposure |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H1, fee 0.5 % | 67.20 % | 41.04 % | 20.63 % | 11 | 45.5 % | 639 h | 53.7 % |
| H1, fee 1.0 % | 55.81 % | 34.54 % | 25.93 % | 11 | 45.5 % | 639 h | 53.7 % |
| Buy-and-hold (0.5 %) | 265.23 % | 137.87 % | 20.52 % | — | — | — | 100 % |

## Sensitivity and ETH/EUR robustness (train + validation span, fee 0.5 %; never used to select)

| Run | Net return | Max DD (marked to market) | Trades |
| --- | --- | --- | --- |
| BTC/EUR 10/5-day channels | 109.52 % | 35.84 % | 52 |
| BTC/EUR 55/20-day channels | 426.73 % | 25.14 % | 10 |
| ETH/EUR, H1's 20/10-day channels | 358.15 % | 24.29 % | 25 |

These span 2020-01 → 2024-06 as one run each, so they are not comparable
line by line with the per-period tables above.

## Held-out (2024-07-01 → 2026-09-20)

Strategy frozen at SHA-256 `142f95a482cf41f03bf27ed071d5464c63b8abcfa64637b6a6608f5f70d7fc8e`
before the first held-out run; it was not changed afterwards.

| Run | Net return | CAGR | Max DD (marked to market) | Trades | Win rate | Avg hold | Exposure |
| --- | --- | --- | --- | --- | --- | --- | --- |
| H1, fee 0.5 % | 39.81 % | 16.31 % | 30.37 % | 14 | 35.7 % | 535 h | 38.5 % |
| H1, fee 1.0 % | 25.55 % | 10.80 % | 36.50 % | 14 | 35.7 % | 535 h | 38.5 % |
| Buy-and-hold (0.5 %) | 17.57 % | 7.56 % | 52.18 % | — | — | — | 100 % |

### Sizing correction (disclosed)

The first held-out run (2026-09-24) used `stake_amount: "unlimited"`, which
compounds about 99 % of the balance into every trade. H1.md specifies a fixed
stake. An independent review flagged the mismatch after that run; every
period was then re-run with the declared fixed stake, without
changing the strategy, the periods, the costs or the decision rule. The
superseded compounding results are kept in
[superseded-compounding/](superseded-compounding/): held-out net return 32.25 %,
marked-to-market drawdown 35.11 %, which failed K2. The held-out window has
therefore been evaluated twice; the second run is the protocol-conformant one,
but this history weakens the result and is part of the decision.

H1.md does not say how much idle cash sits beside the fixed stake. The account
here holds exactly one stake at the start (no idle cash), the most conservative
reading: a cash buffer would lower the percentage drawdown further.

### Decision (K1–K4, H1.md)

| Criterion | Value | Result |
| --- | --- | --- |
| K1 held-out net return > 0 (base) | 39.81 % | pass |
| K2 held-out max DD ≤ 0.6 × B&H max DD (31.31 %) | 30.37 % | pass (margin 0.94 points) |
| K3 held-out net return > 0 (stress) | 25.55 % | pass |
| K4 validation net return > 0 (base) | 67.20 % | pass |

**GO, marginal** ([ADR-0005](../../../docs/adr/0005-h1-go-after-sizing-correction.md)).
Per H1.md, GO permits forward paper trading and the €10 execution pilot only.
The held-out window is spent; further evidence must come from forward data
collected after 2026-09-24.
