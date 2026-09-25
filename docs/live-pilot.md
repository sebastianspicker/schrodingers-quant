# €10 live execution pilot: runbook

Status: tooling built, **live trading not authorized**. This is the runbook for
the live pilot (open work in [status](status.md)) once the maintainer
explicitly authorizes it. Nothing in this repository places, cancels or edits
an order: `sq.live.preflight` and `sq.live.reconcile` are read-only, and
`config/live.json` starts `stopped`.

## In short

- **Purpose.** The pilot tests real execution on Kraken (orders, fills,
  exchange-side stops, restarts, key revocation) with a stake of €8 on about
  €10 of capital. It is not evidence of profitability.
- **Order of work.** Meet every prerequisite, layer the live config, run the
  four drills once each, reconcile at least daily, and record everything.
  Stop on any of the stop conditions.
- **Worst case per trade, as measured so far.** With a −20 % stop and an
  assumed 0.40 % fee on both sides, public market data on 2026-09-24 put the
  worst-case loss at about €1.65 for an €8 stake and €2.06 for a €10 stake.
  That check used no account, so it does not confirm the real fee tier,
  balance or order handling ([Preflight evidence](#7-preflight-evidence)).
- **Scaling.** Continuing or enlarging the pilot is a written decision (an
  ADR), never automatic.

## 1. Prerequisites, before any credential exists

- Explicit authorization from the maintainer to fund and run a live pilot.
- An isolated Kraken account or subaccount, funded with roughly €10–15. The
  small margin above the €10 stake covers Kraken's cost minimum and fees on
  both legs (see [Preflight evidence](#7-preflight-evidence)).
- An API key scoped to **query and trade permissions only**. No withdrawal
  permission. Restrict it to the VPS's IP address if Kraken's key settings
  allow it.
- The H1 decision (`research/hypotheses/H1.md`,
  [ADR-0005](adr/0005-h1-go-after-sizing-correction.md)) has reached a "go".
- The unattended dry-run soak on the VPS has passed
  ([soak checklist](../ops/soak-checklist.md)): control, health, backups and
  recovery drills rehearsed.
- `make preflight` (`sq.live.preflight`) exits 0 (feasible) for the pair, stake
  and stoploss actually configured, run against the live account, so it also
  reads the account's real fee tier and EUR balance, not just market defaults.
- Preflight assumes Kraken's default convention: the entry (buy) fee is charged
  in the base currency, BTC (see `assess_feasibility`'s docstring). Confirm this
  against the first real fill, by checking the fee currency on the filled
  order, before trusting later feasibility runs or reconciliation.

Credentials are never committed. They belong only in an ignored
`config/local/secrets.json` (see `compose.override.example.yaml`) or the
equivalent path on the VPS.

## 2. Config layering on the VPS

The config is built from layers, in this order; later layers override earlier
ones:

1. `config/base.json`: tracked, credential-free spot dry-run defaults.
2. `config/local/vps.json`: the VPS overlay (Telegram, API server), copied from
   `config/examples/vps-dryrun.example.json`.
3. `config/live.json`: the tracked, credential-free live overlay. It sets
   `dry_run: false`, `initial_state: stopped`, the live `db_url`
   (`live.sqlite`), `available_capital: 10`, `stake_amount: 8`,
   `max_open_trades: 1`, `order_types.stoploss_on_exchange: true` and
   `force_entry_enable: false`.

   With `stake_amount: 8` and `available_capital: 10`, after roughly one full
   stop-out the remaining balance can fall below the €8 stake. Freqtrade then
   stops opening new trades on its own (insufficient stake), with no automatic
   top-up. This is an intended hard cap on the pilot's size, not a bug: see
   `AGENTS.md` on automatic capital increases.
4. `config/local/secrets.json` (ignored): the exchange `key` and `secret`, and
   the Telegram and API credentials.

`config/live.json` is **only ever applied explicitly** as one of these layers.
It is never part of the default `make validate` / `make up` path (enforced by
`tests/live/test_live_overlay.py`). Check the merged layers first:

```sh
docker compose run --rm tools python -m sq.config \
  --config /freqtrade/config/base.json --config /freqtrade/config/local/vps.json \
  --config /freqtrade/config/live.json --config /freqtrade/config/local/secrets.json
```

To go live, edit the ignored `compose.vps.yaml` on the VPS so that its
`command` list inserts `--config /freqtrade/config/live.json` before the
secrets layer. Then run `docker compose up -d freqtrade` (the supervised
service, with its restart policy, not a foreground `docker compose run`). The
bot starts `stopped`; the operator sends `/start` only after preflight passes.
To return to dry-run, remove the line and run `up -d` again.

With the live overlay, the trade database is `user_data/runtime/live.sqlite`.
Backups snapshot it next to `dry-run.sqlite`, and a restore must then name it:
`ops/restore.sh --local <backup-dir> --db live.sqlite`.

## 3. Drill checklist

Run each drill once, on the funded live account, with a full evidence record
(commands, timestamps, screenshots or log excerpts) filed under
`research/experiments/` or in an ADR. Do not repeat a drill "until it works"
without recording the failures too.

1. **Forced round trip.** Temporarily set `force_entry_enable: true` in a
   local, uncommitted overlay (never in `config/live.json`). Issue one forced
   entry with the Telegram or API `forceentry` command, let it fill, then let it
   exit (signal, ROI, or a manual `forceexit`). Set `force_entry_enable` back to
   `false` and confirm no further forced entries are accepted.
2. **Restart with an open position.** With a position open, restart the
   container (`docker compose restart freqtrade` or equivalent). Verify that
   Freqtrade reconciles the open trade from the database, and that the stoploss
   order still exists on Kraken after the restart (check in the Kraken UI or
   with `make reconcile`).
3. **Kill during an open entry order.** Send `kill -9` to the Freqtrade process
   (or force-kill the container) while an entry order is unfilled or partially
   filled. Restart, then run `make reconcile` and confirm it reports a match, or
   a specific, expected mismatch that Freqtrade's own restart reconciliation
   should already have resolved.
4. **Key revocation.** Revoke the API key on Kraken while the bot is running.
   Confirm the bot reports clear errors (logs, and Telegram or the health
   endpoint if enabled) without corrupting the trade database: no silent state
   loss, and no crash loop that hides the failure.

## 4. Reconciliation schedule

Run `make reconcile` (`sq.live.reconcile`) at least once a day while the live
pilot is active (for example from a VPS cron entry or a systemd timer), and
immediately after each drill above. It is read-only: it never calls a ccxt
method that changes orders. It exits 0 on a clean match, 3 on any mismatch,
and 1 on an error (for example missing credentials or an unreachable exchange).

```sh
make reconcile   # base + live + config/local/secrets.json, in the tools container
```

For each run, record the timestamp, the exit code, the full JSON report and,
on a mismatch, the remediation taken before the next entry is allowed.

## 5. Evidence to record throughout the pilot

- Every preflight run (JSON report, exit code, date).
- Every reconciliation run (JSON report, exit code, date).
- Each drill's timeline and outcome.
- Daily or per trade: entry and exit fills, fees paid, realized profit and
  loss, drawdown.
- Any manual intervention (forced entry or exit, key rotation, restart), with
  the reason.

## 6. Stop conditions

Stop the pilot (set `force_entry_enable: false` if it is not already, then
send `/stopentry` or stop the container), and do not resume without the
maintainer's explicit review, if any of the following occurs:

- `make preflight` or `make reconcile` reports infeasibility or a mismatch that
  is not immediately understood and resolved.
- A drill in the checklist above fails (stop order missing after a restart,
  database corruption, errors swallowed silently).
- Realized losses approach the loss budget for the funded capital, agreed with
  the maintainer before the pilot started.
- The Kraken account, API or key state changes unexpectedly (for example a
  permissions change, or a balance change not explained by the bot's own
  trades).
- Any doubt about whether the bot's live orders match its logged or database
  state.

Continuing or scaling the pilot past its initial scope is a decision record
(ADR), never an automatic action (`AGENTS.md`: no automatic capital
increases).

## 7. Preflight evidence

Recorded 2026-09-24 from public Kraken market data (`load_markets`,
`fetch_order_book`), without credentials, against the pinned image
(`freqtradeorg/freqtrade:2026.8@sha256:4d23160b501d2b34579e76f57ad75edfa274967cd0dd824ff1c1b86d8c166ab4`),
using the then-current `scripts/live/preflight.py` (now `sq.live.preflight`,
same code). Rerun with
`make preflight ARGS="--pair BTC/EUR --stake <8|10> --stoploss -0.20"`.

Both runs report `"feasible": true, "reasons": []` and exit code `0`.

| | Stake €8 | Stake €10 |
| --- | --- | --- |
| BTC/EUR price used | €74,221.3 | €74,221.5 |
| BTC bought | 0.00010778 | 0.00013473 |
| BTC left to sell after the entry fee | 0.00010734 | 0.00013419 |
| Stop price (−20 %) | €59,377.04 | €59,377.20 |
| Value at the stop | €6.37 | €7.97 |
| Worst-case loss (fees + stop move + rounding dust) | €1.65 | €2.06 |
| Kraken minimums | 0.00005 BTC and €0.45 | 0.00005 BTC and €0.45 |

Without credentials, preflight assumes Kraken Pro's base-tier taker fee of
0.40 % (ccxt's bundled default of 0.26 % is outdated), labeled in
`fee_source`. The real account fee tier can only be read once a live-scoped key
exists, and preflight must be re-run against it before the pilot starts. No
account balance was checked (no credentials): `"eur_balance": null`.

<details>
<summary>Full report, stake €8, stoploss −20 %</summary>

```json
{
  "pair": "BTC/EUR",
  "stake_eur": 8.0,
  "stoploss": -0.2,
  "price": 74221.3,
  "bid": 74221.2,
  "ask": 74221.3,
  "spread_bps": 0.013473230376182128,
  "amount_min": 5e-05,
  "cost_min": 0.45,
  "amount_precision": 1e-08,
  "price_precision": 0.1,
  "precision_mode": 4,
  "taker_fee": 0.004,
  "fee_source": "conservative base-tier assumption 0.004 (no credentials; ccxt market default 0.0026)",
  "eur_balance": null,
  "eur_balance_source": "not read (no credentials)",
  "stoploss_on_exchange_supported": true,
  "entry_amount_requested": 0.00010778577039205726,
  "entry_amount": 0.00010778,
  "entry_cost_eur": 7.999571714,
  "stake_dust_eur": 0.00042828599999999994,
  "entry_fee_base": 4.3112e-07,
  "amount_after_entry_fee": 0.00010734888,
  "sellable_amount": 0.00010734,
  "dust_base": 8.879999999997787e-09,
  "stop_price": 59377.04000000001,
  "exit_value_at_stop_eur": 6.373531473600001,
  "exit_fee_quote_eur": 0.025494125894400005,
  "max_loss_eur": 1.651534366294399,
  "feasible": true,
  "reasons": []
}
```

Exit code: `0`.

</details>

<details>
<summary>Full report, stake €10, stoploss −20 %</summary>

```json
{
  "pair": "BTC/EUR",
  "stake_eur": 10.0,
  "stoploss": -0.2,
  "price": 74221.5,
  "bid": 74221.4,
  "ask": 74221.5,
  "spread_bps": 0.013473194070692605,
  "amount_min": 5e-05,
  "cost_min": 0.45,
  "amount_precision": 1e-08,
  "price_precision": 0.1,
  "precision_mode": 4,
  "taker_fee": 0.004,
  "fee_source": "conservative base-tier assumption 0.004 (no credentials; ccxt market default 0.0026)",
  "eur_balance": null,
  "eur_balance_source": "not read (no credentials)",
  "stoploss_on_exchange_supported": true,
  "entry_amount_requested": 0.00013473184993566554,
  "entry_amount": 0.00013473,
  "entry_cost_eur": 9.999862695000001,
  "stake_dust_eur": 0.0001373049999990883,
  "entry_fee_base": 5.389200000000001e-07,
  "amount_after_entry_fee": 0.00013419108,
  "sellable_amount": 0.00013419,
  "dust_base": 1.0800000000213417e-09,
  "stop_price": 59377.200000000004,
  "exit_value_at_stop_eur": 7.967826468,
  "exit_fee_quote_eur": 0.031871305872,
  "max_loss_eur": 2.063907532872001,
  "feasible": true,
  "reasons": []
}
```

Exit code: `0`.

</details>

### Reading this evidence

At an assumed 0.40 % taker fee on both legs and a −20 % stoploss, both an €8
and a €10 stake on BTC/EUR clear Kraken's amount (5e-05 BTC) and cost (€0.45)
minimums on both the entry and a full stop-out. The worst-case loss (fees, the
stoploss move and rounding dust) is about €1.65 for an €8 stake or €2.06 for a
€10 stake, at the prices observed. This is feasibility on public market data
only. It does not validate the account's real fee tier, real balance, order
acceptance, partial fills, or exchange-side stoploss behavior. Those require an
authenticated preflight run and the drill checklist once a live-scoped key
exists.

## Glossary

| Term | Meaning here |
| --- | --- |
| Pilot | The first live run with real money, capped at about €10, to test execution. |
| Overlay | A config file layered on top of `config/base.json`; later layers override earlier ones. |
| Stake | The amount put into one trade (€8 in the live overlay). |
| Stop-out / stoploss | An exit triggered when the price falls to the stop (−20 % from entry). |
| Exchange-side stop (`stoploss_on_exchange`) | A stop order held by Kraken itself, so it still works if the bot is down. |
| Forced entry / exit | A trade opened or closed by an operator command instead of a strategy signal. |
| Preflight | A read-only check that a stake can enter and still exit at the stop after fees, minimums and precision. |
| Reconciliation | A read-only comparison of the bot's trade database with the exchange. |
| Base currency | The asset being bought, here BTC; the quote currency is EUR. |
| Taker fee | The fee for an order that fills immediately against the order book. |
| Dust | A tiny leftover amount that is too small to sell because of precision limits. |
| Precision | The smallest step an exchange allows for an amount or price. |
| ccxt | The library Freqtrade uses to talk to exchanges. |
| Soak | The 14-day unattended dry-run on the VPS that must pass first. |
| ADR | Architecture decision record: a dated, written decision in `docs/adr/`. |
