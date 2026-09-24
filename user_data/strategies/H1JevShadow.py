"""H1ChannelBreakout with an optional Jev observation/entry-filter overlay.

This strategy inherits every signal, exit, protection and risk parameter from
the frozen `H1ChannelBreakout` unchanged (see `research/hypotheses/H1.md` and
`docs/adr/0005-h1-go-after-sizing-correction.md`). It only overrides
`confirm_trade_entry`, Freqtrade's callback for vetoing an entry order that the
strategy's own signals already decided to place. No populate_* method,
protection or exit path is touched, so exits, stoploss and the exit signal are
unaffected by anything in this module, including Jev being unavailable or
erroring.

Mode is read from the merged config's `"jev"` key (verified to survive
Freqtrade's config schema: `freqtrade.config_schema.config_schema.CONF_SCHEMA`
has no top-level `additionalProperties: false`, so an unrecognised top-level
key such as `jev` passes `validate_config_schema` unchanged and is available
via `self.config`) with an environment-variable fallback if a future
Freqtrade version starts rejecting it:

    {"jev": {"mode": "off" | "shadow" | "filter",
              "assessment_ttl_minutes": 60,
              "dir": "/freqtrade/user_data/runtime/jev"}}

An unrecognized mode blocks new entries (fail closed); exits are unaffected.
`mode` also falls back to `SQ_JEV_MODE`, `dir` to `SQ_JEV_DIR`, and
`assessment_ttl_minutes` to `SQ_JEV_TTL_MINUTES` if the config key is absent.

Modes:
  - "off" (default): behaves exactly like `H1ChannelBreakout`. Returns True
    immediately; no file I/O, no DataProvider access.
  - "shadow": records a candidate (see below) and returns True. Orders are
    never affected by shadow mode, even if recording fails.
  - "filter": records a candidate, then blocks the entry (returns False)
    unless an `assessments.jsonl` record for that candidate exists, is not
    expired, and has `decision == "approve"`. Any exception anywhere in this
    path is caught here and treated as a block (see "why never raise" below).

## Candidate identity

A candidate is one (strategy, pair, signal candle) entry opportunity:
`candidate_id` is a stable sha256 of the strategy class name, pair, the
signal candle's UTC open/close-basis timestamp (the last row of
`self.dp.get_analyzed_dataframe(pair, self.timeframe)`, i.e. the last *closed*
candle, per `process_only_new_candles = True`) and a "strategy file identity"
hash: sha256 of the concatenated `inspect.getsource()` of this class and every
non-Freqtrade base class in its MRO (in practice: this file plus the frozen
`H1ChannelBreakout.py`, excluding `IStrategy` itself). Candidates are appended
once per `candidate_id` to `<dir>/candidates.jsonl`, deduplicated against
already-recorded ids.

## Why `confirm_trade_entry` must never raise

Freqtrade wraps every strategy callback, including `confirm_trade_entry`, in
`strategy_safe_wrapper(..., default_retval=True)`
(`freqtrade/freqtradebot.py`, `execute_entry`, around the
`strategy_safe_wrapper(self.strategy.confirm_trade_entry, default_retval=True)`
call). That wrapper (`freqtrade/strategy/strategy_wrapper.py`) catches *any*
exception raised by the callback and returns `default_retval` — here `True`,
i.e. **allow the entry**. An uncaught exception in filter mode would therefore
silently defeat the filter and let the entry through, the opposite of "missing
or expired assessment blocks only new entries." This module therefore wraps
its own logic in `try/except Exception` and returns `False` explicitly on any
internal error in filter mode (record/lookup failures, a malformed assessment
line, a missing DataProvider, etc.), rather than relying on Freqtrade's
built-in exception handling.

## Confirm-trade-entry retry window (filter mode latency requirement)

Verified against the pinned image
(`freqtradeorg/freqtrade:2026.8@sha256:4d23160b501d2b34579e76f57ad75edfa274967cd0dd824ff1c1b86d8c166ab4`,
`/freqtrade/freqtrade/freqtradebot.py`):

  - `FreqtradeBot.process()` (line ~257) runs once per main-loop iteration,
    throttled by `internals.process_throttle_secs` (5 seconds in
    `config/base.json`). Each call reaches `enter_positions()` → `create_trade(pair)`
    (line ~684) whenever a free trade slot exists and the pair has no open
    trade.
  - `create_trade()` (line ~701-707) re-reads the *cached* analyzed dataframe
    (`self.dataprovider.get_analyzed_dataframe`) and re-derives the entry
    signal from its last row on every call. With `process_only_new_candles =
    True`, that cached dataframe — and therefore the entry signal — only
    changes when a new candle closes; it does not change between iterations
    within the same still-open candle.
  - If the signal is set, `execute_entry()` (line ~895) calls
    `confirm_trade_entry` (line ~945-958) through
    `strategy_safe_wrapper(..., default_retval=True)`. A `False` return only
    skips *this* call: `create_trade` returns `False` and `enter_positions`
    logs "Found no enter signals ... Trying again", i.e. this iteration's
    creation attempt is abandoned, not the trade.

  **Consequence:** a `confirm_trade_entry` rejection in filter mode *is*
  retried on every subsequent main-loop iteration (every
  `process_throttle_secs`, here 5s) for as long as the same closed candle's
  entry signal persists — for this 4h strategy, up to just under 4 hours from
  the candle close that produced the signal, or until the pair's max open
  trades fills first. The Jev worker's end-to-end latency (poll interval +
  provider call + write) must stay comfortably inside that window, or a
  legitimate `approve` assessment will never arrive in time for any retry to
  see it and the entry opportunity for that candle is lost for good (the
  strategy does not re-signal on the same closed candle after it rolls over).
"""

import hashlib
import inspect
import json
import logging
import os
from datetime import UTC, datetime, timedelta
from functools import cache
from pathlib import Path

from H1ChannelBreakout import H1ChannelBreakout

logger = logging.getLogger(__name__)

DEFAULT_MODE = "off"
DEFAULT_DIR = "/freqtrade/user_data/runtime/jev"
DEFAULT_TTL_MINUTES = 60.0

CANDIDATES_FILENAME = "candidates.jsonl"
ASSESSMENTS_FILENAME = "assessments.jsonl"


@cache
def _strategy_identity(strategy_cls: type) -> str:
    """Sha256 of the source of `strategy_cls` and its non-Freqtrade bases.

    Captures this file (H1JevShadow) and the inherited, frozen
    H1ChannelBreakout signal/exit logic, so the candidate id changes if either
    changes. IStrategy's own source (and anything else under the `freqtrade.`
    package) is excluded so the identity reflects only this project's code.
    """
    sources: list[str] = []
    for cls in reversed(strategy_cls.__mro__):
        if cls is object:
            continue
        module = getattr(cls, "__module__", "") or ""
        if module.startswith("freqtrade."):
            continue
        try:
            sources.append(inspect.getsource(cls))
        except OSError, TypeError:
            continue
    return hashlib.sha256("\n".join(sources).encode("utf-8")).hexdigest()


def _candidate_id(
    strategy_name: str, pair: str, signal_candle_time: datetime, strategy_version: str
) -> str:
    payload = "|".join(
        [strategy_name, pair, signal_candle_time.astimezone(UTC).isoformat(), strategy_version]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _append_json_line(path: Path, record: dict) -> None:
    # A single write() call of one line is atomic against other appenders on
    # POSIX for the line lengths this module produces (well under PIPE_BUF);
    # this module has exactly one writer (the live strategy process) anyway.
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")


def _read_jsonl(path: Path):
    """Yield parsed records from a JSONL file; skip and log malformed lines."""
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
                logger.warning("Jev: skipping malformed line %d in %s", line_number, path)


def _read_ids(path: Path, key: str) -> set:
    return {record[key] for record in _read_jsonl(path) if key in record}


def _parse_utc(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except TypeError, ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _latest_assessment(path: Path, candidate_id: str) -> dict | None:
    """Newest matching assessment by `available_at`; falls back to file order."""
    latest: dict | None = None
    latest_available_at: datetime | None = None
    for record in _read_jsonl(path):
        if record.get("candidate_id") != candidate_id:
            continue
        available_at = _parse_utc(record.get("available_at"))
        if (
            latest is None
            or latest_available_at is None
            or (available_at is not None and available_at >= latest_available_at)
        ):
            latest = record
            latest_available_at = available_at
    return latest


class H1JevShadow(H1ChannelBreakout):
    """H1ChannelBreakout plus an optional Jev shadow/filter overlay on entries.

    See the module docstring for modes, candidate identity, the exception
    policy and the confirm_trade_entry retry-window finding. Only
    `confirm_trade_entry` is overridden; every populate_* method, protection,
    exit path and risk parameter is inherited unchanged from
    `H1ChannelBreakout`.
    """

    def _jev_config(self) -> dict:
        configured = self.config.get("jev")
        return configured if isinstance(configured, dict) else {}

    def _jev_mode(self) -> str:
        mode = self._jev_config().get("mode")
        if mode is None:
            mode = os.environ.get("SQ_JEV_MODE", DEFAULT_MODE)
        return str(mode)

    def _jev_dir(self) -> Path:
        directory = self._jev_config().get("dir")
        if directory is None:
            directory = os.environ.get("SQ_JEV_DIR", DEFAULT_DIR)
        return Path(directory)

    def _jev_ttl_minutes(self) -> float:
        ttl = self._jev_config().get("assessment_ttl_minutes")
        if ttl is None:
            ttl = os.environ.get("SQ_JEV_TTL_MINUTES", DEFAULT_TTL_MINUTES)
        return float(ttl)

    def _jev_build_candidate(self, pair: str, rate: float, current_time: datetime) -> dict:
        if self.dp is None:
            raise RuntimeError("Jev: no DataProvider on this strategy instance")
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe is None or dataframe.empty:
            raise RuntimeError(f"Jev: no analyzed dataframe available yet for {pair}")

        last_candle = dataframe.iloc[-1]
        signal_candle_time = last_candle["date"]
        if signal_candle_time.tzinfo is None:
            signal_candle_time = signal_candle_time.tz_localize("UTC")
        else:
            signal_candle_time = signal_candle_time.tz_convert("UTC")
        signal_candle_time = signal_candle_time.to_pydatetime()

        strategy_name = type(self).__name__
        strategy_version = _strategy_identity(type(self))
        candidate_id = _candidate_id(strategy_name, pair, signal_candle_time, strategy_version)

        return {
            "candidate_id": candidate_id,
            "pair": pair,
            "signal_candle_time": signal_candle_time.isoformat(),
            "rate": float(rate),
            "observed_at": datetime.now(UTC).isoformat(),
            "strategy_name": strategy_name,
            "strategy_version": strategy_version,
        }

    def _jev_record_candidate(self, candidate: dict) -> None:
        jev_dir = self._jev_dir()
        jev_dir.mkdir(parents=True, exist_ok=True)
        path = jev_dir / CANDIDATES_FILENAME

        seen = getattr(self, "_jev_seen_ids", None)
        if seen is None:
            seen = _read_ids(path, "candidate_id")
            self._jev_seen_ids = seen

        if candidate["candidate_id"] in seen:
            return
        _append_json_line(path, candidate)
        seen.add(candidate["candidate_id"])

    def _jev_filter_decision(self, candidate: dict) -> bool:
        path = self._jev_dir() / ASSESSMENTS_FILENAME
        assessment = _latest_assessment(path, candidate["candidate_id"])
        if assessment is None:
            logger.info(
                "Jev filter: no assessment yet for %s (%s); blocking entry",
                candidate["pair"],
                candidate["candidate_id"],
            )
            return False

        available_at = _parse_utc(assessment.get("available_at"))
        if available_at is None:
            logger.warning(
                "Jev filter: assessment for %s has no usable available_at; blocking entry",
                candidate["candidate_id"],
            )
            return False

        expires_at = available_at + timedelta(minutes=self._jev_ttl_minutes())
        now = datetime.now(UTC)
        if expires_at < now:
            logger.info(
                "Jev filter: assessment for %s expired at %s (now %s); blocking entry",
                candidate["candidate_id"],
                expires_at.isoformat(),
                now.isoformat(),
            )
            return False

        decision = assessment.get("decision")
        if decision != "approve":
            logger.info(
                "Jev filter: assessment for %s decision=%r; blocking entry",
                candidate["candidate_id"],
                decision,
            )
            return False

        return True

    def confirm_trade_entry(
        self,
        pair: str,
        order_type: str,
        amount: float,
        rate: float,
        time_in_force: str,
        current_time: datetime,
        entry_tag: str | None,
        side: str,
        **kwargs,
    ) -> bool:
        try:
            mode = self._jev_mode()
        except Exception:
            logger.exception("Jev: could not read the mode; blocking entry for %s", pair)
            return False

        if mode == "off":
            return True

        if mode not in ("shadow", "filter"):
            # A mistyped mode must not silently disable an intended filter.
            logger.error("Jev: unknown mode %r in config/env; blocking entry for %s", mode, pair)
            return False

        if mode == "shadow":
            try:
                candidate = self._jev_build_candidate(pair, rate, current_time)
                self._jev_record_candidate(candidate)
            except Exception:
                # Shadow mode must never affect orders, including on I/O errors.
                logger.exception("Jev shadow: failed to record a candidate for %s", pair)
            return True

        # mode == "filter"
        try:
            candidate = self._jev_build_candidate(pair, rate, current_time)
            self._jev_record_candidate(candidate)
            return self._jev_filter_decision(candidate)
        except Exception:
            # Never let an exception reach Freqtrade's strategy_safe_wrapper:
            # its default_retval=True would allow the entry, the opposite of
            # this module's fail-closed contract. See the module docstring.
            logger.exception("Jev filter: blocking entry for %s after an internal error", pair)
            return False
