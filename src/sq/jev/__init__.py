"""The Jev shadow/filter experiment: an optional, isolated entry observer.

Jev never receives exchange credentials and has no role in order execution
(see AGENTS.md). `sq.jev.protocol` is the stdlib-only file contract shared
with `user_data/strategies/H1JevShadow.py`; `sq.jev.providers`, `sq.jev.worker`
and `sq.jev.evaluate` build on it.
"""
