<img src="pages/assets/favicon.svg" alt="" width="56" height="56">

# Schrödingers Quant

[![CI](https://github.com/sebastianspicker/schrodingers-quant/actions/workflows/ci.yml/badge.svg)](https://github.com/sebastianspicker/schrodingers-quant/actions/workflows/ci.yml)
[![Demo](https://img.shields.io/badge/demo-GitHub%20Pages-1d3f8f)](https://sebastianspicker.github.io/schrodingers-quant/)

The bot is both trading and not trading until you look at the config. Out of
the box, it is **not trading**.

## In short

- **What it is.** A cryptocurrency spot trading bot built on
  [Freqtrade](https://www.freqtrade.io/), set up for Kraken and a small Debian
  VPS, and meant to run unattended around the clock. The repository holds one
  strategy (H1), the research that decided whether to run it, and the
  operations around it: health checks, backups, restore, systemd units and
  runbooks.
- **What it does as shipped.** Nothing with real money. The tracked
  configuration runs in dry-run (simulated orders only), contains no exchange
  credentials, and starts the bot in the `stopped` state. A real order needs
  an operator to add keys, apply the live overlay and send `/start`.
- **What the evidence says.** H1 passed the test written for it before the
  data was run, by a narrow margin and on simulated fills. The verdict is
  *GO, marginal*: enough for forward paper trading and a €10 live execution
  test, not for more capital ([experiment record](research/experiments/H1/record.md)).
- **Where it stands.** Dry-run only. Nothing has been deployed, funded or
  traded live ([status](docs/status.md)).

> [!WARNING]
> This is an experiment, not investment advice. The strategy passed its
> predeclared test only by a narrow margin, on simulated fills. Reliable
> operation and positive returns are separate goals, and neither is promised.

## Screenshot tour

The [interactive demo](https://sebastianspicker.github.io/schrodingers-quant/)
replays the recorded backtests of H1. It runs in your browser from a static
JSON file; there is no bot or exchange behind it.

### 1. Compare H1 with buy-and-hold

H1 buys BTC/EUR when a 4h close breaks the 20-day high and sells when it breaks
the 10-day low. The demo values both H1 and buy-and-hold at every close
(marked to market), fees included, on a fixed €1,000 stake. A strip under the
chart shows when H1 holds a position, and the hatched space after the held-out
run is the forward test that has not happened yet.
[Open the held-out run](https://sebastianspicker.github.io/schrodingers-quant/?period=heldout&cost=base).

![Held-out period: H1 ends at €1,398 against €1,186 for buy-and-hold, with a smaller drawdown](pages/assets/screenshots/heldout.png)

### 2. See where it lags

In a strong bull market, buy-and-hold wins by a wide margin. H1 exists to cut
drawdowns, not to beat the market in every period.
[Open the validation run](https://sebastianspicker.github.io/schrodingers-quant/?period=validation&cost=base).

![Validation period: H1 returns 67 % while buy-and-hold returns 265 %](pages/assets/screenshots/validation.png)

### 3. Stress the costs

Switch to 1.0 % per side to see how much fees and slippage matter. Hover over
the chart, or use the arrow keys, to read the equity on any day.
[Open the stress run](https://sebastianspicker.github.io/schrodingers-quant/?period=heldout&cost=stress).

![Equity chart with a tooltip showing both curves on 15 Nov 2025](pages/assets/screenshots/tooltip.png)

### 4. Check the decision rule

The four pass criteria were written down before the held-out data was run. The
demo shows each result next to the reasons the evidence is still weak.

![Four passed criteria and a list of caveats](pages/assets/screenshots/decision.png)

### 5. See how the bot is wired

Freqtrade is the only component that places orders. Everything else only
checks, records or watches.

![Diagram: Kraken, the Freqtrade container, the trade database, read-only checks, the optional model worker and host timers](pages/assets/screenshots/system.png)

The demo also works on a phone and follows your system's dark mode.

<img src="pages/assets/screenshots/mobile-dark.png" alt="The demo on a phone in dark mode, showing the held-out period" width="300">

## What's in it

- **A tested strategy.** `H1ChannelBreakout` is a long-only channel breakout
  on BTC/EUR 4h candles: it enters on a break of the 20-day high, exits on a
  break of the 10-day low, and has a −20 % catastrophe stop plus Freqtrade's
  protections. It was [predeclared](research/hypotheses/H1.md), checked for
  lookahead bias, and evaluated once on held-out data. The result is GO, but
  only just, and the [experiment record](research/experiments/H1/record.md)
  explains why that is weak evidence.
- **Safe defaults.** Spot only, dry-run, no credentials,
  `initial_state: stopped`. A test fails if the tracked config drifts from
  that, or if project code names an exchange method that creates orders.
- **Operations for one small VPS.** A health check that pings a dead-man's
  switch, consistent SQLite backups with optional restic, a restore that leaves
  the bot stopped, image pruning, hardened systemd units, a
  [Debian 13 runbook](ops/README.md) and a
  [14-day soak checklist](ops/soak-checklist.md).
- **Read-only live tooling.** `make preflight` checks whether a stake can enter
  and still exit at the stop after fees, minimums and precision.
  `make reconcile` compares the trade database with Kraken. Neither can place
  or cancel an order.
- **An optional model filter (Jev).** A worker without exchange credentials can
  approve or reject entry candidates. Exits never wait for it, and a missing
  answer blocks the entry. There is no real model provider yet; see
  [Jev](docs/jev.md).

## Quick start

You need Docker with Compose and [uv](https://docs.astral.sh/uv/).

```sh
git clone https://github.com/sebastianspicker/schrodingers-quant.git
cd schrodingers-quant
uv sync --locked --group dev
make ci        # lint, config validation (base, live and VPS overlays), tests
make up        # dry-run bot; stays "stopped" until started via API or Telegram
make logs
make down
```

`make help` lists every target. Passing checks show that the configuration and
code load and behave as tested. They don't show exchange connectivity, real
fills or profitability.

To run the bot on a server, follow the [Debian 13 runbook](ops/README.md). The
[live pilot runbook](docs/live-pilot.md) covers going live with €10, including
the drills and stop conditions that must come first.

## Adapting it

The strategy and exchange are configuration, not architecture:

- Pairs, stake, exchange and protections live in `config/base.json`. Local and
  secret overlays go in the git-ignored `config/local/`.
- Strategies are plain Freqtrade files in `user_data/strategies/`. They don't
  import project code, so any Freqtrade install can load them.
- Before trusting a new strategy, write a hypothesis in `research/hypotheses/`
  with its rules, periods and pass criteria, then run the held-out period once.
  `make research` (`src/sq/research/`) shows how H1 was evaluated.

The live tooling (`sq.live`) and the research data proxy assume Kraken with EUR
pairs. Other exchanges need changes there and a fresh preflight run.

## Repository layout

| Path | Contents |
| --- | --- |
| `compose.yaml` | The pinned Freqtrade image and every service: bot, Jev worker, tools, research, tests |
| `config/` | Freqtrade config layers: `base.json` (dry-run), `live.json` (explicit live overlay), `examples/`, ignored `local/` |
| `user_data/strategies/` | `H1ChannelBreakout` (frozen), `H1JevShadow` (optional model filter) |
| `src/sq/` | Project package: config validation, read-only live tools, Jev worker, research pipeline and analysis |
| `research/` | Hypotheses, research configs, experiment records, research-only strategies |
| `ops/` | Host side: health ping, backup and restore, retention, systemd units, runbooks |
| `pages/` | The GitHub Pages demo and its screenshots |
| `tests/` | pytest suite, run inside the pinned Freqtrade image |
| `docs/` | [Status](docs/status.md), [architecture](docs/architecture.md), [ADRs](docs/adr/README.md), [operations](docs/operations.md), [live pilot](docs/live-pilot.md), [Jev](docs/jev.md) |

Research, backtests and model work run on a development machine, never on the
VPS.

## Project status

Dry-run only. Nothing has been deployed, funded or traded live. The next step
is a 14-day soak on a VPS, which also serves as H1's forward paper test. See
[status](docs/status.md) for what has been verified and what hasn't.

## Contributing and security

Issues and pull requests are welcome; see [CONTRIBUTING](CONTRIBUTING.md).
Report vulnerabilities privately as described in [SECURITY](SECURITY.md).
Never paste API keys, account exports or trade databases into an issue.

## License

[MIT](LICENSE)

## Glossary

| Term | Meaning here |
| --- | --- |
| Dry-run | Freqtrade's simulation mode: the bot runs its strategy on live market data but sends no orders. |
| `stopped` state | The bot process runs but opens no trades until an operator sends `/start`. |
| Live overlay | `config/live.json`, the config layer that switches off dry-run. It is only applied explicitly. |
| Held-out period | Data (2024-07-01 → 2026-09-20) kept aside and run once, after the strategy was frozen. |
| Marked to market | Valuing an open position at the current price on every close, not only when it is sold. |
| Drawdown | The fall from the highest equity reached so far to a later low, as a percentage. |
| Catastrophe stop | H1's fixed −20 % stop from the entry price, a last-resort exit rather than the normal one. |
| Forward paper trading | Running the strategy in dry-run on data that did not exist when it was chosen. |
| Soak | A 14-day unattended dry-run on the VPS, with drills, before any live money. |
| Dead-man's switch | An external monitor that alerts when the bot's regular health ping stops arriving. |
| Preflight / reconciliation | Read-only checks against Kraken: can a stake enter and exit, and does the trade database match the exchange? |
