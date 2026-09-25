"""The Jev shadow/filter experiment: an optional, isolated entry observer.

Jev never receives exchange credentials and has no role in order execution
(see AGENTS.md). `sq.jev.protocol` is the stdlib-only file contract shared
with `user_data/strategies/H1JevShadow.py`; `sq.jev.providers` and
`sq.jev.worker` build on it. Every module here imports only the standard
library and `sq.jev` (tests/test_architecture.py); offline analysis of Jev's
records is research and lives in `sq.research.jev_evaluation`.
"""
