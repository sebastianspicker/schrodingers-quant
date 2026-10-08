# ADR-0008 — Physics-informed null models, a calibrated forward test, and a trials ledger

**Date:** 2026-10-08. **Status:** accepted. Extends [ADR-0006](0006-evidence-standard.md)
(the evidence standard) and [ADR-0007](0007-trader-workbench.md) (the
workstation workbench). H1's rules, trades and verdict are unchanged; the record gains a section.

## In short

- The record gains a third assessment file, `physics.json`, built by
  `make physics` without Docker from a tracked copy of the proxy candles.
  It contains the stylized facts of the market, H1 replayed on surrogate and
  simulated markets that share those facts but have no edge, the forward
  protocol's false-GO rate under such markets, a first-passage check of the
  stop, growth and Kelly figures, and a ledger of every evaluation of a rule on recorded data with
  the deflated Sharpe ratio. [docs/physics.md](../physics.md) explains the
  physics and its limits.
- The set of null models was declared in the code and in this ADR's draft
  before the first build and has not changed since. The repository history
  cannot show that order, so the claim rests on the author; what it can show
  is that no null was added or removed after the first build.
- A pure reimplementation of H1's rules (`sq.research.breakout`) is accepted
  as a research tool only because a test shows it reproduces all 41 recorded
  trades to the candle on the tracked candles. It models the StoplossGuard
  protection and not the MaxDrawdown protection; neither fired in the record.
  It never runs in the bot.
- Nothing in this ADR is evidence of an edge or a capital decision. The
  held-out window was inspected before these diagnostics existed; on it they
  are descriptive. A future forward GO is to be read against the false-GO
  rates recorded here.

## Context

ADR-0006 established that H1's held-out result is not distinguishable from
zero and added two nulls that keep the real price path and vary the strategy
(random timing at equal exposure, constant exposure). What was missing:

1. Nulls that keep the strategy and vary the market: does a market with the
   same tails and volatility clustering but no trends produce H1's result as
   often? Is there any persistence in BTC/EUR 4h returns for a breakout to
   earn from?
2. The forward test's own error rate. ADR-0006 fixed P1 and P2 after 30
   trades but did not say how often a market with no edge would pass them.
   A GO from a test with a 40 % false-GO rate means something different from
   one with 5 %.
3. A single-trader view of sizing. The fixed-stake record reports an ensemble
   average; the superseded compounding run showed how different the time
   average can be.
4. An explicit count of how many times the record was looked at, which the
   deflated Sharpe ratio needs.

The project's name promised physics. Section 3 of
[physics.md](../physics.md) states the one exact mapping (Fokker–Planck to
imaginary-time Schrödinger, the stop as an absorbing barrier) and what is
deliberately not used.

## Decision

1. **Tracked proxy candles.** `research/data/binance-BTC_EUR-4h.csv.gz` holds
   the Binance BTC/EUR 4h history (2020-01-03 to 2026-09-24, 14,736 rows, one
   maintenance gap) with provenance and hash in `research/data/manifest.json`.
   The record's pinned `research/data-manifest.json` is left byte-identical
   because the H1 result files carry its SHA-256. The tracked copy is
   the same proxy ADR-0002 validated against Kraken; the last decimals of a
   few candles differ from the feather the container downloaded, and
   `tests/test_breakout.py` bounds the effect (every trade's dates and exit
   reason match; returns within 0.05 points; window return and drawdown
   within 0.02 points).
2. **A verified replay of H1.** `sq.research.breakout` reproduces Freqtrade's
   backtest semantics for H1 (signal on close, fill at next open, exit
   signal before stop in the same candle, stop at stop price or at a gap
   open, force exit at the window end, Freqtrade's fee arithmetic, the
   StoplossGuard lock after two stop exits in 30 days). The MaxDrawdown
   protection is not modelled because its semantics depend on the realised
   profit path and the record never triggered it; every null reports the
   share of paths with stop exits, where that gap could matter. The replay
   is licensed by that test and by nothing else; a change that breaks the
   test invalidates every null built on it.
3. **The predeclared null set** (`sq.research.nulls`), on the held-out,
   validation and train windows at base costs, 1,000 trials each:
   5-day block shuffle; IAAFT surrogate; GBM with zero drift; GARCH(1,1)-t
   fitted to the window with zero drift and with the window's sample-mean
   drift; MRW fitted to the window with zero drift. Synthetic paths get wicks
   resampled from the real candles and the same warm-up the real run had.
   The reported figures are the shares of trials with return at least H1's,
   drawdown at most H1's, and both. No further null is added to the record
   without a new ADR.
4. **Forward-protocol calibration.** The same fitted models (on the full
   2020–2026 history), 1,000 ten-year paths each; the window ends at the 30th
   closed trade; F3, P1 and P2 as in [forward-test.md](../forward-test.md),
   with buy-and-hold on the forward report's basis. The record carries, per
   model, the share reaching 30 trades, the years to 30 trades, the F3 stop
   share and the GO share. Under a zero-drift model the GO share is the
   protocol's false-GO rate; under the drifted model it is the pass rate in a
   rising market without timing skill. Both are the reference against which
   a forward GO is read.
5. **Stylized facts, first passage, growth, ledger** are recorded as
   diagnostics (`sq.research.stylized`, `nulls.first_passage_check`,
   `sq.research.growth`, `sq.research.ledger`). The desk report gains a
   growth block. `research/ledger.json` lists every evaluation of a rule on
   recorded data and is appended, never edited, when a new one happens.
6. **Reproducibility and drift.** Fixed seed, legacy `RandomState` streams,
   one stream per model. Each Monte Carlo section records a SHA-256 of its
   first 25 per-trial outcomes; a unit test rebuilds the cheap sections
   exactly and the 25-trial prefixes of every null run and of the calibration,
   and fails if any of them drifts. The aggregate shares beyond the prefix are
   not re-derived by the test; `make physics` reproduces them.
7. **What the nulls may not be used for.** They do not change H1's rules or
   its verdict. A physics-derived entry filter or regime switch on existing
   data is a new hypothesis for forward data only (ADR-0004 spends the
   2024-07 to 2026-09 window for every successor).

## What the first build shows (held-out window, base costs)

| Figure | Value |
| --- | --- |
| Hurst exponent of returns (DFA) | 0.531; iid shuffle range 0.447 to 0.554 (inside it in every period and on the full history) |
| Hurst exponent of absolute returns | 0.757 (full history 0.858): strong long memory of volatility |
| Hill tail index, losses / gains | 2.64 / 2.60 (full history 2.31 / 2.43): heavier than the inverse-cubic law |
| Excess kurtosis | 7.3 (full history 19.8) |
| Permutation entropy | 0.9989, shuffle range 0.9988 to 0.9997 (inside); below its range on the train period (0.9986 against 0.9991 to 0.9998) and on the full history (0.9993 against 0.9996 to 0.9999) |
| MRW intermittency λ² | 0.043 |
| Share of nulls with return ≥ H1's: block shuffle / IAAFT / GBM / GARCH-t / GARCH-t with the window's mean drift / MRW | 9.5 % / 16 % / 16 % / 26 % / 33 % / 19 % (1,000 paths each) |
| Share with return ≥ and drawdown ≤ H1's | 7.2 % / 12 % / 12 % / 8.9 % / 11 % / 13 % |
| Validation window, block shuffle / IAAFT: share with return ≥ H1's | 55 % / 47 % |
| Forward calibration, years to 30 trades (median, 5–95 %) | 4.5 to 5.3 years (3.8 to 6.4) |
| Forward calibration, share stopped by F3 before 30 trades | 95 % GBM, 98 % GARCH-t, 97 % MRW (no drift); 87 % GARCH-t with the historical mean drift (1,000 ten-year paths each) |
| Forward calibration, share GO | 4.8 % GBM, 1.8 % GARCH-t, 3.2 % MRW (false-GO rates); 12 % GARCH-t with the historical mean drift (pass rate in a rising market without timing skill) |
| Stop hit within 30 days at the window's volatility, analytic / simulated | 8.9 % / 8.0 % |
| Time-average growth per trade, ensemble mean | +2.00 % against +2.83 % (drag 0.83 points) |
| Kelly fraction, capped at 1 | 1.0 (uncapped 2.4); 95 % interval 0.0 to 1.0; 24 % of resamples say do not trade |
| Trials counted, deflated Sharpe probability | 7 trials; expected maximum Sharpe 0.37 annualised; probability 0.74 that H1's 0.78 exceeds it (variance basis: the three counted trials with a recorded Sharpe) |

Reading: BTC/EUR 4h returns show no persistence that DFA can detect beyond
what shuffled returns produce, in any period; permutation entropy sits below
its shuffle range on the train period and on the full history, which is slight
short-range ordinal structure, not a trend. What the returns show clearly is
heavy tails and long-memory volatility. H1's held-out return is matched by 9.5
% of block-shuffled markets and by 16 % of random walks, but a market with the
window's own mean drift and volatility clustering matches its return in 33 %
of trials (return and drawdown together: 11 %); on the validation window the
window's own returns in any order match it more often than not. The forward
protocol's false-GO rate is low (1.8 % to 4.8 % under the no-drift nulls)
because F3 stops almost every path first: with a fixed stake and about six
trades a year, a 31.31 % drawdown from the peak is nearly certain within the
four to six years that 30 trades take (95 % to 98 % of no-drift paths), and
still likely in a market with the historical mean drift (87 %), where only 12
% of paths reach GO. The protocol as predeclared is therefore mostly a
drawdown test. No market with an edge was simulated, so its power is not
measured; the contrast between 12 % with drift and 1.8 % to 4.8 % without is
weak discrimination. Changing F3 is a decision for a new ADR, not for this
one; the figure is recorded so that decision can be made with it. The deflated
Sharpe probability of 0.74 says the held-out Sharpe is not distinguishable
from the best of seven lucky trials, on a variance basis of three recorded
Sharpe ratios. Two caveats: the GARCH fits on the longer windows reach the
persistence cap (near-integrated variance), and GARCH and train-window paths
stop out often (38 % of held-out GARCH-t paths), where the unmodelled
MaxDrawdown protection could have altered trading.

## Consequences

- `make physics` (minutes, no Docker) rebuilds `physics.json`; `make test`
  includes the breakout verification and the drift test; `make desk` reports
  growth and Kelly figures; the demo page gains the section "Market structure
  and null models".
- The record (`record.md`) gains a "Physics-informed assessment" section
  that points here; `docs/status.md` reflects this ADR.
- Not verified in the session that produced this ADR: the demo page was
  built and its pure rendering function checked under node, but not opened in
  a browser.

## Glossary

| Term | Meaning here |
| --- | --- |
| Surrogate | A synthetic series built from the real one that keeps stated properties (distribution, spectrum) and randomises the rest. |
| IAAFT | Iterative amplitude-adjusted Fourier transform: a surrogate with the exact same values and nearly the same power spectrum. |
| GARCH(1,1)-t | A model in which today's variance depends on yesterday's squared return and variance, with Student-t shocks. |
| MRW | Multifractal random walk: returns scaled by a log-normal volatility cascade whose correlations decay logarithmically. |
| Hurst exponent | A measure of persistence of a path: 0.5 memoryless, above 0.5 trending, below 0.5 reverting. |
| False-GO rate | The share of simulated no-edge markets in which the forward protocol would issue GO. |
| Deflated Sharpe ratio | The probability that an observed Sharpe ratio exceeds what the best of N unrelated trials shows by luck. |
| Time-average growth | The mean of ln(1 + r) per trade: what a single compounding account experiences. |
