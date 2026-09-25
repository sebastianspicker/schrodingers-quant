# ADR-0005 — H1 is GO (marginal) after correcting the backtest sizing

**Date:** 2026-09-24. **Status:** accepted. Supersedes [ADR-0004](0004-h1-no-go.md).

> **Reworded 2026-09-25 for readability, at the maintainer's request.** The
> decision and its reasons are unchanged. One figure is written more precisely:
> the stress-cost return, previously "+25.6 %", is given as the recorded
> 25.55 %. The original wording is in `git show b753551:docs/adr/0005-h1-go-after-sizing-correction.md`.

## In short

H1 may move on to forward paper trading and a €10 live execution test. It may
not receive more capital. It passed all four of its predeclared criteria, but
the hardest one by less than one percentage point, and only after a sizing
error in the first run was corrected.

## Context

[ADR-0004](0004-h1-no-go.md) recorded NO-GO from backtests that compounded
about 99 % of the balance into every trade. H1.md declares a fixed stake. An
independent review found the mismatch. Every period was re-run with a fixed
stake, and the metrics were measured against that fixed amount (the notional).
The strategy, periods, costs and decision rule were unchanged. Details:
[experiment record, Sizing correction](../../research/experiments/H1/record.md#sizing-correction-disclosed).

## Decision

**GO, marginal.**

| Figure (held-out period unless stated) | H1 | Buy-and-hold |
| --- | --- | --- |
| Net return, base costs (0.5 % per side) | +39.8 % | +17.6 % |
| Net return, stress costs (1.0 % per side) | +25.55 % | — |
| Max drawdown, marked to market | 30.4 % | 52.2 % |
| K2 limit (0.6 × buy-and-hold's drawdown) | 31.3 % | |
| Validation net return, base costs | +67.2 % | |

Source: [experiment record, Decision](../../research/experiments/H1/record.md#decision-k1k4-h1md).

## Why this is weak evidence

- The held-out window was evaluated twice. The first, non-conformant run
  failed K2.
- K2 passes by under one percentage point. The drawdown percentage also
  depends on how much idle cash sits beside the stake, which H1.md left
  unspecified; the most conservative reading (no idle cash) was used.
- 14 held-out trades with a 35.7 % win rate (5 winners): the returns come from
  a few large trends.
- The author knew crypto price history when choosing the strategy family.

## Consequences

- `H1ChannelBreakout` becomes the tracked dry-run strategy (`config/base.json`,
  4h, bot state `stopped`). Forward paper trading on data after 2026-09-24 is
  the next evidence; the 14-day VPS soak doubles as that forward test.
- The €10 execution pilot is permitted by H1.md but still requires the
  maintainer's explicit authorization, a funded isolated account and a
  trade-only key.
- No capital beyond the pilot: that needs a forward record, per H1.md.
- Jev work is no longer gated by the strategy; it still needs access to a model
  provider.
- Research configs use a fixed stake from now on. `mtm_drawdown.py` (since
  [ADR-0007](0007-research-pipeline-in-python.md), `sq.research.metrics.mtm_report`)
  supplies the decision metrics.

## Glossary

| Term | Meaning here |
| --- | --- |
| GO, marginal | Passed all criteria, but by a narrow margin; allows forward paper trading and the €10 pilot only. |
| K1–K4 | The four predeclared pass criteria in [H1.md](../../research/hypotheses/H1.md#decision-rule-evaluated-on-btceur). |
| Held-out window | 2024-07-01 → 2026-09-20, kept aside and meant to be run once. |
| Compounding | Reinvesting nearly all of the balance in each trade, instead of a fixed stake. |
| Drawdown, marked to market | The largest fall from a previous high, with open positions valued at every close. |
| Soak | A 14-day unattended dry-run on the VPS, with drills. |
| Trade-only key | An exchange API key that can query and trade but not withdraw. |
