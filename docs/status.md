# Status

**As of 2026-10-08.** For how the system is built, see
[architecture](architecture.md). The H1 research decision is recorded in
[ADR-0005](adr/0005-h1-go-after-sizing-correction.md).

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
- **The record now carries a statistical assessment.** On held-out data the
  mean trade's 95 % interval (−3.2 % to +11.1 %) includes zero, and H1's
  timing is not shown to beat random timing at the same exposure; no
  performance judgement is made before 30 forward trades
  ([ADR-0006](adr/0006-evidence-standard.md)). The forward test has
  predeclared criteria ([forward test](forward-test.md)). None of this is
  deployed yet.
- **The record carries a physics-informed assessment.** BTC/EUR 4h returns
  show no persistence that DFA can detect beyond chance (Hurst exponent inside
  its shuffle range in every period); a random market with the window's mean
  drift and volatility clustering matches H1's held-out return in
  33 % of trials; the forward protocol's false-GO rate is 1.8 % to 4.8 %
  without drift, but F3 stops 95 % to 98 % of no-drift paths and 87 % of
  drifted paths before the 30th trade ([ADR-0008](adr/0008-physics-informed-nulls.md),
  [physics](physics.md)). Whether F3 should be revised is an open decision.
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
| Research | One pipeline (`make research ARGS=…`): Binance proxy data validated against Kraken, data manifest, bias checks, fixed-stake backtests, marked-to-market metrics with provenance, [H1 record](../research/experiments/H1/record.md); a statistical assessment of the record (`statistics.json`, rebuilt by `make stats`, no Docker) |
| Physics | `make physics` (no Docker, about 8 minutes): stylized facts, surrogate and fitted-model nulls, forward-protocol calibration, first-passage check, growth/Kelly, trials ledger (`research/ledger.json`); `physics.json` drift-tested by prefix hashes; a pure H1 replay verified against all 41 recorded trades (`tests/test_breakout.py`) |
| Hypotheses | H1 (GO marginal, frozen); [H2](../research/hypotheses/H2.md) (predeclared 2026-10-08, not run) |
| Operations | Health ping to a dead-man's switch; backup of every trade database and the Jev records (plaintext secret staging is removed even when restic fails); restore of a named database that leaves the bot stopped; image pruning; hardened systemd units, including a daily forward report timer (04:15 UTC, after the backup); [Debian 13 runbook](../ops/README.md); soak checklist |
| Live tooling | Read-only public preflight plus an account-required live-pilot target; bounded account-wide reconciliation that fails on incomplete history; a read-only forward report (`sq.live.forward`, `make forward-report`) that checks the [forward test](forward-test.md) criteria; credential-free `config/live.json`; secrets validation for deployable overlays |
| Jev | Candidate recording, credential-free worker with a bounded main-loop deadline and null provider, fail-closed filter, leakage-safe evaluation harness; no real provider ([Jev](jev.md)) |
| Checks | `make ci`: unit tests (`make test`, no Docker), lint, format, shellcheck, Compose variants and three config validations; the same in GitHub Actions |
| Demo | [GitHub Pages](https://sebastianspicker.github.io/schrodingers-quant/), built from `pages/` and the recorded H1 equity curves (`make research ARGS=equity-curves`, which refuses to write if a curve disagrees with the record) |

## Physics-informed assessment (2026-10-08)

[ADR-0008](adr/0008-physics-informed-nulls.md) adds a tracked copy of the
proxy candles, a verified pure replay of H1, the predeclared null set, the
forward-protocol calibration, growth and Kelly figures, the research-trials
ledger and the demo section "Market structure and null models". The desk
reads its growth block from `physics.json` and stays standard-library only.
Verified locally: `uv run --locked pytest` (all tests, including the breakout
reproduction and the physics drift test), ruff, shellcheck, `sh pages/build.sh`,
and the page's pure renderer under node against the real `physics.json`. Not
verified: the page in a browser; nothing was deployed.

## Workbench revision (2026-10-08)

[ADR-0007](adr/0007-trader-workbench.md) adds the offline trader desk, durable
Kraken candle history, actual-fill accounting, initial-capital drawdown,
separate account and reference returns, P1/P2 and H2 eligibility gates,
window fingerprints, persistent STOP monitoring and bounded report resources.
The historical H1 strategy, fills and 4h decision record remain unchanged.
`statistics.json` corrects constant-exposure fee handling. New tests exercise
malformed/gapped/revised market data, archive rollback, actual fill timestamps,
as-of cutoffs, first-candle losses, evidence eligibility, report failure and
incident persistence. Codex completed the requested follow-up review without
Claude consultation; the maintainer authorized local integration. A real VPS
soak remains pending.

## Verified

- **Workbench v2 (2026-10-08).** Expanded local unit suite, Ruff, shellcheck,
  Compose variants and base/live/VPS configuration loads pass. OrbStack was
  started and the pinned-image integration check passes against the actual
  Freqtrade ORM: filled snapshot, forward CLI, H2 resolver and sizing callback.
  Desk artifacts and the static Pages build were generated locally. No exchange
  orders, new historical backtests, remote publication or VPS deployment occurred.
  Follow-up fixes prevent incomplete startup coverage from qualifying performance
  evidence and prevent a missing window start from bypassing an existing manifest.
  `make ci` passes with 292 tests, including these regressions and the pinned-image
  integration check.

What has actually been run and checked, newest first:

- **Evidence standard (2026-10-08, local, no Docker).** `uv run --locked
  pytest` passes; ruff passes; `make stats` rebuilt `statistics.json` and
  reproduced the record's figures (t = 0.73, K2 margin 0.94 points, exposure
  38.45 %); `sh pages/build.sh` passes; shellcheck passes. Nothing was
  deployed.
  - **Not verified (no Docker in that session):** strategy loading in the
    pinned image after the `except A, B:` fix in `sq.jev.protocol` and
    `H1JevShadow`; `sq.live.forward` end to end against a real trade
    database; H2 backtests; the new systemd units.
- **Safety review hardening (2026-09-27).** Pre-publication verification
  confirmed that failed restic pushes remove plaintext `config/local` staging,
  account-wide reconciliation handles bounded pagination and missing base
  balances, and live preflight refuses to pass without authenticated fee and
  balance reads. A direct pinned-image probe confirmed that a 50 ms Jev timeout
  returns in about 55 ms rather than waiting for the 300–500 ms provider call.
- **Research-pipeline differential checks (2026-09-25).** A differential
  check reruns a step and compares its output with the tracked record. `make research` with
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
- **Earlier-pipeline differential checks (2026-09-24).** The train run, train
  benchmark and data manifest reproduced the recorded H1 results and provenance
  exactly; marked-to-market drawdown on the recorded validation result matched.
  The held-out window was not rerun.
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
| Decision on continuing or scaling after the pilot | Open | An explicit maintainer decision; never automatic |
| Jev real provider and shadow assessments on forward data | Blocked | Jev API access and documentation; a spending cap |
| Jev matched evaluation (baseline vs filter) | Harness done | Recorded assessments; a comparison against a deterministic rule before crediting the model |
| Jev live filter | Mechanism done, off | A real provider and a positive matched evaluation |
| Successor hypotheses (H2…) | H2 predeclared (2026-10-08), not run | Judged on forward data after 2026-10-08, paired against H1 in the forward report; the 2024-07 → 2026-09 window is spent |
| Race-safe publication of frozen experiment artifacts | Open | Needed only if experiment freezing is automated (the records are currently made by hand) |
| Demo benchmark curve/headline alignment | Open | Preserve the frozen benchmark records, restore the ignored research inputs, and give the benchmark its own dated series; current plotted endpoints use a different window and stress fee |
| Verify data files against the manifest before research runs | Open | Reject a run if the Feather file hashes no longer match the manifest it records as provenance |

### Open decisions

| Decision | Current position |
| --- | --- |
| Start the €10 live pilot | The maintainer's; requires authorization, account and key |
| Legacy live database or state migration | None needed: no live state has ever existed |
| Control surface beyond Telegram and the SSH-tunnelled API | None planned; no dashboard dependency assumed |

### Deferred capabilities

Deferred means in scope, but started only when evidence calls for it; not
dropped.

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
