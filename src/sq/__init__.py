"""Schrödingers Quant: the project's Python package, run in the pinned Freqtrade image.

- `sq.config`   layered Freqtrade config loading and validation (`make validate*`).
- `sq.live`     read-only live-readiness checks: preflight and reconciliation.
- `sq.jev`      the credential-free Jev worker and its file protocol.
- `sq.research` the H1 research pipeline and offline analysis (`make research`).

Boundaries and dependency rules: docs/architecture.md, enforced by
tests/test_architecture.py.
"""
