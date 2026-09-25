"""The H1 research pipeline: one step function per `python -m sq.research`
subcommand (see __main__.py), run inside the pinned Freqtrade image via the
`research` Compose service. Each step is a thin driver: build the exact
Freqtrade argv or data read h1.py's constants call for, run it, and record the
result under RECORD_DIR with provenance. Every backtest writes both
`<run>.json` (Freqtrade's metrics) and `mtm-<run>.json` (marked-to-market
metrics on the fixed notional, which decide H1's criteria).

Every step takes its paths as keyword arguments defaulting to h1.py's
constants, so callers (tests, in particular) can point a run at a temporary
directory without changing the Freqtrade argv the step builds.
"""

import json
import subprocess
import sys
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from freqtrade.data.btanalysis import load_backtest_data
from freqtrade.data.history import load_pair_history

from sq.research import data_manifest, h1, metrics, provenance
from sq.research import equity_curves as equity_curves_module
from sq.research import proxy_check as proxy_check_module


def _compact_date(date: str) -> str:
    """YYYY-MM-DD -> YYYYMMDD, as Freqtrade's --timerange expects."""
    return date.replace("-", "")


# --- backtesting -------------------------------------------------------


def _latest_result_zip(results_dir: Path) -> Path:
    """The result zip Freqtrade's `.last_result.json` points at in `results_dir`."""
    pointer = json.loads((results_dir / ".last_result.json").read_text())
    return results_dir / pointer["latest_backtest"]


def write_summary(
    run: h1.BacktestRun,
    results_dir: Path,
    *,
    record_dir: Path = h1.RECORD_DIR,
    manifest_path: Path = h1.MANIFEST,
) -> None:
    """Write `<record_dir>/<run.name>.json`: the backtest's metrics and
    provenance, from the latest result in `results_dir`."""
    zip_path = _latest_result_zip(results_dir)
    with zipfile.ZipFile(zip_path) as zf:
        payload = json.loads(zf.read(zip_path.stem + ".json"))
    strategy_stats = payload["strategy"][run.strategy]

    summary = {
        "metrics": metrics.backtest_metrics(strategy_stats),
        "provenance": provenance.build_summary_provenance(
            strategy_stats,
            period=run.period,
            fee=run.fee,
            strategy_file=run.strategy_file,
            manifest_path=manifest_path,
        ),
    }
    out = record_dir / f"{run.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {out}")
    print(json.dumps(summary["metrics"], indent=2, sort_keys=True))


def write_mtm(
    run: h1.BacktestRun,
    results_dir: Path,
    *,
    record_dir: Path = h1.RECORD_DIR,
    manifest_path: Path = h1.MANIFEST,
    datadir: Path = h1.DATADIR,
    wallet: float = h1.NOTIONAL,
) -> None:
    """Write `<record_dir>/mtm-<run.name>.json`: the marked-to-market report
    for the latest result in `results_dir`, with provenance."""
    zip_path = _latest_result_zip(results_dir)

    trades = load_backtest_data(zip_path)
    candles = load_pair_history(pair=run.pair, timeframe=h1.TIMEFRAME, datadir=datadir)
    # H1's period dates are inclusive, so the marked window runs through the
    # period's last day; the record's `end` is the window's exclusive bound
    # (the next day), which equity_curves.py reapplies to rebuild the curve.
    start = pd.Timestamp(run.start, tz="UTC")
    end = pd.Timestamp(run.end, tz="UTC") + pd.Timedelta(days=1)
    window = candles[(candles["date"] >= start) & (candles["date"] < end)]

    report = metrics.mtm_report(
        result=str(zip_path),
        pair=run.pair,
        start=run.start,
        end=end.strftime("%Y-%m-%d"),
        trades=trades,
        candles=window,
        wallet=wallet,
    )
    report["provenance"] = provenance.build_mtm_provenance(
        strategy_file=run.strategy_file, manifest_path=manifest_path
    )
    out = record_dir / f"mtm-{run.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Wrote {out}")


def backtest(
    run: h1.BacktestRun,
    *,
    config: Path = h1.CONFIG,
    backtest_root: Path = h1.BACKTEST_ROOT,
    record_dir: Path = h1.RECORD_DIR,
    manifest_path: Path = h1.MANIFEST,
    datadir: Path = h1.DATADIR,
) -> None:
    """Run one `freqtrade backtesting` invocation and record its summary and
    marked-to-market report."""
    results_dir = backtest_root / run.name
    # --backtest-directory only writes timestamped results *inside* the given
    # path if that path already exists as a directory; otherwise it treats it
    # as a filename prefix in its (possibly missing) parent. Pre-create it.
    results_dir.mkdir(parents=True, exist_ok=True)

    timerange = f"{_compact_date(run.start)}-{_compact_date(run.end)}"
    argv = [
        "freqtrade",
        "backtesting",
        "--config",
        str(config),
        "--strategy",
        run.strategy,
        "--strategy-path",
        str(run.strategy_path),
        "--timerange",
        timerange,
        "--fee",
        str(run.fee),
        "--pairs",
        run.pair,
        "--export",
        "trades",
        "--backtest-directory",
        str(results_dir),
    ]
    subprocess.run(argv, check=True)

    write_summary(run, results_dir, record_dir=record_dir, manifest_path=manifest_path)
    write_mtm(run, results_dir, record_dir=record_dir, manifest_path=manifest_path, datadir=datadir)


def train(**kwargs) -> None:
    for run in h1.TRAIN_RUNS:
        backtest(run, **kwargs)


def validation(**kwargs) -> None:
    for run in h1.VALIDATION_RUNS:
        backtest(run, **kwargs)


def sensitivity(**kwargs) -> None:
    # Sensitivity is reported once over the combined train+validation span
    # (H1.md: "on train + validation only"), never on the held-out period,
    # and never used to select a strategy.
    for run in h1.SENSITIVITY_RUNS:
        backtest(run, **kwargs)


def eth_robustness(**kwargs) -> None:
    for run in h1.ETH_ROBUSTNESS_RUNS:
        backtest(run, **kwargs)


def heldout(**kwargs) -> None:
    # DEFINED BUT MUST NOT BE RUN before the strategy is frozen; run once,
    # after the strategy is frozen (research/hypotheses/H1.md).
    print(
        "sq.research heldout: this evaluates H1's predeclared held-out",
        file=sys.stderr,
    )
    print(
        "period (2024-07-01 -> 2026-09-20). Run this once, only after the",
        file=sys.stderr,
    )
    print("strategy is frozen, per research/hypotheses/H1.md.", file=sys.stderr)
    for run in h1.HELDOUT_RUNS:
        backtest(run, **kwargs)


# --- bias checks ---------------------------------------------------------


def bias(*, config: Path = h1.CONFIG) -> None:
    """lookahead-analysis + recursive-analysis for H1 (train+validation)."""
    timerange = f"{_compact_date(h1.BIAS_START)}-{_compact_date(h1.VALIDATION_END)}"
    for subcommand in ("lookahead-analysis", "recursive-analysis"):
        argv = [
            "freqtrade",
            subcommand,
            "--config",
            str(config),
            "--strategy",
            "H1ChannelBreakout",
            "--timerange",
            timerange,
            "--pairs",
            h1.PAIR,
        ]
        subprocess.run(argv, check=True)


# --- benchmark, manifest, proxy check, equity curves ----------------------


def _load_close_series(
    datadir: Path, pair: str, timeframe: str, start: str, end: str
) -> pd.DataFrame:
    df = pd.read_feather(data_manifest.feather_path(datadir, pair, timeframe))
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")
    windowed = df[(df["date"] >= start_ts) & (df["date"] <= end_ts)]
    if windowed.empty:
        raise ValueError(f"No candles for {pair} {timeframe} in [{start}, {end}]")
    return windowed.sort_values("date").reset_index(drop=True)


def benchmark(
    period: str,
    pair: str = h1.PAIR,
    *,
    fee: float = h1.FEE_BASE,
    timeframe: str = h1.TIMEFRAME,
    datadir: Path = h1.DATADIR,
    record_dir: Path = h1.RECORD_DIR,
) -> dict:
    """Buy-and-hold return/drawdown for a predeclared period (default pair
    BTC/EUR); writes `<record_dir>/benchmark-<period>-<pair>.json`."""
    start, end = h1.BENCHMARK_PERIODS[period]
    candles = _load_close_series(datadir, pair, timeframe, start, end)
    result = metrics.buy_and_hold(
        pair=pair, timeframe=timeframe, start=start, end=end, candles=candles, fee=fee
    )
    result["generated_at"] = datetime.now(UTC).isoformat()

    out_pair = pair.replace("/", "_")
    out = record_dir / f"benchmark-{period}-{out_pair}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(f"Wrote {out}")
    print(json.dumps(result, indent=2, sort_keys=True))
    return result


def manifest(*, datadir: Path = h1.DATADIR, out: Path = h1.MANIFEST) -> dict:
    """Write research/data-manifest.json."""
    return data_manifest.write_manifest(datadir, out)


def proxy_check(*, datadir: Path = h1.DATADIR, record_dir: Path = h1.RECORD_DIR) -> int:
    """Validate the Binance-as-Kraken proxy (ADR-0002); returns nonzero if
    the gate fails (the result is still written)."""
    return proxy_check_module.run(datadir, record_dir / "proxy-check.json")


def equity_curves(
    *,
    experiment: Path = h1.RECORD_DIR,
    datadir: Path = h1.DATADIR,
    out: Path | None = None,
) -> None:
    """Daily equity curves of the recorded BTC/EUR runs for the Pages demo."""
    equity_curves_module.build_equity_curves(
        experiment, datadir, out or (experiment / "equity-curves.json")
    )
