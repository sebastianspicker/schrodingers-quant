# ADR-0001 — Evidence-first vertical slices

**Date:** 2026-09-24. **Status:** accepted (approval delegated by the maintainer).

## Context
The earlier implementation plan ordered 29 work packages foundation-first. It
planned an application journal, custom reconciliation and a host supervisor
beside Freqtrade before any strategy had shown an edge.

## Decision
Follow the restructure plan (retired by [ADR-0006](0006-one-package-one-runtime-definition.md);
open items now in [status](../status.md)): cross-cutting setup, then
slice A (a strategy with an edge), B (unattended dry-run), C (€10 live pilot)
and D (the Jev model filter). Use
Freqtrade's built-in order handling, protections, API/Telegram control and
health endpoint first; add custom code only for a gap shown by a test or drill.

## Consequences
The intent journal, custom execution adapter, host supervisor, full release
manifests and forecasting models are deferred, not dropped. Each has an evidence
trigger listed in [status](../status.md#deferred-capabilities).
