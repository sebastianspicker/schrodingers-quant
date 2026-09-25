# Status

**As of 2026-09-25.** For how the system is built, see
[architecture](architecture.md). For why, see the [ADRs](adr/README.md)
(architecture decision records).

## In short

- **Nothing is live.** The system runs in dry-run only. Nothing has been
  deployed, funded or traded live.
- **The strategy passed its test, just.** H1 (a 20/10-day channel breakout on
  BTC/EUR 4h candles) was predeclared, checked for lookahead bias and evaluated
  on held-out data from 2024-07 to 2026-09. With the fixed stake H1 declares, it
  passed all four criteria: +39.8 % net return, and a marked-to-market
  drawdown of 30.4 % against a limit of 31.3 %. The verdict is **GO, marginal**
  ([ADR-0005](adr/0005-h1-go-after-sizing-correction.md)). An earlier run that
  compounded the stake failed the drawdown test; both runs are published.
- **What GO allows.** Forward paper trading and the €10 execution pilot,
  nothing more.
- **What is built.** VPS operations, live-pilot tooling and the Jev shadow mode
  are built and tested locally, not on a real host or exchange account.
- **What is next.** A 14-day soak on a VPS, which doubles as H1's forward paper
  test. It is blocked until a Debian 13 VPS exists.

## What exists

| Area | State |
| --- | --- |
| Runtime | Freqtrade 2026.8, pinned by image digest in `compose.yaml`; config in layers (base → VPS → live → secrets) |
| Strategies | `H1ChannelBreakout` (tracked, dry-run, frozen); `H1JevShadow` (modes off, shadow and filter) |
| Research | One pipeline (`make research ARGS=…`, [ADR-0007](adr/0007-research-pipeline-in-python.md)): Binance proxy data validated against Kraken, data manifest, bias checks, fixed-stake backtests, marked-to-market metrics with provenance, [H1 record](../research/experiments/H1/record.md) |
| Operations | Health ping to a dead-man's switch; backup of every trade database and the Jev records; restore of a named database that leaves the bot stopped; image pruning; hardened systemd units; [Debian 13 runbook](../ops/README.md); soak checklist |
| Live tooling | Read-only preflight (BTC/EUR at €8 and €10 feasible at an assumed 0.40 % fee), read-only reconciliation, credential-free `config/live.json`, secrets validation for deployable overlays |
| Jev | Candidate recording, credential-free worker with a null provider, fail-closed filter, leakage-safe evaluation harness; no real provider ([Jev](jev.md)) |
| Checks | `make ci`: lint, format, shellcheck, Compose variants, three config validations, pytest in the pinned image; the same in GitHub Actions |
| Demo | [GitHub Pages](https://sebastianspicker.github.io/schrodingers-quant/), built from `pages/` and the recorded H1 equity curves (`make research ARGS=equity-curves`, which refuses to write if a curve disagrees with the record) |

## Verified

What has actually been run and checked, newest first:

- **`make ci` exits 0 locally** after the research-pipeline restructure
  ([ADR-0007](adr/0007-research-pipeline-in-python.md)).
- **ADR-0007 differential checks (2026-09-25).** A differential check reruns
  a step and compares its output with the tracked record. `make research` with
  `manifest`, `train`, `validation`, `sensitivity`, `eth-robustness`,
  `benchmark` for all three periods, and `equity-curves` reproduced every
  tracked record's metrics. Only `generated_at`, result zip names and the
  manifest's timestamp-driven hash differed; with the tracked manifest, `train`
  also reproduced its records' provenance. `equity-curves.json` was
  byte-identical. `bias` found no lookahead or recursive bias. `jev-evaluate`
  on the train export reproduced the recorded marked-to-market baseline.
  - The records in the repository are still the 2026-09-24 originals.
  - The held-out window was not rerun. Its hand-made `mtm-heldout-*.json`
    records used the period's last day as an exclusive end, while the other
    records and the pipeline mark through that day. A rerun would report
    +39.84 % instead of +39.81 % net, with the same 30.37 % drawdown, so the
    decision would not change.
- **ADR-0006 differential checks (2026-09-24).** `research/run.sh train`, the
  train benchmark and the data manifest reproduced the recorded H1 results and
  provenance exactly; marked-to-market drawdown on the recorded validation
  result matched. The held-out window was not rerun.
- **Local Docker drills (2026-09-24).** `make up` loads `H1ChannelBreakout`
  with its protections, in state `STOPPED`, with no errors. Both strategies load
  in the bot container without `src/`. A backup taken with the container
  running snapshots both `dry-run.sqlite` and `live.sqlite`. Restoring
  `live.sqlite` by name moves the current file and a stale WAL (SQLite's
  write-ahead log) aside and leaves the bot stopped. The Jev worker records an
  assessment with the null provider. Read-only preflight against public Kraken
  data reports BTC/EUR at €8 as feasible.
- **Earlier:** an H1 dry-run smoke test against live Kraken public data, and
  the local rehearsal of API control, health ping and process-crash restart
  ([rehearsal](../ops/local-rehearsal.md)).

## Not verified

None of the following has been tested yet:

- exchange order acceptance, partial fills and exchange-side stops;
- restart reconciliation with real orders;
- authenticated preflight and reconciliation;
- the assumption about which currency the entry fee is charged in;
- the restic path;
- systemd units and hardening on a real host, and VPS resource use;
- any real model provider;
- future profitability.

## Open work

| Item | State | Needs |
| --- | --- | --- |
| 14-day VPS soak with drills; doubles as H1's forward paper test | Blocked | A Debian 13 VPS ([runbook](../ops/README.md), [soak](../ops/soak-checklist.md)) |
| €10 live pilot drills: forced round trip, restart with an open position, exchange stop after restart, key revocation | Blocked | The maintainer's authorization, a funded isolated Kraken account, a trade-only key; the soak first ([live pilot](live-pilot.md)) |
| Decision on continuing or scaling after the pilot | Open | An ADR; never automatic |
| Jev real provider and shadow assessments on forward data | Blocked | Jev API access and documentation; a spending cap |
| Jev matched evaluation (baseline vs filter) | Harness done | Recorded assessments; a comparison against a deterministic rule before crediting the model |
| Jev live filter | Mechanism done, off | A real provider and a positive matched evaluation |
| Successor hypotheses (H2…) | Open | Predeclared, judged on forward data after 2026-09-24; the 2024-07 → 2026-09 window is spent |
| Race-safe publication of frozen experiment artifacts | Open | Needed only if experiment freezing is automated (the records are currently made by hand) |

### Open decisions

| Decision | Current position |
| --- | --- |
| Start the €10 live pilot | The maintainer's; requires authorization, account and key |
| Legacy live database or state migration | None needed: no live state has ever existed |
| Control surface beyond Telegram and the SSH-tunnelled API | None planned; no dashboard dependency assumed |

### Deferred capabilities

Deferred means in scope, but started only when evidence calls for it; not
dropped ([ADR-0001](adr/0001-evidence-first-restructure.md)).

| Capability | Trigger |
| --- | --- |
| Intent journal, custom execution adapter | A restart or ambiguous-submission drill (soak, pilot) shows a gap in Freqtrade's handling; must meet the [contracts](architecture.md#contracts-for-deferred-execution-work) |
| Host supervisor beyond systemd + Docker restart | The soak shows a failure that the health ping and restart policy miss |
| Forecasting or learning models | A simple baseline has a measured forward edge |
| Full release manifests (beyond the digest pin and CI) | A second deployment target or a release cadence |

### Gates

Gates are the milestones the project passes in order.

| Gate | Meaning | State |
| --- | --- | --- |
| G-0 | Scaffold and checks | Reached |
| G-1 | Integrated software (strategy, ops, live tooling, Jev mechanism) tested locally | Reached |
| G-2 | Reproducible release: committed, CI on a remote | Reached (2026-09-24, first GitHub Actions run green) |
| G-3 | Unattended Debian operation: soak passed | Not reached |
| G-4 | Live execution: pilot drills passed | Not reached |
| G-5 | Economic evidence: positive forward record after costs | Not reached |

## Background

The repository was rebuilt around its current layout before the first commit
(ADR-0006). An earlier implementation was reviewed on 2026-09-22 (164 files,
594 of 619 tests passing on macOS). That review and the retired planning
documents were kept outside the repository; they are not part of this history
and not CI evidence.

## Glossary

| Term | Meaning here |
| --- | --- |
| Dry-run | Freqtrade's simulation mode: the strategy runs on live market data but no orders are sent. |
| Held-out data | The 2024-07-01 → 2026-09-20 window, kept aside until the strategy was frozen and meant to be run once. It is now "spent". |
| Marked to market | Valuing an open position at the current price on every close. |
| Drawdown | The fall from the highest equity reached so far to a later low, as a percentage. |
| Fixed stake / compounded stake | A fixed stake bets the same amount on every trade; compounding reinvests (here about 99 % of) the balance each time. |
| Forward paper trading | Dry-run on data that did not exist when the strategy was chosen. |
| Soak | A 14-day unattended dry-run on the VPS, with recovery drills. |
| Preflight | A read-only check that a stake can enter and still exit at the stop after fees, minimums and precision. |
| Reconciliation | A read-only comparison of the bot's trade database with the exchange's records. |
| Proxy data | Binance prices used in place of Kraken's, checked against Kraken where they overlap. |
| Lookahead bias | A backtest using information that would not have been available at decision time. |
| Null provider | Jev's placeholder model, which always abstains. |
| Fail-closed | When anything goes wrong, the filter blocks the entry instead of allowing it. |
| Dead-man's switch | An external monitor that alerts when the regular health ping stops. |
