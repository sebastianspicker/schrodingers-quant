# ADR-0003 — Operational defaults

**Date:** 2026-09-24. **Status:** accepted (decisions D3–D6, delegated by the maintainer).

| Decision | Choice | Reason |
| --- | --- | --- |
| D3 soak length | ≥ 14 days of unattended dry-run on the VPS, with drills, before live | Covers at least two weekly cycles and several strategy decisions; cheap to extend |
| D4 alerting | Telegram (Freqtrade built-in) for trade events and control; an external dead-man's switch (healthchecks.io-compatible ping URL) for silence | Silence is the failure an in-process notifier cannot report |
| D4 backups | Nightly `sqlite3 .backup` plus config, pushed with restic to a user-supplied repository | Consistent SQLite snapshot; encrypted, deduplicated offsite copy |
| D4 VPS | Not provisioned from this repository; the runbook targets any Debian 13 host | Needs the maintainer's account and payment |
| D5 handoff | `.agents/handoff.md` (ignored) holds transient notes between coding agents; `docs/status.md` (formerly `docs/handoff.md`, ADR-0006) is the tracked status page | One place for transient state, one for tracked status |
| D6 CI | GitHub Actions runs `make ci` on every push and pull request; locally `make ci` | Same checks in both places |
