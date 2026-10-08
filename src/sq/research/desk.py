"""Offline research decision desk. Standard library only; never launches a backtest.

The sleeve transformation scales recorded fixed-stake P&L, not daily returns.
Daily marks are augmented by initial capital, without reconstructing 4h prices.
"""

import argparse
import csv
import hashlib
import json
import math
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MAX_BYTES = 32 * 1024 * 1024
MAX_RUNS = 32
MAX_DAYS = 20_000
SLEEVES = (0.25, 0.5, 0.75, 1.0)
CAPITALS = (1000, 5000, 10_000)


def number(value: object, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: expected a number")
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name}: must be finite and between {low} and {high}")
    return float(value)


def read_json(path: Path) -> tuple[dict, str]:
    with path.open("rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError(f"{path.name}: exceeds {MAX_BYTES} bytes")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: expected an object")
    return value, hashlib.sha256(raw).hexdigest()


def validate_run(run: dict, name: str) -> None:
    start, end = date.fromisoformat(run["start"]), date.fromisoformat(run["end"])
    days = (end - start).days
    if not 1 <= days <= MAX_DAYS:
        raise ValueError(f"{name}: period must span 1..{MAX_DAYS} days")
    dates = run["dates"]
    if not isinstance(dates, list) or not 1 <= len(dates) <= MAX_DAYS:
        raise ValueError(f"{name}: invalid daily mark count")
    parsed = [date.fromisoformat(d) for d in dates]
    if parsed[0] < start or parsed[-1] != end - timedelta(days=1):
        raise ValueError(f"{name}: marks must be inside period and reach its final day")
    if any(b - a != timedelta(days=1) for a, b in zip(parsed, parsed[1:], strict=False)):
        raise ValueError(f"{name}: daily marks must be consecutive and ordered")
    for series in ("strategy", "buy_hold"):
        values = run[series]
        if not isinstance(values, list) or len(values) != len(dates):
            raise ValueError(f"{name}: {series} must align with dates")
        for value in values:
            number(value, f"{name}.{series}", 0.000001, 1e15)
    for key in ("mtm_max_drawdown_pct", "buy_hold_max_drawdown_pct"):
        number(run[key], f"{name}.{key}", 0, 100)
    trades = run["trades"]
    if not isinstance(trades, list) or len(trades) > MAX_DAYS:
        raise ValueError(f"{name}: invalid trade count")
    for trade in trades:
        number(trade["return_pct"], f"{name}.trade return", -100, 1e9)


def curve_risk(values: list[float], dates: list[str], start: str, initial: float) -> dict:
    """Risk on daily end marks, with initial equity at period start.

    Duration is elapsed calendar days from the most recent peak to each
    underwater mark. Recovery-day equality resets it. An initial negative
    first day therefore counts as one day, and unrecovered episodes count.
    """
    peak = initial
    peak_boundary = date.fromisoformat(start)
    longest = current_days = 0
    maximum = current = 0.0
    for value, day in zip(values, dates, strict=True):
        boundary = date.fromisoformat(day) + timedelta(days=1)
        if value >= peak:
            peak = value
            peak_boundary = boundary
            current_days = 0
        else:
            current_days = (boundary - peak_boundary).days
            longest = max(longest, current_days)
        current = 100 * (peak - value) / peak
        maximum = max(maximum, current)
    return {
        "return_pct": 100 * (values[-1] / initial - 1),
        "max_drawdown_pct_daily": maximum,
        "current_drawdown_pct_daily": current,
        "longest_underwater_days": longest,
        "current_underwater_days": current_days,
    }


def monthly_returns(run: dict, notional: float) -> list[dict]:
    """Monthly close-to-close returns; first interval starts at initial capital.

    Excess is a percentage-point difference, not causal alpha attribution.
    The first/last months and missing early daily marks are disclosed.
    """
    groups: dict[str, list[int]] = {}
    for i, day in enumerate(run["dates"]):
        groups.setdefault(day[:7], []).append(i)
    result = []
    previous_s = previous_b = notional
    previous_boundary = run["start"]
    for month, indices in groups.items():
        i = indices[-1]
        last = date.fromisoformat(run["dates"][i])
        next_day = last + timedelta(days=1)
        month_start = f"{month}-01"
        partial = previous_boundary != month_start or next_day.day != 1
        s, b = run["strategy"][i], run["buy_hold"][i]
        strategy_pct = 100 * (s / previous_s - 1)
        hold_pct = 100 * (b / previous_b - 1)
        result.append(
            {
                "month": month,
                "from_boundary": previous_boundary,
                "through": last.isoformat(),
                "partial_month": partial,
                "incomplete_daily_coverage": len(indices)
                != (next_day - date.fromisoformat(previous_boundary)).days,
                "daily_marks": len(indices),
                "strategy_pct": strategy_pct,
                "buy_hold_pct": hold_pct,
                "excess_pp": strategy_pct - hold_pct,
            }
        )
        previous_s, previous_b = s, b
        previous_boundary = next_day.isoformat()
    return result


def concentration(trades: list[dict]) -> dict:
    returns = [t["return_pct"] for t in trades]
    winners = sorted((r for r in returns if r > 0), reverse=True)[:2]
    total = math.fsum(returns)
    top = math.fsum(winners)
    return {
        "trade_count": len(returns),
        "top_winners_removed": len(winners),
        "rounded_trade_sum_pp": total,
        "top_two_winners_pp": top,
        "without_top_two_winners_pp": total - top,
        "method": "Descriptive sum of rounded net trade returns on fixed stake; "
        "not a simulated equity path, executable trade deletion, or account return.",
    }


def sleeve_scenarios(run: dict, notional: float, monthly_cost_eur: float) -> list[dict]:
    years = (date.fromisoformat(run["end"]) - date.fromisoformat(run["start"])).days / 365
    result = []
    for fraction in SLEEVES:
        values = [notional + fraction * (v - notional) for v in run["strategy"]]
        risk = curve_risk(values, run["dates"], run["start"], notional)
        annual_cash_rate = risk["return_pct"] / 100 / years
        cost = monthly_cost_eur * 12
        break_even = cost / annual_cash_rate if annual_cash_rate > 0 else None
        result.append(
            {
                "strategy_sleeve_pct": fraction * 100,
                "idle_cash_pct": (1 - fraction) * 100,
                **risk,
                "observed_average_annual_cash_return_pct": annual_cash_rate * 100,
                "annual_cost_eur": cost,
                "historical_cost_break_even_capital_eur": break_even,
                "capital_sensitivity": [
                    {
                        "capital_eur": capital,
                        "average_annual_cash_profit_eur": capital * annual_cash_rate,
                        "average_annual_cash_after_vps_eur": capital * annual_cash_rate - cost,
                    }
                    for capital in CAPITALS
                ],
            }
        )
    return result


def evidence(stats: dict) -> dict:
    """Consume existing bounded assessment; no new resampling or significance claims."""
    mean_ci = stats["bootstrap_trades"]["mean_ci95_pct"]
    sharpe_ci = stats["bootstrap_sharpe"]["sharpe_ci95"]
    for name, interval in (("mean trade CI", mean_ci), ("Sharpe CI", sharpe_ci)):
        if not isinstance(interval, list) or len(interval) != 2:
            raise ValueError(f"{name}: expected two bounds")
        for bound in interval:
            number(bound, name, -1e12, 1e12)
        if interval[0] > interval[1]:
            raise ValueError(f"{name}: bounds are reversed")
    timing = number(stats["random_timing"]["p_return_ge_actual"], "random timing tail", 0, 1)
    return {
        "mean_trade_ci95_pct": mean_ci,
        "daily_sharpe_ci95": sharpe_ci,
        "random_timing_share_return_ge_actual": timing,
        "mean_trade_ci_includes_zero": mean_ci[0] <= 0 <= mean_ci[1],
        "daily_sharpe_ci_includes_zero": sharpe_ci[0] <= 0 <= sharpe_ci[1],
        "qualification": "Archived bootstrap intervals and random timing are descriptive "
        "uncertainty checks; they do not establish edge or approve capital allocation.",
    }


def build_report(experiment: Path, monthly_cost_eur: float = 0) -> dict:
    monthly_cost_eur = number(monthly_cost_eur, "monthly cost EUR", 0, 1e6)
    curves, curves_hash = read_json(experiment / "equity-curves.json")
    stats, stats_hash = read_json(experiment / "statistics.json")
    if stats.get("source_sha256") != curves_hash:
        raise ValueError(
            "statistics source hash differs from equity-curves.json; rebuild statistics"
        )
    # The growth block comes from the tracked physics.json (make physics, numpy),
    # so the desk itself stays standard-library only; it is optional, but a stale
    # file is refused like stale statistics.
    physics, physics_hash = {}, None
    physics_path = experiment / "physics.json"
    if physics_path.exists():
        physics, physics_hash = read_json(physics_path)
        if physics.get("equity_curves_sha256") != curves_hash:
            raise ValueError("physics source hash differs from equity-curves.json; rebuild physics")
        if not isinstance(physics.get("growth"), dict):
            raise ValueError("physics.json has no growth block; rebuild physics")
    if curves.get("timeframe", "4h") != "4h":
        raise ValueError("desk expects the archived 4h experiment contract")
    notional = number(curves["notional_eur"], "notional EUR", 0.000001, 1e12)
    runs = curves["runs"]
    if not isinstance(runs, dict) or not 1 <= len(runs) <= MAX_RUNS:
        raise ValueError(f"expected 1..{MAX_RUNS} runs")
    if runs.keys() != stats["runs"].keys():
        raise ValueError("equity and statistics run sets differ")
    report = {
        "schema_version": 1,
        "experiment": experiment.name,
        "pair": curves["pair"],
        "initial_notional_eur": notional,
        "source_sha256": curves_hash,
        "statistics_sha256": stats_hash,
        "physics_sha256": physics_hash,
        "monthly_cost_eur": monthly_cost_eur,
        "methods": {
            "risk": "DAILY end marks plus initial capital; 4h recorded drawdowns remain "
            "authoritative. Durations are calendar days from last peak to underwater marks.",
            "sleeves": "Retrospective constant fixed-stake P&L scaling: "
            "equity = initial + sleeve * (recorded equity - initial); remaining cash earns zero. "
            "No rebalancing, compounding stake, optimizer, or new fee simulation.",
            "cost": "Annual cash cost = monthly EUR cost * 12. Break-even = annual cost / "
            "(sleeve period profit / elapsed years). This is an observed simple cash rate, "
            "not CAGR or a future expected return; null means no positive historical cash rate. "
            "Sensitivity uses hypothetical capital, linear fills/costs, no taxes or cash yield.",
            "growth": "Read from the tracked physics.json (make physics): time-average (log) "
            "growth against the ensemble mean per trade; Kelly fraction capped at 1 (no "
            "leverage) with a trade bootstrap; compounding frontier on the daily marks. "
            "Retrospective rescaling of the recorded path, not a new backtest.",
            "months": "Monthly close-to-close percent returns; first starts at initial capital. "
            "Excess is strategy minus buy-and-hold in percentage points, not estimated alpha. "
            "Multiply monthly growth factors to reconcile cumulative return; do not sum them.",
        },
        "contamination": "H1's held-out period was evaluated twice after a stake-sizing "
        "correction; its first compounding run failed K2. The corrected fixed-stake run "
        "preserves the declared strategy but the window is spent. These diagnostics were "
        "added after inspecting the record. Fresh evidence requires predeclared forward data."
        if experiment.name == "H1"
        else "These diagnostics use inspected historical results. Review the experiment record "
        "for selection, reruns and contamination; fresh validation requires new predeclared data.",
        "runs": {},
    }
    for name, run in runs.items():
        validate_run(run, name)
        report["runs"][name] = {
            "start": run["start"],
            "end_exclusive": run["end"],
            "first_daily_mark": run["dates"][0],
            "daily_marks": len(run["dates"]),
            "strategy": curve_risk(run["strategy"], run["dates"], run["start"], notional),
            "buy_hold": curve_risk(run["buy_hold"], run["dates"], run["start"], notional),
            "recorded_strategy_drawdown_pct_4h": run["mtm_max_drawdown_pct"],
            "recorded_buy_hold_drawdown_pct_4h": run["buy_hold_max_drawdown_pct"],
            "concentration": concentration(run["trades"]),
            "evidence": evidence(stats["runs"][name]),
            "sleeves": sleeve_scenarios(run, notional, monthly_cost_eur),
            "monthly": monthly_returns(run, notional),
            "growth": physics.get("growth", {}).get(name) if physics else None,
        }
    return report


def fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:,.2f}"


def markdown(report: dict) -> str:
    lines = [
        f"# {report['experiment']} research decision desk — {report['pair']}",
        "",
        "Retrospective risk and operational economics. This report does not establish an edge "
        "or approve a capital allocation.",
        "",
        report["contamination"],
        "",
        f"Initial archived stake: €{fmt(report['initial_notional_eur'])}. "
        f"User-supplied monthly VPS cost: €{fmt(report['monthly_cost_eur'])}.",
        "",
    ]
    for name, run in report["runs"].items():
        s, b = run["strategy"], run["buy_hold"]
        e, c = run["evidence"], run["concentration"]
        lines += [
            f"## {name}: {run['start']} → {run['end_exclusive']} (end exclusive)",
            "",
            f"Curve return {fmt(s['return_pct'])}%; B&H {fmt(b['return_pct'])}%. "
            f"Authoritative recorded 4h max DD {fmt(run['recorded_strategy_drawdown_pct_4h'])}% "
            f"(B&H {fmt(run['recorded_buy_hold_drawdown_pct_4h'])}%).",
            f"DAILY max DD including initial equity {fmt(s['max_drawdown_pct_daily'])}%; "
            f"current DD {fmt(s['current_drawdown_pct_daily'])}%; longest underwater "
            f"{s['longest_underwater_days']} days; current {s['current_underwater_days']} days. "
            f"B&H DAILY max DD {fmt(b['max_drawdown_pct_daily'])}%.",
            f"{run['daily_marks']} daily marks, first {run['first_daily_mark']}. "
            "Early missing marks are not filled; risk inside unobserved days is unknown.",
            "",
            f"{c['trade_count']} trades. Rounded trade-return sum {fmt(c['rounded_trade_sum_pp'])} "
            f"pp; removing the largest {c['top_winners_removed']} winners leaves "
            f"{fmt(c['without_top_two_winners_pp'])} pp. {c['method']}",
            "",
            f"Mean trade 95% bootstrap CI {e['mean_trade_ci95_pct']}; daily Sharpe 95% "
            f"block-bootstrap CI {e['daily_sharpe_ci95']}. Random timing trials matching or "
            f"exceeding actual return: {100 * e['random_timing_share_return_ge_actual']:.2f}%. "
            "A tail share is not the probability the strategy has no edge.",
            "",
            "### Fixed-stake sleeve and idle cash scenarios",
            "",
            "| Strategy / cash | Return % | DAILY max DD % | Annual cash rate % | "
            "Cost break-even capital € | Annual cash after VPS at €1k / €5k / €10k |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
        for sleeve in run["sleeves"]:
            sensitivity = " / ".join(
                fmt(v["average_annual_cash_after_vps_eur"]) for v in sleeve["capital_sensitivity"]
            )
            lines.append(
                f"| {sleeve['strategy_sleeve_pct']:.0f}% / {sleeve['idle_cash_pct']:.0f}% "
                f"| {fmt(sleeve['return_pct'])} | {fmt(sleeve['max_drawdown_pct_daily'])} "
                f"| {fmt(sleeve['observed_average_annual_cash_return_pct'])} "
                f"| {fmt(sleeve['historical_cost_break_even_capital_eur'])} | {sensitivity} |"
            )
        lines += [
            "",
            "### Monthly strategy and buy-and-hold returns",
            "",
            "| Month | Partial | Strategy % | B&H % | Excess pp |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
        for month in run["monthly"]:
            lines.append(
                f"| {month['month']} | {'yes' if month['partial_month'] else 'no'} "
                f"| {fmt(month['strategy_pct'])} | {fmt(month['buy_hold_pct'])} "
                f"| {fmt(month['excess_pp'])} |"
            )
        growth = run["growth"]
        lines += ["", "### Growth and sizing", ""]
        if growth is None:
            lines.append(
                "Not available: physics.json is missing for this experiment (make physics)."
            )
            lines.append("")
            continue
        kelly = growth["trade_level"]["kelly"]
        lines += [
            f"Per trade: mean {fmt(growth['trade_level']['mean_pct'])}%, time-average growth "
            f"{fmt(growth['trade_level']['time_average_growth_pct'])}% "
            f"(volatility drag {fmt(growth['trade_level']['volatility_drag_pct'])} pp).",
        ]
        if kelly["kelly_fraction"] is None:
            lines.append("Kelly fraction: n/a (fewer than two trades).")
        else:
            lines.append(
                f"Kelly fraction {kelly['kelly_fraction']:.2f} (no leverage; unconstrained "
                f"{kelly['kelly_fraction_unconstrained']:.2f}), 95% bootstrap interval "
                f"{kelly['kelly_ci95']}; {100 * kelly['share_resamples_zero']:.1f}% of "
                "resamples say do not trade."
            )
        lines += [
            growth["qualification"],
            "",
            "| Fraction | Annualised growth % | Compounding max DD % |",
            "| ---: | ---: | ---: |",
        ]
        for point in growth["frontier"]:
            lines.append(
                f"| {point['fraction']:.2f} | {fmt(point['annualised_growth_pct'])} "
                f"| {fmt(point['max_drawdown_pct'])} |"
            )
        lines.append("")
    lines += ["## Methods and provenance", ""]
    for name, method in report["methods"].items():
        lines += [f"**{name}:** {method}", ""]
    lines += [
        f"Equity input SHA-256: `{report['source_sha256']}`.",
        f"Statistics input SHA-256: `{report['statistics_sha256']}`.",
        "Statistics are accepted only when their source hash matches the equity input.",
        "",
    ]
    return "\n".join(lines)


def write_report(report: dict, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    (out_dir / "report.md").write_text(markdown(report))
    rows = [{"run": name, **month} for name, r in report["runs"].items() for month in r["monthly"]]
    with (out_dir / "monthly-returns.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def terminal_summary(report: dict) -> str:
    lines = [f"{report['experiment']} / {report['pair']} — retrospective research desk"]
    for name, run in report["runs"].items():
        s = run["strategy"]
        lines.append(
            f"{name:18} return {s['return_pct']:7.2f}% | 4h DD "
            f"{run['recorded_strategy_drawdown_pct_4h']:6.2f}% | DAILY DD "
            f"{s['max_drawdown_pct_daily']:6.2f}% | underwater "
            f"{s['current_underwater_days']}d current / {s['longest_underwater_days']}d longest"
        )
    focus = report["runs"].get("heldout-base", next(iter(report["runs"].values())))
    e = focus["evidence"]
    lines += [
        f"Focus: mean-trade 95% CI {e['mean_trade_ci95_pct']}; random timing ≥ actual "
        f"{100 * e['random_timing_share_return_ge_actual']:.2f}%.",
        f"Without top two winners: {focus['concentration']['without_top_two_winners_pp']:.2f} "
        "pp (descriptive rounded trade sum).",
        f"VPS cost €{report['monthly_cost_eur']:.2f}/month. Sleeve economics and monthly "
        "attribution are in the report. No edge or capital approval inferred.",
        report["contamination"],
    ]
    for sleeve in focus["sleeves"]:
        lines.append(
            f"Focus sleeve {sleeve['strategy_sleeve_pct']:.0f}%: return "
            f"{sleeve['return_pct']:.2f}%, DAILY DD {sleeve['max_drawdown_pct_daily']:.2f}%, "
            f"historical VPS break-even capital "
            f"€{fmt(sleeve['historical_cost_break_even_capital_eur'])}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, default=ROOT / "research/experiments/H1")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "build/desk")
    parser.add_argument("--monthly-cost-eur", type=float, default=0)
    args = parser.parse_args(argv)
    try:
        report = build_report(args.experiment, args.monthly_cost_eur)
        write_report(report, args.out_dir)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.error(str(exc))
    print(terminal_summary(report))
    print(f"Wrote {args.out_dir}: report.md, report.json, monthly-returns.csv")


if __name__ == "__main__":
    main()
