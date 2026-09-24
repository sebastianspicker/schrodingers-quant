# Status

**As of 2026-09-24.** For the structure, see [architecture](architecture.md).
For the reasoning behind it, see the [ADRs](adr/README.md).

## Summary

The system is dry-run only. Nothing has been deployed, funded or traded live. The tracked strategy is H1 (20/10-day channel breakout, BTC/EUR 4h).
It was predeclared, bias-checked and evaluated on held-out data (2024-07 →
2026-09). With the fixed stake H1 declares, it passed all four criteria:
+39.8 % net, marked-to-market drawdown 30.4 % against a 31.3 % limit. That is
**GO, marginal** ([ADR-0005](adr/0005-h1-go-after-sizing-correction.md)). An
earlier run that compounded the stake failed the drawdown test; both runs are
published. GO permits forward paper trading and the €10 execution pilot, nothing
more. VPS operations, live-pilot tooling and the Jev shadow mode are built and
tested locally.

## What exists

| Area | State |
| --- | --- |
| Runtime | Freqtrade 2026.8 pinned by digest in `compose.yaml`; layered config (base → VPS → live → secrets) |
| Strategies | `H1ChannelBreakout` (tracked, dry-run, frozen); `H1JevShadow` (off/shadow/filter) |
| Research | Binance proxy data validated against Kraken, data manifest, bias checks, fixed-stake backtests, marked-to-market metrics, [H1 record](../research/experiments/H1/record.md) |
| Operations | Health/dead-man's-switch ping; backup of every trade DB and the Jev records; restore of a named DB that leaves the bot stopped; image pruning; hardened systemd units; [Debian 13 runbook](../ops/README.md); soak checklist |
| Live tooling | Read-only preflight (BTC/EUR €8 and €10 feasible at an assumed 0.40 % fee), read-only reconciliation, credential-free `config/live.json`, secrets validation for deployable overlays |
| Jev | Candidate recording, credential-free worker with a null provider, fail-closed filter, leakage-safe evaluation harness; no real provider ([Jev](jev.md)) |
| Checks | `make ci`: lint, format, shellcheck, Compose variants, three config validations, pytest in the pinned image; the same in GitHub Actions |
| Demo | [GitHub Pages](https://sebastianspicker.github.io/schrodingers-quant/) built from `pages/` and the recorded H1 equity curves (`research/run.sh equity-curves`, which refuses to write if a curve disagrees with the record) |

## Verified

- `make ci` exits 0 locally after the restructure
  ([ADR-0006](adr/0006-one-package-one-runtime-definition.md)).
- Restructure differential checks (2026-09-24): `research/run.sh train`, the
  train benchmark and the data manifest reproduce the recorded H1 results and
  provenance exactly; marked-to-market drawdown on the recorded validation result
  matches. The held-out window was not rerun.
- Local Docker drills (2026-09-24): `make up` loads `H1ChannelBreakout` with its
  protections, state `STOPPED`, no errors; both strategies load in the bot
  container without `src/`; backup with the container running snapshots both
  `dry-run.sqlite` and `live.sqlite`; restore of `live.sqlite` by name moves the
  current file and a stale WAL aside and leaves the bot stopped; the Jev worker
  records an assessment with the null provider; read-only preflight against
  public Kraken data reports BTC/EUR €8 feasible.
- Earlier: H1 dry-run smoke test against live Kraken public data, and the local
  rehearsal of API control, health ping and process-crash restart
  ([rehearsal](../ops/local-rehearsal.md)).

## Not verified

Exchange order acceptance, partial fills, exchange-side stops, restart
reconciliation with real orders, authenticated preflight and reconciliation,
the entry-fee currency assumption, the restic path, systemd units and hardening
on a real host, VPS resource use, any real model provider, and future profitability.

## Open work

| Item | State | Needs |
| --- | --- | --- |
| 14-day VPS soak with drills; doubles as H1's forward paper test | Blocked | A Debian 13 VPS ([runbook](../ops/README.md), [soak](../ops/soak-checklist.md)) |
| €10 live pilot drills: forced round trip, restart with an open position, exchange stop after restart, key revocation | Blocked | The maintainer's authorization, a funded isolated Kraken account, a trade-only key; the soak first ([live pilot](live-pilot.md)) |
| Decision on continuing or scaling after the pilot | Open | An ADR; never automatic |
| Jev real provider and shadow assessments on forward data | Blocked | Jev API access and documentation; a spending cap |
| Jev matched evaluation (baseline vs filter) | Harness done | Recorded assessments; compare against a deterministic rule before crediting the model |
| Jev live filter | Mechanism done, off | A real provider and a positive matched evaluation |
| Successor hypotheses (H2…) | Open | Predeclared, judged on forward data after 2026-09-24; the 2024-07 → 2026-09 window is spent |
| Race-safe publication of frozen experiment artifacts | Open | Needed only if experiment freezing is automated (currently manual records) |

### Open decisions

| Decision | Current position |
| --- | --- |
| Start the €10 live pilot | The maintainer's; requires authorization, account and key |
| Legacy live DB/state migration | None needed: no live state has ever existed |
| Control surface beyond Telegram and the SSH-tunnelled API | None planned; no dashboard dependency assumed |

### Deferred capabilities

Deferred means in scope but triggered by evidence, not dropped
([ADR-0001](adr/0001-evidence-first-restructure.md)):

| Capability | Trigger |
| --- | --- |
| Intent journal, custom execution adapter | A restart or ambiguous-submission drill (soak, pilot) shows a gap in Freqtrade's handling; must meet the [contracts](architecture.md#contracts-for-deferred-execution-work) |
| Host supervisor beyond systemd + Docker restart | The soak shows a failure that health ping and restart policy miss |
| Forecasting / learning models | A simple baseline has a measured forward edge |
| Full release manifests (beyond the digest pin and CI) | A second deployment target or a release cadence |

### Gates

| Gate | Meaning | State |
| --- | --- | --- |
| G-0 | Scaffold and checks | Reached |
| G-1 | Integrated software (strategy, ops, live tooling, Jev mechanism) tested locally | Reached |
| G-2 | Reproducible release: committed, CI on a remote | Reached once CI passes on GitHub |
| G-3 | Unattended Debian operation: soak passed | Not reached |
| G-4 | Live execution: pilot drills passed | Not reached |
| G-5 | Economic evidence: positive forward record after costs | Not reached |

## Background

The repository was rebuilt around its current layout before the first commit
(ADR-0006). An earlier implementation was reviewed on 2026-09-22 (164 files,
594 of 619 tests passing on macOS). That review and the retired planning
documents were kept outside the repository; they are not part of this history
and not CI evidence.
