"""Reproduce the public FRC campaign accounting and plots without private logs."""

from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "research/campaigns/frc-48h-20261014"
ASSETS = ROOT / "docs/research/assets"
TOKEN_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")


def token_cost(tokens, rates, *, long_context=False, cache_write=False):
    """Cache writes substitute for uncached input; they are not an added fee."""
    input_rate = rates["cache_write" if cache_write else "uncached_input"]
    input_multiplier = rates["long_input_multiplier"] if long_context else 1
    output_multiplier = rates["long_output_multiplier"] if long_context else 1
    return (
        (tokens["input_tokens"] - tokens["cached_input_tokens"]) * input_rate * input_multiplier
        + tokens["cached_input_tokens"] * rates["cached_input"] * input_multiplier
        + tokens["output_tokens"] * rates["output"] * output_multiplier
    ) / 1_000_000


def union_seconds(jobs):
    intervals = sorted(
        (j["receipt_started_seconds"], j["receipt_finished_seconds"])
        for j in jobs
        if j["receipt_started_seconds"] is not None and j["receipt_finished_seconds"] is not None
    )
    merged = []
    for start, finish in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(finish, merged[-1][1])
        else:
            merged.append([start, finish])
    return sum(finish - start for start, finish in merged)


def load_and_check():
    metrics = json.loads((DATA / "metrics.json").read_text())
    with (DATA / "worker-requests.csv").open(newline="") as stream:
        requests = [
            {k: int(v) if k != "elapsed_hours" else float(v) for k, v in row.items()}
            for row in csv.DictReader(stream)
        ]
    with (DATA / "executions.csv").open(newline="") as stream:
        jobs = []
        numeric = {"receipt_started_seconds", "receipt_finished_seconds", "source_wall_seconds"}
        for row in csv.DictReader(stream):
            jobs.append(
                {
                    k: (float(v) if v else None) if k in numeric else int(v) if k == "gpus" else v
                    for k, v in row.items()
                }
            )
    for field in TOKEN_FIELDS:
        assert sum(row[field] for row in requests) == metrics["roles"]["worker"]["tokens"][field]
        assert (
            sum(role["tokens"][field] for role in metrics["roles"].values())
            == metrics["tokens"][field]
        )
    assert len(requests) == metrics["worker_requests_with_counters"]
    assert len(jobs) == metrics["execution"]["count"]
    assert dict(Counter(row["status"] for row in jobs)) == metrics["execution"]["statuses"]
    assert abs(union_seconds(jobs) - metrics["execution"]["active_union_seconds"]) < 0.01
    gpu_hours = sum((row["source_wall_seconds"] or 0) * row["gpus"] for row in jobs) / 3600
    assert abs(gpu_hours - metrics["execution"]["requested_gpu_hours"]) < 1e-8
    for row in requests:
        assert 0 <= row["cached_input_tokens"] <= row["input_tokens"]
        assert row["last_request_input_tokens"] == row["input_tokens"]
    return metrics, requests, jobs


def pricing_scenarios(metrics, requests):
    rates = metrics["api_pricing"]["usd_per_million_tokens"]
    others = [r["tokens"] for name, r in metrics["roles"].items() if name != "worker"]
    scenarios = {}
    for cache_write in (False, True):
        worker = sum(
            token_cost(
                row,
                rates,
                long_context=row["last_request_input_tokens"]
                > metrics["api_pricing"]["long_context_threshold"],
                cache_write=cache_write,
            )
            for row in requests
        )
        key = "all_uncached_as_cache_write" if cache_write else "all_uncached_as_input"
        scenarios[key] = {
            "worker_usd": worker,
            "other_roles_short_usd": sum(
                token_cost(t, rates, cache_write=cache_write) for t in others
            ),
            "other_roles_long_usd": sum(
                token_cost(t, rates, long_context=True, cache_write=cache_write) for t in others
            ),
        }
        scenarios[key]["total_short_other_usd"] = worker + scenarios[key]["other_roles_short_usd"]
        scenarios[key]["total_long_other_usd"] = worker + scenarios[key]["other_roles_long_usd"]
    return scenarios


def plot(metrics, requests, jobs):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.rcParams.update({"font.size": 10, "axes.spines.top": False, "axes.spines.right": False})
    fig, axes = plt.subplots(3, 1, figsize=(10, 10), constrained_layout=True)
    bins = metrics["six_hour_bins"]
    positions = list(range(len(bins)))
    labels = [row["hours"] for row in bins]
    colors = {"succeeded": "#497b69", "failed": "#bd6256", "timed_out": "#e4ad51"}
    bottom = [0] * len(bins)
    for status, color in colors.items():
        counts = [row["job_statuses"].get(status, 0) for row in bins]
        axes[0].bar(positions, counts, bottom=bottom, color=color, label=status.replace("_", " "))
        bottom = [a + b for a, b in zip(bottom, counts, strict=True)]
    axes[0].set(
        ylabel="Recorded executions",
        title="Work continued across the campaign; executions include builds and diagnostics",
    )
    axes[0].set_xticks(positions, labels)
    axes[0].legend(ncols=3, frameon=False)

    cached, uncached = [], []
    for hour in range(0, 48, 6):
        rows = [row for row in requests if hour <= row["elapsed_hours"] < hour + 6]
        cached.append(sum(row["cached_input_tokens"] for row in rows) / 1e6)
        uncached.append(sum(row["input_tokens"] - row["cached_input_tokens"] for row in rows) / 1e6)
    axes[1].bar(positions, cached, color="#7565a6", label="Cached input")
    axes[1].bar(positions, uncached, bottom=cached, color="#ddb35e", label="Uncached input")
    axes[1].set(
        ylabel="Worker input tokens (millions)",
        title="The final six hours processed 144.78 million worker input tokens",
    )
    axes[1].set_xticks(positions, labels)
    axes[1].legend(ncols=2, frameon=False)

    palette = {
        "solver_or_native_fixture": "#497b69",
        "build": "#7565a6",
        "analysis_and_setup": "#c99643",
    }
    names = {
        "solver_or_native_fixture": "Solver and native fixtures",
        "build": "Builds",
        "analysis_and_setup": "Analysis and setup",
    }
    for y, (group, color) in enumerate(palette.items()):
        intervals = [
            (
                row["receipt_started_seconds"] / 3600,
                (row["receipt_finished_seconds"] - row["receipt_started_seconds"]) / 3600,
            )
            for row in jobs
            if row["work_group"] == group
            and row["receipt_started_seconds"] is not None
            and row["receipt_finished_seconds"] is not None
        ]
        axes[2].broken_barh(intervals, (y - 0.3, 0.6), facecolors=color)
    axes[2].set_yticks(list(range(3)), [names[group] for group in palette])
    axes[2].set(
        xlim=(0, 48),
        xlabel="Hours since launch",
        title="At least one execution receipt was active for 30.19 hours",
    )
    axes[2].text(
        0,
        -0.23,
        "Receipt intervals include transfer and collection. Categories follow the retained audit "
        "heuristics; GPU utilization was not measured.",
        transform=axes[2].transAxes,
        fontsize=8,
    )
    for ax in axes[:2]:
        ax.set_xlabel("Six-hour interval since launch")
    for ax in axes:
        ax.grid(axis="y" if ax != axes[2] else "x", alpha=0.15)
        ax.set_axisbelow(True)
    fig.suptitle("FRC campaign   12–14 October 2026   Hypothesis unresolved", fontsize=16)
    ASSETS.mkdir(parents=True, exist_ok=True)
    for extension in ("png", "pdf"):
        fig.savefig(ASSETS / f"frc-campaign-accounting-20261014.{extension}", dpi=180)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--node-hourly-usd", type=float, help="Assumed hourly charge for the entire eight-GPU node"
    )
    parser.add_argument("--no-plots", action="store_true")
    args = parser.parse_args()
    if args.node_hourly_usd is not None and args.node_hourly_usd < 0:
        parser.error("the node charge must be nonnegative")
    metrics, requests, jobs = load_and_check()
    scenarios = pricing_scenarios(metrics, requests)
    for name, row in scenarios.items():
        print(f"{name}: ${row['total_short_other_usd']:.2f}–${row['total_long_other_usd']:.2f}")
    if args.node_hourly_usd is not None:
        rental = args.node_hourly_usd * metrics["campaign"]["wall_hours"]
        low = scenarios["all_uncached_as_input"]["total_short_other_usd"]
        high = scenarios["all_uncached_as_cache_write"]["total_long_other_usd"]
        print(
            f"Assumed node rental: ${rental:.2f}; rental plus recorded-token scenarios: "
            f"${low + rental:.2f}–${high + rental:.2f}"
        )
    print(
        "Unreported model calls, storage, electricity and tax are excluded; "
        "these scenarios are not the campaign invoice."
    )
    if not args.no_plots:
        plot(metrics, requests, jobs)


if __name__ == "__main__":
    main()
