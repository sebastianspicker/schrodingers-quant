#!/bin/sh
# research/run.sh — reproducible commands for the H1 hypothesis
# (research/hypotheses/H1.md), run inside the pinned Freqtrade image via the
# `research` Compose service (compose.yaml; the pinned image digest lives in
# exactly one place there, x-freqtrade-image).
#
# Usage: research/run.sh <subcommand> [args...]
#
# Subcommands:
#   manifest                        Write research/data-manifest.json
#   proxy-check                     Validate the Binance-as-Kraken proxy (ADR-0002)
#   bias                            lookahead-analysis + recursive-analysis for H1 (train+validation)
#   train                           Backtest H1 on BTC/EUR, train period, base + stress fee
#   validation                      Backtest H1 on BTC/EUR, validation period, base + stress fee
#   sensitivity                     10/5 and 55/20 channel variants, train+validation span, base fee
#   eth-robustness                  H1 on ETH/EUR, train+validation span, base fee
#   heldout                         H1 on BTC/EUR, held-out period, base + stress fee.
#                                   DEFINED BUT MUST NOT BE RUN before the strategy is frozen;
#                                   Run it once, after the strategy is frozen (research/hypotheses/H1.md).
#   benchmark <train|validation|heldout> [PAIR]
#                                   Buy-and-hold return/drawdown for a period (default pair BTC/EUR)
#   equity-curves                   Daily equity curves of the recorded BTC/EUR runs for the Pages demo

set -eu

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
ROOT_DIR=$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)
CONFIG="/freqtrade/research/configs/backtest.json"

TRAIN_START="2020-01-01"
TRAIN_END="2022-12-31"
VALIDATION_START="2023-01-01"
VALIDATION_END="2024-06-30"
HELDOUT_START="2024-07-01"
HELDOUT_END="2026-09-20"

FEE_BASE="0.005"
FEE_STRESS="0.01"

# --- compose wrappers ---------------------------------------------------

docker_freqtrade() {
    # cd, not --project-directory: Compose resolves compose.yaml, .env and
    # COMPOSE_FILE relative to the current working directory, not
    # --project-directory (see ops/backup.sh for the same pattern).
    (cd "$ROOT_DIR" && docker compose run --rm research freqtrade "$@")
}

docker_python() {
    module="$1"
    shift
    (cd "$ROOT_DIR" && docker compose run --rm research python -m "sq.research.${module%.py}" "$@")
}

# YYYY-MM-DD -> YYYYMMDD
compact_date() {
    printf '%s' "$1" | tr -d '-'
}

# --- subcommands ---------------------------------------------------

cmd_manifest() {
    docker_python data_manifest \
        --datadir /freqtrade/user_data/data/binance \
        --out /freqtrade/research/data-manifest.json
}

cmd_proxy_check() {
    docker_python proxy_check \
        --datadir /freqtrade/user_data/data/binance \
        --out /freqtrade/research/experiments/H1/proxy-check.json
}

cmd_bias() {
    # lookahead-analysis/recursive-analysis probe a short window right at the
    # timerange start; the Binance BTC/EUR feed only begins 2020-01-03, so
    # start a few days later to give that probe window real candles. This
    # only affects the bias check's internal window, not any reported period.
    bias_start="2020-01-10"
    timerange="$(compact_date "$bias_start")-$(compact_date "$VALIDATION_END")"
    docker_freqtrade lookahead-analysis \
        --config "$CONFIG" \
        --strategy H1ChannelBreakout \
        --timerange "$timerange" \
        --pairs BTC/EUR
    docker_freqtrade recursive-analysis \
        --config "$CONFIG" \
        --strategy H1ChannelBreakout \
        --timerange "$timerange" \
        --pairs BTC/EUR
}

# backtest_and_summarize <period> <pair> <strategy> <strategy_path> <strategy_file> \
#                         <start> <end> <fee> <out_name>
backtest_and_summarize() {
    period="$1"
    pair="$2"
    strategy="$3"
    strategy_path="$4"
    strategy_file="$5"
    start="$6"
    end="$7"
    fee="$8"
    out_name="$9"

    timerange="$(compact_date "$start")-$(compact_date "$end")"
    results_dir="/freqtrade/user_data/backtest_results/H1/$out_name"

    # --backtest-directory only writes timestamped results *inside* the given
    # path if that path already exists as a directory; otherwise it treats it
    # as a filename prefix in its (possibly missing) parent. Pre-create it.
    mkdir -p "$ROOT_DIR/user_data/backtest_results/H1/$out_name"

    docker_freqtrade backtesting \
        --config "$CONFIG" \
        --strategy "$strategy" \
        --strategy-path "$strategy_path" \
        --timerange "$timerange" \
        --fee "$fee" \
        --pairs "$pair" \
        --export trades \
        --backtest-directory "$results_dir"

    docker_python backtest_summary \
        --results-dir "$results_dir" \
        --strategy "$strategy" \
        --period "$period" \
        --fee "$fee" \
        --strategy-file "$strategy_file" \
        --manifest /freqtrade/research/data-manifest.json \
        --out "/freqtrade/research/experiments/H1/$out_name.json"
}

cmd_train() {
    backtest_and_summarize train BTC/EUR H1ChannelBreakout /freqtrade/strategies \
        /freqtrade/strategies/H1ChannelBreakout.py \
        "$TRAIN_START" "$TRAIN_END" "$FEE_BASE" train-base-BTC_EUR
    backtest_and_summarize train BTC/EUR H1ChannelBreakout /freqtrade/strategies \
        /freqtrade/strategies/H1ChannelBreakout.py \
        "$TRAIN_START" "$TRAIN_END" "$FEE_STRESS" train-stress-BTC_EUR
}

cmd_validation() {
    backtest_and_summarize validation BTC/EUR H1ChannelBreakout /freqtrade/strategies \
        /freqtrade/strategies/H1ChannelBreakout.py \
        "$VALIDATION_START" "$VALIDATION_END" "$FEE_BASE" validation-base-BTC_EUR
    backtest_and_summarize validation BTC/EUR H1ChannelBreakout /freqtrade/strategies \
        /freqtrade/strategies/H1ChannelBreakout.py \
        "$VALIDATION_START" "$VALIDATION_END" "$FEE_STRESS" validation-stress-BTC_EUR
}

# Sensitivity and the ETH robustness check are reported once over the
# combined train+validation span (H1.md: "on train + validation only"), never
# on the held-out period, and never used to select a strategy.
cmd_sensitivity() {
    backtest_and_summarize sensitivity-10x5 BTC/EUR H1Sensitivity10x5 \
        /freqtrade/research/strategies /freqtrade/research/strategies/H1Sensitivity.py \
        "$TRAIN_START" "$VALIDATION_END" "$FEE_BASE" sensitivity-10x5-BTC_EUR
    backtest_and_summarize sensitivity-55x20 BTC/EUR H1Sensitivity55x20 \
        /freqtrade/research/strategies /freqtrade/research/strategies/H1Sensitivity.py \
        "$TRAIN_START" "$VALIDATION_END" "$FEE_BASE" sensitivity-55x20-BTC_EUR
}

cmd_eth_robustness() {
    backtest_and_summarize eth-robustness ETH/EUR H1ChannelBreakout /freqtrade/strategies \
        /freqtrade/strategies/H1ChannelBreakout.py \
        "$TRAIN_START" "$VALIDATION_END" "$FEE_BASE" eth-robustness-train-validation
}

cmd_heldout() {
    echo "research/run.sh heldout: this evaluates H1's predeclared held-out" >&2
    echo "period (2024-07-01 -> 2026-09-20). Run this once, only after the" >&2
    echo "strategy is frozen, per research/hypotheses/H1.md." >&2
    backtest_and_summarize heldout BTC/EUR H1ChannelBreakout /freqtrade/strategies \
        /freqtrade/strategies/H1ChannelBreakout.py \
        "$HELDOUT_START" "$HELDOUT_END" "$FEE_BASE" heldout-base-BTC_EUR
    backtest_and_summarize heldout BTC/EUR H1ChannelBreakout /freqtrade/strategies \
        /freqtrade/strategies/H1ChannelBreakout.py \
        "$HELDOUT_START" "$HELDOUT_END" "$FEE_STRESS" heldout-stress-BTC_EUR
}

cmd_benchmark() {
    period_name="${1:-}"
    pair="${2:-BTC/EUR}"
    case "$period_name" in
        train)
            start="$TRAIN_START"
            end="$TRAIN_END"
            ;;
        validation)
            start="$VALIDATION_START"
            end="$VALIDATION_END"
            ;;
        heldout)
            start="$HELDOUT_START"
            end="$HELDOUT_END"
            ;;
        *)
            echo "benchmark: unknown period '$period_name' (want train|validation|heldout)" >&2
            exit 2
            ;;
    esac

    out_pair=$(printf '%s' "$pair" | tr '/' '_')
    docker_python benchmark \
        --pair "$pair" \
        --timeframe 4h \
        --start "$start" \
        --end "$end" \
        --fee "$FEE_BASE" \
        --out "/freqtrade/research/experiments/H1/benchmark-$period_name-$out_pair.json"
}

cmd_equity_curves() {
    docker_python equity_curves --out /freqtrade/research/experiments/H1/equity-curves.json
}

usage() {
    sed -n '2,22p' "$0"
}

main() {
    subcommand="${1:-}"
    [ "$#" -gt 0 ] && shift

    case "$subcommand" in
        manifest) cmd_manifest ;;
        proxy-check) cmd_proxy_check ;;
        bias) cmd_bias ;;
        train) cmd_train ;;
        validation) cmd_validation ;;
        sensitivity) cmd_sensitivity ;;
        eth-robustness) cmd_eth_robustness ;;
        heldout) cmd_heldout ;;
        benchmark) cmd_benchmark "$@" ;;
        equity-curves) cmd_equity_curves ;;
        *)
            usage
            exit 2
            ;;
    esac
}

main "$@"
