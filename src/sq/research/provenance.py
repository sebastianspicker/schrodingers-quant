"""Provenance helpers shared by the research scripts: the pinned image
reference, file hashing, and the provenance blocks recorded alongside
backtest and mark-to-market summaries (not a standalone entry point)."""

import hashlib
import os
from pathlib import Path


def image_ref() -> str:
    """The pinned Freqtrade image ref this research run used.

    Read from the SQ_FREQTRADE_IMAGE environment variable, which compose.yaml
    sets (from its single `x-freqtrade-image` anchor) for every `docker
    compose run --rm research ...` invocation, so the image digest is defined
    in exactly one place. Raises rather than silently recording an empty
    string in a provenance file if the variable is unset or empty.
    """
    value = os.environ.get("SQ_FREQTRADE_IMAGE")
    if not value:
        raise RuntimeError(
            "SQ_FREQTRADE_IMAGE is unset or empty; research provenance must not record an "
            "empty image reference. Run this via `docker compose run --rm research ...` "
            "(compose.yaml's `research` service sets SQ_FREQTRADE_IMAGE from the pinned "
            "x-freqtrade-image anchor)."
        )
    return value


def sha256_file(path: Path) -> str:
    """SHA-256 hex digest of a file's bytes, streamed to bound memory use."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_hash(manifest_path: Path | None) -> str | None:
    return sha256_file(manifest_path) if manifest_path and manifest_path.exists() else None


def build_summary_provenance(
    strategy_stats: dict,
    *,
    period: str,
    fee: float,
    strategy_file: Path,
    manifest_path: Path | None,
) -> dict:
    """The provenance block recorded alongside a backtest's summary metrics:
    period, fee, timerange, pair, strategy identity and hash, image digest and
    data manifest hash."""
    return {
        "period": period,
        "fee": fee,
        "timerange": strategy_stats["timerange"],
        "backtest_start": strategy_stats["backtest_start"],
        "backtest_end": strategy_stats["backtest_end"],
        "pairlist": strategy_stats["pairlist"],
        "strategy_name": strategy_stats["strategy_name"],
        "strategy_file": str(strategy_file),
        "strategy_file_sha256": sha256_file(strategy_file),
        "image_digest": image_ref(),
        "data_manifest_sha256": _manifest_hash(manifest_path),
        "enable_protections": strategy_stats["enable_protections"],
        "stoploss": strategy_stats["stoploss"],
        "minimal_roi": strategy_stats["minimal_roi"],
    }


def build_mtm_provenance(*, strategy_file: Path, manifest_path: Path | None) -> dict:
    """The provenance block recorded alongside a mark-to-market summary:
    image digest, strategy file hash and data manifest hash (no per-run
    timerange/fee; those are already in the mtm report's own fields)."""
    return {
        "image_digest": image_ref(),
        "strategy_file": str(strategy_file),
        "strategy_file_sha256": sha256_file(strategy_file),
        "data_manifest_sha256": _manifest_hash(manifest_path),
    }
