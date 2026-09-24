# ADR-0005 — H1 is GO (marginal) after correcting the backtest sizing

**Date:** 2026-09-24. **Status:** accepted. Supersedes [ADR-0004](0004-h1-no-go.md).

## Context
ADR-0004 recorded NO-GO from backtests that compounded ~99 % of the balance
per trade. H1.md declares a fixed stake. An independent review found the
mismatch; every period was re-run with a fixed stake and metrics measured
against that notional. Strategy, periods, costs and decision rule were unchanged.
Details: [experiment record](../../research/experiments/H1/record.md).

## Decision
GO, marginal. Held-out: net return +39.8 % (base costs) and +25.6 % (stress);
marked-to-market drawdown 30.4 % against a K2 limit of 31.3 %; validation
+67.2 %. Buy-and-hold over the held-out window: +17.6 %, drawdown 52.2 %.

## Why this is weak evidence
- The held-out window was evaluated twice; the first, non-conformant run failed K2.
- K2 passes by under one percentage point, and the drawdown percentage depends
  on how much idle cash sits beside the stake, which H1.md left unspecified
  (the most conservative reading was used).
- 14 held-out trades, 36 % win rate: returns come from a few large trends.
- The author knew crypto price history when choosing the strategy family.

## Consequences
- `H1ChannelBreakout` becomes the tracked dry-run strategy (`config/base.json`,
  4h, bot state `stopped`). Forward paper trading on data after 2026-09-24 is
  the next evidence; the 14-day VPS soak doubles as that forward test.
- The €10 execution pilot is permitted by H1.md but still requires the maintainer's
  explicit authorization, a funded isolated account and a trade-only key.
- No capital beyond the pilot: that needs a forward record, per H1.md.
- Jev work is no longer gated by the strategy; it still needs access to a model provider.
- Research configs use a fixed stake from now on; `mtm_drawdown.py` supplies
  decision metrics.
