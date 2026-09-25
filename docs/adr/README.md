# Architecture decision records

One file per decision: context, decision, consequences. Superseded records stay
and link to their replacement.

| ADR | Decision |
| --- | --- |
| [0001](0001-evidence-first-restructure.md) | Evidence-first vertical slices; Freqtrade built-ins before custom code |
| [0002](0002-research-data-proxy.md) | Binance OHLCV as research price proxy for Kraken |
| [0003](0003-operational-defaults.md) | Soak length, alerting, backups, handoff file, CI |
| [0004](0004-h1-no-go.md) | *Superseded by 0005.* H1 NO-GO on compounding backtests |
| [0005](0005-h1-go-after-sizing-correction.md) | H1 GO (marginal) under the declared fixed stake; forward paper trading next |
| [0006](0006-one-package-one-runtime-definition.md) | One `sq` package, `compose.yaml` as the single runtime definition, `ops/` for the host; planning docs retired |
| [0007](0007-research-pipeline-in-python.md) | The research pipeline is one Python CLI with one experiment definition; `sq.jev` is only the isolated worker |
