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
  jev-evaluate                    Offline baseline-vs-filter comparison of Jev's
                                   recorded assessments
"""

import argparse
import sys

from sq.research import h1, jev_evaluation, pipeline


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

    jev_parser = subparsers.add_parser(
        "jev-evaluate", help="Offline baseline-vs-filter comparison of Jev's recorded assessments"
    )
    jev_evaluation.add_arguments(jev_parser)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.subcommand is None:
        parser.print_usage()
        return 2
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
