# ADR-0006 — Evidence standard: uncertainty in the record, predeclared forward criteria, tracked tests

**Date:** 2026-10-08. **Status:** accepted. Amends [ADR-0005](0005-h1-go-after-sizing-correction.md):
the GO verdict stands, its meaning and what follows from it change.

## In short

- The backtest record now carries a statistical assessment
  ([statistics.json](../../research/experiments/H1/statistics.json), built by
  `make stats`). It shows that H1's held-out result is not distinguishable
  from zero, and that its timing is not shown to add value beyond the time it
  spends in the market. GO (marginal) remains the procedural verdict; it is
  not read as evidence of an edge.
- The forward paper test is judged by criteria fixed here, before it starts
  ([forward test protocol](../forward-test.md)), with the same metrics as the
  backtest, produced by a read-only report (`sq.live.forward`).
- No performance judgement on H1 before 30 closed forward trades. At the
  observed trade rate that is about five years. The forward test is therefore,
  first of all, an implementation and execution test.
- The repository tracks its unit tests. Code that decides a verdict without a
  test is not accepted.

## Context

ADR-0005 recorded GO (marginal) because H1 passed four threshold tests. None
of them carried a measure of uncertainty, and the record had no benchmark
other than buy-and-hold at 100 % exposure, which the record itself calls a
weak comparison for a strategy in cash 60 % of the time.

A statistical assessment of the recorded curves and trades (held-out period,
base costs; figures from `statistics.json`) gives:

| Figure | Value |
| --- | --- |
| Mean trade, 14 trades | +2.83 % (s.d. 14.49 %), t = 0.73 |
| 95 % bootstrap interval for the mean trade | −3.2 % to +11.1 % |
| Share of bootstrap resamples with mean ≤ 0 | 24 % |
| Sharpe ratio (daily marks, 365-day annualisation) | 0.78, 95 % block-bootstrap interval −0.79 to 2.12 |
| Random timing at equal exposure (5,000 trials): share with return ≥ H1's | 15 % |
| Random timing at equal exposure: share with a drawdown ≤ H1's (daily marks) | 36 % |
| Constant 38.5 % exposure to BTC, daily rebalanced | +12.8 % return, 23.0 % drawdown (H1: +39.8 %, 30.4 %) |
| Trades needed for t = 2 at this mean and dispersion | 105, about 17 years at 6.3 trades a year |

On the validation period the picture is weaker still: random timing at equal
exposure did at least as well as H1 in 54 % of trials, and constant 54 %
exposure returned +111 % against H1's +67 % with half the drawdown.

Two further gaps: the repository had no tests, so the functions behind K1–K4
were unchecked, and the forward test had no pass or stop conditions, which
would have left its interpretation to hindsight.

## Decision

1. **GO (marginal) stands, reinterpreted.** It permits forward paper trading
   and the €10 execution pilot, as before. It is not evidence that H1 has an
   edge: the mean trade's interval includes zero, and the strategy's return
   is not shown to exceed what random timing at the same exposure produces.
   What the backtest supports is narrower still than H1.md's claim of
   drawdown control: H1's drawdown is smaller than buy-and-hold's at 100 %
   exposure, and most of that difference is exposure, not timing. Constant
   38.5 % exposure had a smaller drawdown than H1 (23.0 % against 30.4 %), and
   36 % of random timings at equal exposure did too (both on daily marks).
   No capital decision rests on the backtest.
2. **Statistical assessment is part of every experiment record.** `make stats`
   rebuilds `statistics.json` deterministically from `equity-curves.json`
   (fixed seed, legacy numpy RNG). A test fails if the tracked file drifts from
   a fresh build. The demo page shows the assessment next to the headline
   figures.
3. **Forward criteria F1–F4 are fixed in [forward-test.md](../forward-test.md)**
   before the soak starts. `sq.live.forward` produces them from the trade
   database and public candles, read-only, on the same marked-to-market basis
   as the record, and exits non-zero on a STOP so the host timer alerts.
   Performance criteria (P1, P2, mirroring K1, K2 on forward data) apply only
   once F4 (30 closed trades) is met.
4. **Tests are tracked** (`tests/`, previously ignored by `.gitignore`), run by
   `make check` and CI, without Docker. Pure logic is separated from Freqtrade
   and ccxt by lazy imports so it is testable on a development machine. The
   strategies are tested through a minimal `IStrategy` stand-in when
   Freqtrade is absent; the frozen H1 file hash is asserted.
5. **Code must run on the image's Python.** `except A, B:` without
   parentheses (Python 3.14 only) was present in the Jev protocol and the Jev
   strategy and is replaced by the portable form. Ruff's target version is
   lowered to 3.13 (`pyproject.toml`), because with 3.14 the formatter strips
   the parentheses again. Changing `H1JevShadow.py` changes the source hash in
   Jev's `candidate_id`; no candidates have been recorded, so nothing is
   invalidated.
6. **A successor hypothesis is predeclared** ([H2](../../research/hypotheses/H2.md)):
   H1's signals with volatility-targeted sizing, judged on forward data only,
   paired against H1 on the same trades. It does not run on the VPS; it is
   evaluated offline from H1's forward trades.

## Consequences

- `research/experiments/H1/record.md` gains a "Statistical assessment"
  section; `docs/status.md` reflects this ADR.
- `make check` includes `make test`; CI runs the tests on every push.
- The soak checklist and the VPS runbook gain the daily forward report
  (`ops/forward_report.sh`, `sq-forward-report.timer`), whose STOP exit
  reaches the dead-man's switch through the existing `OnFailure=` hook.
- The live pilot's stop conditions include F1–F3 failing.
- What could not be verified in the session that produced this ADR: anything
  that needs the Freqtrade image (strategy loading after the `except` fix,
  `sq.live.forward` end to end against a real database, the H2 strategy in
  a backtest). `docs/status.md` lists these as unverified until a Docker run
  confirms them.

## Glossary

| Term | Meaning here |
| --- | --- |
| Bootstrap interval | A range for a statistic obtained by resampling the observed trades or days many times; a 95 % interval that includes zero means the data cannot rule out a zero mean. |
| Block bootstrap | A bootstrap that resamples runs of consecutive days, keeping trends and volatility clusters intact. |
| Random timing at equal exposure | Placing the strategy's own trade durations at random in the period; a benchmark for whether *when* it was long mattered. |
| Constant exposure | Holding a fixed fraction of the notional in the asset every day; a passive portfolio with the same market participation. |
| Power (trades needed) | The sample size at which a mean trade of the observed size would reach t = 2. |
| F1–F4, P1–P2 | The forward test's predeclared implementation and performance criteria, see [forward-test.md](../forward-test.md). |
