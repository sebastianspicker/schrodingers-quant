# ADR-0002 — Binance OHLCV as research price proxy

**Date:** 2026-09-24. **Status:** accepted.

## Context
Freqtrade reports `ohlcv_has_history: False` for Kraken; history requires
`download-data --dl-trades`, which pages through every trade and takes many
hours per pair-year. Kraken's public OHLC endpoint returns only the latest 720
candles.

## Decision
Backtest on Binance BTC/EUR and ETH/EUR OHLCV (available from 2020-01). Apply
Kraken-level costs. Validate the proxy on the overlapping recent Kraken candles
(return correlation and close-price deviation, recorded in the experiment
record). Live trading remains on Kraken.

## Consequences
Backtests measure the signal on a closely related price series, not Kraken
fills. Venue-specific effects (spread, depth, outages) are left to forward paper
testing and the live pilot. If the proxy check fails, stop and build Kraken
history from trades instead.
