"""Stdlib-only file contract for Jev's candidates/assessments JSONL files.

Used by `sq.jev.worker` (reads candidates.jsonl, writes assessments.jsonl) and
`sq.jev.evaluate` (reads both, offline). The writer of candidates and reader of
assessments is `user_data/strategies/H1JevShadow.py`, which never imports `sq`
(its class source is part of every candidate_id) and keeps its own copy of these
names; tests/jev/test_protocol.py checks that the two agree. Stdlib-only, so the
worker stays free of exchange code (tests/test_architecture.py).
"""

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_DIR = Path("/freqtrade/user_data/runtime/jev")
CANDIDATES_FILENAME = "candidates.jsonl"
ASSESSMENTS_FILENAME = "assessments.jsonl"

# Decisions a provider may return. "abstain" means the provider declined or
# failed to produce a usable answer; H1JevShadow's filter mode treats anything
# other than "approve" as a block.
DECISIONS = ("approve", "reject", "abstain")


def read_jsonl(path: Path, *, strict: bool = False) -> Iterator[dict]:
    """Yield parsed records from a JSONL file.

    By default, a malformed line is skipped and logged, never raised: Jev's
    file I/O must never crash the strategy process or the worker loop. Pass
    `strict=True` to raise `json.JSONDecodeError` instead — used by
    `sq.jev.evaluate`, an offline analysis tool where silently dropping a
    malformed input line would be a worse failure mode than stopping.
    """
    if not path.exists():
        return
    with open(path, encoding="utf-8") as fh:
        for line_number, raw_line in enumerate(fh, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                if strict:
                    raise
                logger.warning("Jev: skipping malformed line %d in %s", line_number, path)


def append_jsonl(path: Path, record: dict) -> None:
    """Append one record as a JSON line.

    A single write() call of one line is atomic against other appenders on
    POSIX for the line lengths this module produces (well under PIPE_BUF).
    """
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def parse_utc(value: str | None) -> datetime | None:
    """Parse an ISO 8601 timestamp string as a UTC-aware datetime, or None."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except TypeError, ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
