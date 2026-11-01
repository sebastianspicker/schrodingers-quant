"""Jev worker: polls recorded strategy candidates and writes model assessments.

Runs as its own process/container with NO exchange credentials and no
Freqtrade config (see ../../../compose.yaml, service `jev-worker`, profile
"jev", not started by default). It only reads `<dir>/candidates.jsonl`
(written by `user_data/strategies/H1JevShadow.py`) and appends
`<dir>/assessments.jsonl`; it never touches an exchange, an order, or
Freqtrade's own config or database.

For each unseen candidate — unseen means: past the persisted byte-offset
cursor into candidates.jsonl, AND without an existing assessments.jsonl
record for its candidate_id, so a candidate already answered is never
re-queried even if the cursor were ever reset — the worker calls
`provider.assess(candidate, prompt)` under a bounded timeout and appends
exactly one assessments.jsonl record: candidate_id, input (the exact
candidate payload sent), input_timestamp, model, model_version,
prompt_version, decision, rationale, confidence, requested_at, available_at,
latency_ms, error. Any exception or timeout raised by the provider is caught
here and recorded as an "abstain" with the error's class name; provider output
that violates the Assessment field types, cannot be written as JSON, or whose decision is not one of
`protocol.DECISIONS`, is recorded as an "abstain" too. A candidate
line that is not a JSON object is logged and skipped (the cursor moves past
it), and an unexpected failure in one polling pass is logged and the loop
continues; the worker loop itself does not die on bad input. (It still exits
at startup if its directory is unusable, see `main`.)

A bounded batch size (`--queue-maxsize`) caps how many unseen candidates are
read and processed in one pass, so a large backlog cannot be pulled into
memory at once; leftover candidates are picked up on the next pass (the
cursor only advances past what this pass actually consumed).

Single instance: a lock file (`<dir>/worker.lock`) is held with an exclusive
`flock` for the run; a concurrent second instance refuses to start rather than
racing the cursor or the assessments file. The file itself is never deleted
(the kernel drops the lock when the process ends), so its presence does not
mean a worker is running.

Prompt text is versioned in `prompts/v1.txt`; `prompt_version` is derived
from its content (`sha256:<hex prefix>`), so a changed prompt is visible in
every assessment it produced.
"""

import argparse
import fcntl
import hashlib
import json
import logging
import os
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from threading import Lock, Thread

from sq.jev.protocol import (
    ASSESSMENTS_FILENAME,
    CANDIDATES_FILENAME,
    DECISIONS,
    DEFAULT_DIR,
    append_jsonl,
    read_jsonl,
)
from sq.jev.providers import Assessment, Provider, build_provider

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_QUEUE_MAXSIZE = 50
DEFAULT_POLL_INTERVAL_SECONDS = 15.0

CURSOR_FILENAME = "worker_cursor.txt"
LOCK_FILENAME = "worker.lock"

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
DEFAULT_PROMPT_PATH = PROMPTS_DIR / "v1.txt"

# Python cannot forcibly stop a running thread. Keep at most one provider call
# alive past its deadline; until it returns, later candidates time out without
# starting more calls. A real network provider must also set its own request
# timeout so the one discarded call eventually releases its resources.
_provider_threads: dict[int, Thread] = {}
_provider_threads_lock = Lock()


# --- prompt versioning -------------------------------------------------


def load_prompt(path: Path) -> tuple[str, str]:
    """Return (prompt text, prompt_version) for a prompt file."""
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text, f"sha256:{digest[:16]}"


# --- startup ---------------------------------------------------------------


class WorkerStartupError(RuntimeError):
    """The worker cannot start; the message says why and is shown without a traceback."""


def ensure_writable_dir(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkerStartupError(f"Jev worker: cannot create {path}: {exc.strerror}") from None
    if not os.access(path, os.W_OK | os.X_OK):
        raise WorkerStartupError(
            f"Jev worker: {path} is not writable by uid {os.getuid()}; "
            "fix the directory's ownership on the host (it must be writable by the "
            "container user) and restart"
        )


# --- single-instance lock -----------------------------------------------


@contextmanager
def single_instance_lock(path: Path):
    """Exclusive advisory lock (flock) for the duration of one worker run.

    Raises RuntimeError immediately if another live instance holds the lock;
    never blocks. The kernel releases the lock when the process dies, so a
    lock file left behind by a crash or `docker kill` never blocks a restart.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    except OSError as exc:
        raise WorkerStartupError(
            f"Jev worker: cannot open lock file {path}: {exc.strerror}"
        ) from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise WorkerStartupError(
                f"Jev worker: {path} is locked by another running instance"
            ) from None
        os.ftruncate(fd, 0)
        os.write(fd, str(os.getpid()).encode("utf-8"))
        yield
    finally:
        os.close(fd)  # releases the flock


# --- JSONL I/O -----------------------------------------------------------


def read_cursor(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return int(path.read_text().strip() or "0")
    except ValueError:
        logger.warning("Jev worker: cursor file %s is unreadable; restarting from 0", path)
        return 0


def write_cursor(path: Path, offset: int) -> None:
    # Write-then-rename so a crash mid-write cannot leave a truncated cursor.
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(str(offset))
    tmp_path.replace(path)


def read_new_candidates(
    candidates_path: Path, offset: int, max_count: int
) -> tuple[list[dict], int]:
    """Complete JSON lines strictly after `offset`, capped at `max_count`.

    Returns (candidates, new_offset). `new_offset` only advances past bytes
    that were actually parsed into a returned candidate or skipped as
    malformed/blank, so a line cut short by a concurrent writer is left for
    the next pass.
    """
    if not candidates_path.exists():
        return [], offset

    size = candidates_path.stat().st_size
    if offset > size:
        # The file is shorter than the cursor: it was truncated or replaced by
        # hand. Rescan from the start; assessments.jsonl still prevents any
        # candidate that already has a record from being queried again.
        logger.warning(
            "Jev worker: cursor %d is beyond the end of %s (%d bytes); resetting to 0",
            offset,
            candidates_path,
            size,
        )
        offset = 0

    candidates: list[dict] = []
    with open(candidates_path, "rb") as fh:
        fh.seek(offset)
        cursor = offset
        while len(candidates) < max_count:
            line = fh.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                # Partial trailing line: stop here, don't advance past it.
                break
            cursor += len(line)
            text = line.strip()
            if not text:
                continue
            try:
                parsed = json.loads(text)
            # JSONDecodeError, UnicodeDecodeError on invalid UTF-8, RecursionError on
            # pathological nesting: skip the line rather than stall the cursor on it.
            except (ValueError, RecursionError):
                logger.warning(
                    "Jev worker: skipping malformed candidate line in %s", candidates_path
                )
                continue
            if not isinstance(parsed, dict):
                logger.warning(
                    "Jev worker: skipping non-object candidate line in %s", candidates_path
                )
                continue
            candidates.append(parsed)
    return candidates, cursor


# --- assessment ------------------------------------------------------


def _assess_in_thread(
    provider: Provider,
    candidate: dict,
    prompt: str,
    results: Queue[tuple[Assessment | None, str | None]],
) -> None:
    try:
        results.put((provider.assess(candidate, prompt), None))
    except Exception as exc:  # the provider is untrusted, third-party code
        results.put((None, exc.__class__.__name__))


def assess_with_timeout(
    provider: Provider, candidate: dict, prompt: str, timeout_seconds: float
) -> tuple[Assessment | None, str | None]:
    """Run provider.assess under a bounded timeout.

    Returns (assessment, error_class_name); exactly one is None. Any exception
    or deadline expiry is caught here: the worker loop never waits for a late
    result. The late call runs in one daemon thread and its result is discarded;
    another call for the same provider cannot start until that thread returns.
    """
    if timeout_seconds <= 0:
        return None, "TimeoutError"

    provider_key = id(provider)
    with _provider_threads_lock:
        existing = _provider_threads.get(provider_key)
        if existing is not None:
            if existing.is_alive():
                return None, "TimeoutError"
            _provider_threads.pop(provider_key, None)

        results: Queue[tuple[Assessment | None, str | None]] = Queue(maxsize=1)
        thread = Thread(
            target=_assess_in_thread,
            args=(provider, candidate, prompt, results),
            name="jev-provider-assessment",
            daemon=True,
        )
        _provider_threads[provider_key] = thread
        try:
            thread.start()
        except Exception as exc:
            _provider_threads.pop(provider_key, None)
            return None, exc.__class__.__name__

    try:
        result = results.get(timeout=timeout_seconds)
    except Empty:
        return None, "TimeoutError"
    # The helper's queue write is its final action. Join the now-completed
    # thread before admitting another call, avoiding a race where a fast result
    # is mistaken for an assessment that is still in flight.
    thread.join()
    with _provider_threads_lock:
        if _provider_threads.get(provider_key) is thread:
            _provider_threads.pop(provider_key, None)
    return result


def build_assessment_record(
    candidate: dict,
    prompt_version: str,
    requested_at: datetime,
    assessment: Assessment | None,
    error: str | None,
) -> dict:
    available_at = datetime.now(UTC)
    latency_ms = (available_at - requested_at).total_seconds() * 1000

    # Dataclass annotations do not validate provider responses. Check the
    # primitive contract before serialization: JSON can encode invalid fields
    # such as list-valued rationales (including deep lists on newer Python).
    if assessment is not None and (
        not isinstance(assessment, Assessment)
        or not all(
            isinstance(value, str)
            for value in (assessment.rationale, assessment.model, assessment.model_version)
        )
        or (
            assessment.confidence is not None
            and (
                not isinstance(assessment.confidence, (int, float))
                or isinstance(assessment.confidence, bool)
            )
        )
    ):
        assessment, error = None, "InvalidAssessment"

    if assessment is None:
        # Provider raised or timed out: record an abstain with the error,
        # never silently drop the candidate.
        error = error or "InvalidAssessment"
        decision, rationale, confidence = "abstain", f"provider error: {error}", None
        model, model_version = "unknown", "unknown"
    else:
        decision = assessment.decision
        rationale = assessment.rationale
        confidence = assessment.confidence
        model = assessment.model
        model_version = assessment.model_version
        if decision not in DECISIONS:
            # Clamp, and say why: only a known decision may ever reach the file.
            error = error or "InvalidDecision"
            rationale = (
                f"provider returned unknown decision {str(decision)[:60]!r}; clamped to abstain"
            )
            decision = "abstain"

    return {
        "candidate_id": candidate.get("candidate_id"),
        "input": candidate,
        "input_timestamp": candidate.get("observed_at"),
        "model": model,
        "model_version": model_version,
        "prompt_version": prompt_version,
        "decision": decision,
        "rationale": rationale,
        "confidence": confidence,
        "requested_at": requested_at.isoformat(),
        "available_at": available_at.isoformat(),
        "latency_ms": round(latency_ms, 3),
        "error": error,
    }


# --- one pass --------------------------------------------------------


def _is_json_serialisable(record: dict) -> bool:
    try:
        # The same dumps arguments as protocol.append_jsonl (sort_keys), plus no NaN.
        json.dumps(record, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        return False
    return True


def process_once(
    *,
    candidates_path: Path,
    assessments_path: Path,
    cursor_path: Path,
    provider: Provider,
    prompt: str,
    prompt_version: str,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    queue_maxsize: int = DEFAULT_QUEUE_MAXSIZE,
) -> int:
    """Process up to `queue_maxsize` unseen candidates once. Returns the count processed."""
    already_assessed = {
        record.get("candidate_id")
        for record in read_jsonl(assessments_path)
        if isinstance(record, dict) and record.get("candidate_id")
    }

    offset = read_cursor(cursor_path)
    candidates, new_offset = read_new_candidates(candidates_path, offset, queue_maxsize)

    processed = 0
    for candidate in candidates:
        candidate_id = candidate.get("candidate_id")
        if (
            not isinstance(candidate_id, str)
            or not candidate_id
            or candidate_id in already_assessed
        ):
            # Already has a record (or is malformed enough to lack an id):
            # never re-query, never fabricate an id.
            continue

        requested_at = datetime.now(UTC)
        assessment, error = assess_with_timeout(provider, candidate, prompt, timeout_seconds)
        record = build_assessment_record(candidate, prompt_version, requested_at, assessment, error)
        if not _is_json_serialisable(record):
            # The provider is untrusted: a field that cannot be written as
            # JSON must not crash the worker or leave the candidate unanswered.
            logger.warning(
                "Jev worker: provider output for %s is not JSON-serialisable; recording abstain",
                candidate_id,
            )
            record = build_assessment_record(
                candidate, prompt_version, requested_at, None, "UnserialisableAssessment"
            )
        append_jsonl(assessments_path, record)
        already_assessed.add(candidate_id)
        processed += 1

    write_cursor(cursor_path, new_offset)
    return processed


def run_forever(
    *,
    candidates_path: Path,
    assessments_path: Path,
    cursor_path: Path,
    provider: Provider,
    prompt: str,
    prompt_version: str,
    timeout_seconds: float,
    queue_maxsize: int,
    poll_interval_seconds: float,
) -> None:
    while True:
        try:
            processed = process_once(
                candidates_path=candidates_path,
                assessments_path=assessments_path,
                cursor_path=cursor_path,
                provider=provider,
                prompt=prompt,
                prompt_version=prompt_version,
                timeout_seconds=timeout_seconds,
                queue_maxsize=queue_maxsize,
            )
        except OSError as exc:
            # Typically an unwritable or full directory: one clear line, not a traceback.
            logger.error(
                "Jev worker: pass failed (%s); retrying in %.0fs", exc, poll_interval_seconds
            )
        except Exception:
            # One bad pass must not end the process: under `restart:
            # unless-stopped` that would crash-loop on the same input.
            logger.exception("Jev worker: pass failed; retrying in %.0fs", poll_interval_seconds)
        else:
            if processed:
                logger.info("Jev worker: processed %d new candidate(s)", processed)
        time.sleep(poll_interval_seconds)


# --- CLI -----------------------------------------------------------------


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dir", type=Path, default=DEFAULT_DIR, help="Shared candidates/assessments directory"
    )
    parser.add_argument("--provider", default="null", choices=["null", "jev"])
    parser.add_argument("--prompt", type=Path, default=DEFAULT_PROMPT_PATH)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--queue-maxsize", type=int, default=DEFAULT_QUEUE_MAXSIZE)
    parser.add_argument(
        "--poll-interval-seconds", type=float, default=DEFAULT_POLL_INTERVAL_SECONDS
    )
    parser.add_argument(
        "--once", action="store_true", help="Process one pass and exit, instead of polling forever"
    )
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    args = build_arg_parser().parse_args()

    jev_dir: Path = args.dir
    lock_path = jev_dir / LOCK_FILENAME

    prompt_text, prompt_version = load_prompt(args.prompt)
    provider = build_provider(args.provider)

    try:
        ensure_writable_dir(jev_dir)
        with single_instance_lock(lock_path):
            kwargs = dict(
                candidates_path=jev_dir / CANDIDATES_FILENAME,
                assessments_path=jev_dir / ASSESSMENTS_FILENAME,
                cursor_path=jev_dir / CURSOR_FILENAME,
                provider=provider,
                prompt=prompt_text,
                prompt_version=prompt_version,
                timeout_seconds=args.timeout_seconds,
                queue_maxsize=args.queue_maxsize,
            )
            if args.once:
                processed = process_once(**kwargs)
                logger.info("Jev worker: processed %d new candidate(s)", processed)
            else:
                run_forever(poll_interval_seconds=args.poll_interval_seconds, **kwargs)
    except WorkerStartupError as exc:
        # Raised only before the loop starts (unusable directory, lock held).
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
