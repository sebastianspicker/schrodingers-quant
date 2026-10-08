# ADR-0007: A resource-bounded research and forward-testing workbench

Date: 2026-10-08. Status: accepted for local integration; host soak pending.
Supersedes the forward measurement and operational contracts of ADR-0006;
retains H1's frozen strategy, archived decision and no-capital-increase rule.
No forward deployment existed when this revision was made.

## Decision

The product should help a trader reject weak evidence, understand capital use,
and collect trustworthy forward observations on one small VPS. More indicators,
leverage, model calls, or parameter searches would not address those needs.

1. Add an offline `make desk` command. It consumes the existing record and
   emits Markdown, JSON and monthly CSV: daily risk, time underwater,
   concentration, fixed-stake cash sleeves and optional VPS cost economics.
   No new backtest is run. Recorded 4h drawdowns remain authoritative; derived
   daily drawdowns and retrospective scenarios cannot establish an edge.
2. Archive closed Kraken candles incrementally in a separate SQLite database.
   Reject gaps, conflicting revisions, invalid OHLCV and stale final marks.
   The public API serves only the latest 720 entries and includes a forming
   candle ([Kraken contract](https://docs.kraken.com/api-reference/market-data/get-ohlc-data)).
   A missed interval is a data repair task, never a silently shortened window.
3. Use a consistent read transaction over Freqtrade trades and orders. Actual
   filled timestamps determine exposure; original submission determines the
   entry reference and H2 sizing information set. Unfilled intents are not
   positions. Unsupported multiple fills/partial exits are explicit errors.
   Quantity checks respect stored exchange precision.
4. Report fixed-stake normalized performance separately from actual account
   quantities. Initialize drawdown with starting equity. Exclude database
   events after the last closed candle. Live or explicitly restarted windows
   need declared initial capital to show account returns; no funding flows are
   inferred. F1 incomplete coverage withholds performance judgement.
5. Implement the documented P1/P2 gates and a deterministic trade bootstrap.
   Passing never grants capital authority. H2 uses full pre-window warmup,
   €1,000 reference capital and explicit eligibility. Invalid F1/F2 evidence,
   missing warmup, predeclaration history or two undefined return/drawdown
   ratios cannot yield GO. The 20-trade early evaluation applies only after
   an F3 drawdown stop with valid implementation and execution evidence.
6. Publish reports atomically, serialize daily runs, pin the window identity
   and preserve the first STOP until investigated. A failed run publishes ERROR.
   The host heartbeat checks report freshness and the STOP latch when enabled.
   Backups include the candle archive and report evidence.
7. Bound report containers to 512 MiB and one CPU with one numerical-library
   thread. Research remains on the workstation. No new daemon, web server,
   database service, subscription or production dependency is introduced.

## Corrections to historical diagnostics

The constant-exposure benchmark now includes its terminal fee in drawdown and
uses the run's actual base/stress fee. `statistics.json` is rebuilt from the
unchanged recorded curves. Flat Sharpe bootstrap samples return undefined
intervals rather than crashing. H1 trades, archived 4h results and verdict are
unchanged; the new desk explicitly discloses the reused held-out period.

## Consequences and limits

This is a tool for research discipline and small-scale execution observation,
not a profitable strategy claim. Thirty H1 trades can take years. Candle archive
availability, not API pagination, makes that horizon possible. Missing old
candles need an independently verified import. The reporter supports the pinned
single-position H1 spot contract, not DCA, futures or a portfolio fill ledger.
Candle-close drawdown is not intrabar maximum risk. A STOP alerts the operator;
Freqtrade remains the only order engine and no automation places or cancels orders.
