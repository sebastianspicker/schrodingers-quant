"""`python -m sq.research <subcommand>`: reproducible commands for the H1
hypothesis (research/hypotheses/H1.md), run inside the pinned Freqtrade image
via the `research` Compose service (`make research ARGS=<subcommand>`). The
experiment definition is h1.py; the steps are in pipeline.py.

Subcommands:
  manifest                        Write research/data-manifest.json
  proxy-check                     Validate the Binance-as-Kraken proxy (ADR-0002)
  bias                            lookahead-analysis + recursive-analysis for H1 (train+validation)
  train                           Backtest H1 on BTC/EUR, train period, base + stress fee
  validation                      Backtest H1 on BTC/EUR, validation period, base + stress fee
  h2-train                        H2 (vol-targeted sizing) on BTC/EUR, train period, base + stress
                                   fee; sanity only
  h2-validation                   H2 (vol-targeted sizing) on BTC/EUR, validation period, base +
                                   stress fee; sanity only
  sensitivity                     10/5 and 55/20 channel variants, train+validation span, base fee
  eth-robustness                  H1 on ETH/EUR, train+validation span, base fee
  heldout                         H1 on BTC/EUR, held-out period, base + stress fee.
                                   DEFINED BUT MUST NOT BE RUN before the strategy is frozen;
                                   Run it once, after the strategy is frozen
                                   (research/hypotheses/H1.md).
  benchmark <train|validation|heldout> [PAIR]
                                   Buy-and-hold return/drawdown for a period
                                   (default pair BTC/EUR)
  equity-curves                   Daily equity curves of the recorded BTC/EUR runs
                                   for the Pages demo
  stats                           Statistical assessment of the recorded equity curves
                                   (bootstrap intervals, random-timing benchmark, ...);
                                   runs without the image: make stats
  physics                         Physics-informed assessment of the record (stylized facts,
                                   surrogate and model nulls, forward-protocol calibration,
                                   growth, trials ledger); no image: make physics
  jev-evaluate                    Offline baseline-vs-filter comparison of Jev's
                                   recorded assessments
"""

import argparse
import sys
from pathlib import Path

from sq.research import h1, h2

# `pipeline` imports Freqtrade and is loaded only by the subcommands that need
# it, so `stats` works on a development machine without the image.


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sq.research",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="subcommand")

    subparsers.add_parser("manifest", help="Write research/data-manifest.json")
    subparsers.add_parser("proxy-check", help="Validate the Binance-as-Kraken proxy (ADR-0002)")
    subparsers.add_parser(
        "bias", help="lookahead-analysis + recursive-analysis for H1 (train+validation)"
    )
    subparsers.add_parser("train", help="Backtest H1 on BTC/EUR, train period, base + stress fee")
    subparsers.add_parser(
        "validation", help="Backtest H1 on BTC/EUR, validation period, base + stress fee"
    )
    subparsers.add_parser(
        "h2-train",
        help="H2 (vol-targeted sizing) on BTC/EUR, train period, base + stress fee; sanity only",
    )
    subparsers.add_parser(
        "h2-validation",
        help="H2 (vol-targeted sizing) on BTC/EUR, validation period, base + stress fee; "
        "sanity only",
    )
    subparsers.add_parser(
        "sensitivity", help="10/5 and 55/20 channel variants, train+validation span, base fee"
    )
    subparsers.add_parser("eth-robustness", help="H1 on ETH/EUR, train+validation span, base fee")
    subparsers.add_parser(
        "heldout",
        help="H1 on BTC/EUR, held-out period, base + stress fee. Run once, after the "
        "strategy is frozen.",
    )

    benchmark_parser = subparsers.add_parser(
        "benchmark", help="Buy-and-hold return/drawdown for a period (default pair BTC/EUR)"
    )
    benchmark_parser.add_argument("period", choices=list(h1.BENCHMARK_PERIODS))
    benchmark_parser.add_argument("pair", nargs="?", default=h1.PAIR)

    subparsers.add_parser(
        "equity-curves", help="Daily equity curves of the recorded BTC/EUR runs for the Pages demo"
    )

    stats_parser = subparsers.add_parser(
        "stats", help="Statistical assessment of the recorded equity curves (no image needed)"
    )
    stats_parser.add_argument(
        "--experiment",
        type=Path,
        default=default_experiment_dir(),
        help="Experiment directory holding equity-curves.json (default: H1's)",
    )
    stats_parser.add_argument(
        "--out", type=Path, help="Output path (default: <experiment>/statistics.json)"
    )

    physics_parser = subparsers.add_parser(
        "physics", help="Physics-informed assessment of the recorded experiment (no image needed)"
    )
    physics_parser.add_argument("--experiment", type=Path, default=default_experiment_dir())
    physics_parser.add_argument("--out", type=Path, help="Output path (default: physics.json)")
    physics_parser.add_argument("--null-trials", type=int, default=None)
    physics_parser.add_argument("--calibration-trials", type=int, default=None)
    physics_parser.add_argument("--first-passage-trials", type=int, default=None)

    jev_parser = subparsers.add_parser(
        "jev-evaluate", help="Offline baseline-vs-filter comparison of Jev's recorded assessments"
    )
    from sq.research import jev_evaluation

    jev_evaluation.add_arguments(jev_parser)

    return parser


def default_experiment_dir() -> Path:
    """H1's record directory: the container path when it exists (the
    `research` service), else the path inside this checkout."""
    if h1.RECORD_DIR.exists():
        return h1.RECORD_DIR
    return Path(__file__).resolve().parents[3] / "research" / "experiments" / "H1"


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.subcommand is None:
        parser.print_usage()
        return 2
    if args.subcommand == "stats":
        from sq.research import statistics

        statistics.write_statistics(args.experiment, args.out)
        return 0
    if args.subcommand == "physics":
        from sq.research import physics

        overrides = {
            key: value
            for key, value in (
                ("null_trials", args.null_trials),
                ("calibration_trials", args.calibration_trials),
                ("first_passage_trials", args.first_passage_trials),
            )
            if value is not None
        }
        physics.write_physics(args.experiment, args.out, **overrides)
        return 0

    from sq.research import jev_evaluation, pipeline

    if args.subcommand == "manifest":
        pipeline.manifest()
    elif args.subcommand == "proxy-check":
        return pipeline.proxy_check()
    elif args.subcommand == "bias":
        pipeline.bias()
    elif args.subcommand == "train":
        pipeline.train()
    elif args.subcommand == "validation":
        pipeline.validation()
    elif args.subcommand == "h2-train":
        for run in h2.TRAIN_RUNS:
            pipeline.backtest(run, record_dir=h2.RECORD_DIR)
    elif args.subcommand == "h2-validation":
        for run in h2.VALIDATION_RUNS:
            pipeline.backtest(run, record_dir=h2.RECORD_DIR)
    elif args.subcommand == "sensitivity":
        pipeline.sensitivity()
    elif args.subcommand == "eth-robustness":
        pipeline.eth_robustness()
    elif args.subcommand == "heldout":
        pipeline.heldout()
    elif args.subcommand == "benchmark":
        pipeline.benchmark(args.period, args.pair)
    elif args.subcommand == "equity-curves":
        pipeline.equity_curves()
    elif args.subcommand == "jev-evaluate":
        jev_evaluation.run(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
