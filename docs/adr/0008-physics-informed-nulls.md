# ADR-0008 — Physics-informed null models, a calibrated forward test, and a trials ledger

**Date:** 2026-10-08. **Status:** accepted. Extends [ADR-0006](0006-evidence-standard.md)
(the evidence standard) and [ADR-0007](0007-trader-workbench.md) (the
workstation workbench). H1's rules, trades and verdict are unchanged; the record gains a section.

## In short

- The record gains a third assessment file, `physics.json`, built by
  `make physics` without Docker from a tracked copy of the proxy candles.
  It contains the stylized facts of the market, H1 replayed on surrogate and
  simulated markets that preserve selected constraints, the forward
  protocol's model-conditioned GO rates, a first-passage check of the
  stop, growth and Kelly figures, and a ledger of every evaluation of a rule on recorded data with
  the deflated Sharpe ratio. [docs/physics.md](../physics.md) explains the
  physics and its limits.
- The set of null models was declared in the code and in this ADR's draft
  before the first build and has not changed since. The repository history
  cannot show that order, so the claim rests on the author; what it can show
  is that no null was added or removed after the first build.
- A pure reimplementation of H1's rules (`sq.research.breakout`) is accepted
  as a research tool only because a test shows it reproduces all 41 recorded
  trades to the candle on the tracked candles. It models CooldownPeriod, StoplossGuard
  and ratios-mode MaxDrawdown. The training replay triggers one MaxDrawdown
  lock without changing any historical trade.
  It never runs in the bot.
- Nothing in this ADR is evidence of an edge or a capital decision. The
  held-out window was inspected before these diagnostics existed; on it they
  are descriptive. A future forward GO can be compared with the model-conditioned
  rates, subject to the limitations in the mathematical review below.

## Context

ADR-0006 established that H1's held-out result is not distinguishable from
zero and added two nulls that keep the real price path and vary the strategy
(random timing at equal exposure, constant exposure). What was missing:

1. Nulls that keep the strategy and vary the market: does a market with the
   same tails and volatility clustering but no trends produce H1's result as
   often? Is there any persistence in BTC/EUR 4h returns for a breakout to
   earn from?
2. The forward test's behavior under specified market models. ADR-0006 fixed
   P1 and P2 after 30 trades but did not simulate their pass rates. These
   models do not establish the test's statistical false-positive rate.
3. A multiplicative-growth view of sizing. The fixed-stake record and
   superseded compounding run illustrate different sizing behavior; empirical
   trade averages do not by themselves establish long-run averages.
4. An explicit count of how many times the record was looked at, which the
   deflated Sharpe ratio needs.

The diffusion correspondence in
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
   StoplossGuard lock after two stop exits in 30 days, and default ratios-mode
   MaxDrawdown). MaxDrawdown is an absolute fall greater than 0.25 in cumulative
   closed-trade return ratios over 540 candles, with a 180-candle lock. It is
   tested against the pinned runtime, including signal exits and strict
   lookback/unlock boundaries; reports expose the frequency of these locks. The replay
   is licensed by that test and by nothing else; a change that breaks the
   test invalidates every null built on it.
3. **The predeclared null set** (`sq.research.nulls`), on the held-out,
   validation and train windows at base costs, 1,000 trials each:
   5-day block shuffle; IAAFT surrogate; GBM with zero log drift; GARCH(1,1)-t
   fitted to the window with zero log drift and with the window's sample-mean
   log drift; MRW fitted to the window with zero log drift. Synthetic paths get wicks
   resampled from the real candles and the same warm-up the real run had.
   The reported figures are the shares of trials with return at least H1's,
   drawdown at most H1's, and both. No further null is added to the record
   without a new ADR.
4. **Forward-protocol calibration.** The same fitted models (on the full
   2020–2026 history), 1,000 ten-year paths each; the window ends at the 30th
   closed trade; F3, P1 and P2 as in [forward-test.md](../forward-test.md),
   with buy-and-hold on the forward report's basis. The record carries, per
   model, the share reaching 30 trades, the years to 30 trades, the F3 stop
   share and the GO share. Schema 2 excludes paths breaching F3 from
   completion and eligible P1/P2 shares, and labels unconstrained timing
   separately. Zero log drift is not zero expected price drift, so these
   shares are model-conditioned rather than established false-positive rates.
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

## Mathematical review and implementation correction (2026-10-08)

The initial implementation is preserved in commit `18fde51`. Schemas 2–3 correct
its interpretation and replay without adding models, tuning H1, or changing
any forward threshold. The corrections are verified against the pinned
runtime and all three recorded periods.

- Cooldown and StoplossGuard unlock times now include Freqtrade's rounding to
  the next candle boundary. The guard excludes stops exactly at its lookback
  boundary. Historical trades still reproduce; dedicated synthetic tests
  cover behavior the historical record never exercised. See the pinned
  [PairLocks implementation](https://raw.githubusercontent.com/freqtrade/freqtrade/2026.8/freqtrade/persistence/pairlock_middleware.py)
  and [trade filtering](https://raw.githubusercontent.com/freqtrade/freqtrade/2026.8/freqtrade/persistence/trade_model.py).
- Schema 3 implements the previously omitted MaxDrawdown protection and
  checks it directly against Freqtrade's actual protection in the pinned
  image. It also uses elapsed-time lock and lookback boundaries across gaps.
  The train replay produces one lock following the 2021-06-20 signal exit;
  no historical entry falls inside that lock, so the recorded trades remain
  unchanged. The initial claim that no protection fired was incorrect. The
  frozen H1 rules and thresholds are not modified by these replay corrections.
- Completion and time-to-30 no longer count trades after F3. Separate fields
  retain counterfactual timing ignoring stopping; conditional completion times
  must not be read as an unconditional forecast.
- Zero-log-drift models are labeled correctly. Exponentiated Student-t log
  returns lack finite means; these models provide finite-path scenarios,
  not a proven financial no-edge null or a measured false-positive rate.
- The first-passage report adds a continuous Brownian-bridge estimator and
  Monte Carlo standard errors; the earlier 4h-close estimator is retained
  with its discrete-monitoring label.
- Volatility drag is the Jensen gap in log units. The wider Kelly search is
  explicitly capped at 5; the daily P&L frontier is a synthetic rescaling.
- The ledger cannot estimate a defensible selection adjustment from three
  different-period Sharpes and four missing ones. The former 0.74 deflated
  Sharpe score is withdrawn and the fields are unavailable with reasons.
- Hurst, entropy, IAAFT and the quantum correspondence now carry their actual
  mathematical limits. No maximum-entropy derivation is claimed for the set.

The current figures are generated in [physics.json](../../research/experiments/H1/physics.json)
and summarized in the [record](../../research/experiments/H1/record.md).
F3 stops most fitted-model paths, but that alone does not measure power or
justify relaxing F3. Such a change remains a separate protocol decision.
The complete derivation and linked sources are in [physics.md](../physics.md).

## Consequences

- `make physics` (minutes, no Docker) rebuilds `physics.json`; `make test`
  includes the breakout verification and the drift test; `make desk` reports
  growth and Kelly figures; the demo page gains the section "Market structure
  and null models".
- The record (`record.md`) gains a "Physics-informed assessment" section
  that points here; `docs/status.md` reflects this ADR.
- The schema 3 verification includes 378 unit tests, pinned-runtime protection
  contracts, the full diagnostic rebuild and desktop/mobile browser checks.
  No trading deployment is part of this assessment.

## Glossary

| Term | Meaning here |
| --- | --- |
| Surrogate | A synthetic series built from the real one that keeps stated properties (distribution, spectrum) and randomises the rest. |
| IAAFT | Iterative amplitude-adjusted Fourier transform: a surrogate with the exact same values and nearly the same power spectrum. |
| GARCH(1,1)-t | A model in which today's variance depends on yesterday's squared return and variance, with Student-t shocks. |
| MRW | Multifractal random walk: returns scaled by a log-normal volatility cascade whose correlations decay logarithmically. |
| DFA exponent | A finite-scale scaling diagnostic; near 0.5 does not rule out drift or nonlinear predictability. |
| Model-conditioned GO rate | The share of paths passing under a specified construction; not an established false-positive rate. |
| Deflated Sharpe score | An asymptotic selection-adjusted score requiring comparable trial Sharpes and an independent-trial assumption; unavailable for the current ledger. |
| Time-average growth | The mean of ln(1 + r) per trade: what a single compounding account experiences. |
