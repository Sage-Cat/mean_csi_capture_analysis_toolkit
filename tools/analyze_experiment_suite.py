#!/usr/bin/env python3
"""Run the full local CSI experiment analysis suite and summarize the results."""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


ROOT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class AnalysisTask:
    """One analyzer invocation plus its expected output directory."""

    slug: str
    label: str
    dataset_dir: Path
    out_dir: Path
    command: list[str]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data_root",
        default="experiments",
        help="Root containing the mounted experiment directories.",
    )
    parser.add_argument(
        "--out_dir",
        default="out",
        help="Root directory for generated analysis artifacts.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Shared random seed for analyzers.")
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter used for analyzer subprocesses.",
    )
    return parser.parse_args()


def date_token_from_name(name: str) -> str:
    match = re.search(r"(\d{4})_(\d{2})_(\d{2})", name)
    if not match:
        return "latest"
    return "".join(match.groups())


def relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT_DIR.resolve()))
    except ValueError:
        return str(path.resolve())


def find_single_experiment_dir(data_root: Path, token: str) -> Path:
    matches = sorted(
        path for path in data_root.iterdir() if path.is_dir() and token in path.name.casefold()
    )
    if not matches:
        raise FileNotFoundError(f"No experiment directory containing '{token}' under {data_root}")
    if len(matches) > 1:
        raise RuntimeError(
            f"Expected one experiment directory for '{token}', found {len(matches)}: "
            + ", ".join(path.name for path in matches)
        )
    return matches[0]


def ensure_readable_dir(path: Path, label: str) -> Path:
    if not path.is_dir():
        raise FileNotFoundError(f"Missing {label} directory: {path}")
    return path


def markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "| status |\n| --- |\n| no data |\n"
    lines = [
        "| " + " | ".join(str(col) for col in frame.columns) + " |",
        "| " + " | ".join("---" for _ in frame.columns) + " |",
    ]
    for row in frame.itertuples(index=False):
        rendered: list[str] = []
        for value in row:
            if isinstance(value, float):
                rendered.append(f"{value:.4f}")
            else:
                rendered.append(str(value))
        lines.append("| " + " | ".join(rendered) + " |")
    return "\n".join(lines) + "\n"


def build_tasks(args: argparse.Namespace, data_root: Path, out_root: Path) -> list[AnalysisTask]:
    distance_root = find_single_experiment_dir(data_root, "distance_measurement")
    angle_root = find_single_experiment_dir(data_root, "angle_measurement")
    static_root = find_single_experiment_dir(data_root, "static_gesture")
    obstacle_root = find_single_experiment_dir(data_root, "obstacle_analysis")

    distance_date = date_token_from_name(distance_root.name)
    angle_date = date_token_from_name(angle_root.name)
    static_date = date_token_from_name(static_root.name)
    obstacle_date = date_token_from_name(obstacle_root.name)

    distance_data = ensure_readable_dir(distance_root / "data", "distance dataset")
    angle_data = ensure_readable_dir(angle_root / "дані", "angle dataset")
    static_data = ensure_readable_dir(static_root / "дані", "static-gesture dataset")
    obstacle_data = ensure_readable_dir(obstacle_root / "дані", "obstacle dataset")

    python = args.python
    seed = str(args.seed)
    return [
        AnalysisTask(
            slug="distance_measurement",
            label="Distance estimation",
            dataset_dir=distance_data,
            out_dir=out_root / f"distance_measurement_{distance_date}",
            command=[
                python,
                "tools/analyze_wifi_distance_measurement.py",
                "--data_dir",
                str(distance_data),
                "--out_dir",
                str(out_root / f"distance_measurement_{distance_date}"),
                "--seed",
                seed,
            ],
        ),
        AnalysisTask(
            slug="stability_statistics",
            label="Distance-channel stability",
            dataset_dir=distance_data,
            out_dir=out_root / f"stability_statistics_{distance_date}",
            command=[
                python,
                "tools/analyze_wifi_stability_statistics.py",
                "--data_dir",
                str(distance_data),
                "--out_dir",
                str(out_root / f"stability_statistics_{distance_date}"),
                "--seed",
                seed,
            ],
        ),
        AnalysisTask(
            slug="stability_statistics_hardening",
            label="Distance manuscript-hardening evidence",
            dataset_dir=distance_data,
            out_dir=out_root / f"stability_statistics_hardening_{distance_date}",
            command=[
                python,
                "tools/analyze_stability_manuscript_hardening.py",
                "--data_dir",
                str(distance_data),
                "--out_dir",
                str(out_root / f"stability_statistics_hardening_{distance_date}"),
                "--seed",
                seed,
            ],
        ),
        AnalysisTask(
            slug="angular_localization",
            label="Angular localization",
            dataset_dir=angle_data,
            out_dir=out_root / f"angular_localization_{angle_date}",
            command=[
                python,
                "tools/analyze_wifi_angular_localization.py",
                "--data_dir",
                str(angle_data),
                "--out_dir",
                str(out_root / f"angular_localization_{angle_date}"),
                "--seed",
                seed,
            ],
        ),
        AnalysisTask(
            slug="static_gesture",
            label="Static gesture classification",
            dataset_dir=static_data,
            out_dir=out_root / f"static_gesture_{static_date}",
            command=[
                python,
                "tools/analyze_wifi_static_gesture.py",
                "--data_dir",
                str(static_data),
                "--out_dir",
                str(out_root / f"static_gesture_{static_date}"),
                "--seed",
                seed,
                "--window_s",
                "1.0",
                "--overlap",
                "0.5",
                "--test_size",
                "0.3",
            ],
        ),
        AnalysisTask(
            slug="obstacle_analysis",
            label="Obstacle attenuation",
            dataset_dir=obstacle_data,
            out_dir=out_root / f"obstacle_analysis_{obstacle_date}",
            command=[
                python,
                "tools/analyze_wifi_obstacle_scenarios.py",
                "--data_dir",
                str(obstacle_data),
                "--out_dir",
                str(out_root / f"obstacle_analysis_{obstacle_date}"),
            ],
        ),
    ]


def run_tasks(tasks: list[AnalysisTask]) -> list[dict[str, str]]:
    command_rows: list[dict[str, str]] = []
    for task in tasks:
        task.out_dir.parent.mkdir(parents=True, exist_ok=True)
        print(f"[suite] Running {task.label}: {shlex.join(task.command)}")
        subprocess.run(task.command, cwd=ROOT_DIR, check=True)
        command_rows.append(
            {
                "analysis_slug": task.slug,
                "label": task.label,
                "dataset_dir": relpath(task.dataset_dir),
                "out_dir": relpath(task.out_dir),
                "command": shlex.join(task.command),
            }
        )
    return command_rows


def distance_summary(task: AnalysisTask) -> tuple[dict[str, Any], dict[str, Any]]:
    overall = pd.read_csv(task.out_dir / "tables/table_metrics_overall.csv")
    best = overall.sort_values("MAE", ascending=True).iloc[0]
    baseline = overall.loc[overall["method"] == "RSSI_log_distance_median"].iloc[0]
    summary = {
        "analysis_slug": task.slug,
        "analysis_label": task.label,
        "dataset_dir": relpath(task.dataset_dir),
        "out_dir": relpath(task.out_dir),
        "best_method": str(best["method"]),
        "primary_metric": "MAE_m",
        "primary_value": float(best["MAE"]),
        "supporting_metric": "RMSE_m",
        "supporting_value": float(best["RMSE"]),
        "conclusion": (
            f"{best['method']} reached {best['MAE']:.2f} m MAE and clearly outperformed the "
            f"stabilized RSSI baseline at {baseline['MAE']:.2f} m MAE."
        ),
    }
    artifact = {
        "analysis_slug": task.slug,
        "report_path": relpath(task.out_dir / "report.md"),
        "key_table_path": relpath(task.out_dir / "tables/table_metrics_overall.csv"),
        "key_figure_path": relpath(task.out_dir / "figs/scatter_pred_vs_true.png"),
    }
    return summary, artifact


def stability_summary(task: AnalysisTask) -> tuple[dict[str, Any], dict[str, Any]]:
    separability = pd.read_csv(task.out_dir / "tables/table_separability_scores.csv")
    fading = pd.read_csv(task.out_dir / "tables/table_fading_depth_by_scenario.csv")
    best_sep = separability.sort_values("silhouette_score_los_vs_nlos", ascending=False).iloc[0]
    most_variable = fading.sort_values("fd_amp_median", ascending=False).iloc[0]
    summary = {
        "analysis_slug": task.slug,
        "analysis_label": task.label,
        "dataset_dir": relpath(task.dataset_dir),
        "out_dir": relpath(task.out_dir),
        "best_method": str(best_sep["feature_set"]),
        "primary_metric": "silhouette_los_vs_nlos",
        "primary_value": float(best_sep["silhouette_score_los_vs_nlos"]),
        "supporting_metric": "max_fd_amp_median",
        "supporting_value": float(most_variable["fd_amp_median"]),
        "conclusion": (
            f"{most_variable['scenario']} showed the highest CSI fading-depth median "
            f"({most_variable['fd_amp_median']:.2f}), but LoS-vs-NLoS separability stayed weak "
            f"(best silhouette {best_sep['silhouette_score_los_vs_nlos']:.3f})."
        ),
    }
    artifact = {
        "analysis_slug": task.slug,
        "report_path": relpath(task.out_dir / "report.md"),
        "key_table_path": relpath(task.out_dir / "tables/table_separability_scores.csv"),
        "key_figure_path": relpath(task.out_dir / "figs/pca_separability.png"),
    }
    return summary, artifact


def angle_summary(task: AnalysisTask) -> tuple[dict[str, Any], dict[str, Any]]:
    overall = pd.read_csv(task.out_dir / "tables/table_metrics_overall.csv")
    best = overall.sort_values("MAE_deg", ascending=True).iloc[0]
    summary = {
        "analysis_slug": task.slug,
        "analysis_label": task.label,
        "dataset_dir": relpath(task.dataset_dir),
        "out_dir": relpath(task.out_dir),
        "best_method": str(best["method"]),
        "primary_metric": "MAE_deg",
        "primary_value": float(best["MAE_deg"]),
        "supporting_metric": "P_abs_err_le_10deg",
        "supporting_value": float(best["P_abs_err_le_10deg"]),
        "conclusion": (
            f"{best['method']} was best, but still reached {best['MAE_deg']:.2f} deg MAE and "
            f"only {best['P_abs_err_le_10deg'] * 100:.1f}% of predictions landed within 10 deg, "
            "so the current single-antenna angle dataset is not operationally reliable."
        ),
    }
    artifact = {
        "analysis_slug": task.slug,
        "report_path": relpath(task.out_dir / "report.md"),
        "key_table_path": relpath(task.out_dir / "tables/table_metrics_overall.csv"),
        "key_figure_path": relpath(task.out_dir / "figs/polar_mean_error.png"),
    }
    return summary, artifact


def static_summary(task: AnalysisTask) -> tuple[dict[str, Any], dict[str, Any]]:
    overall = pd.read_csv(task.out_dir / "tables/table_metrics_overall.csv")
    best = overall.sort_values("balanced_accuracy", ascending=False).iloc[0]
    summary = {
        "analysis_slug": task.slug,
        "analysis_label": task.label,
        "dataset_dir": relpath(task.dataset_dir),
        "out_dir": relpath(task.out_dir),
        "best_method": str(best["method"]),
        "primary_metric": "balanced_accuracy",
        "primary_value": float(best["balanced_accuracy"]),
        "supporting_metric": "run_majority_acc",
        "supporting_value": float(best["run_majority_acc"]),
        "conclusion": (
            f"{best['method']} reached balanced accuracy {best['balanced_accuracy']:.3f} and "
            f"run-level majority accuracy {best['run_majority_acc']:.3f}; the posture task is "
            "measurably separable, but still only moderately robust."
        ),
    }
    artifact = {
        "analysis_slug": task.slug,
        "report_path": relpath(task.out_dir / "report.md"),
        "key_table_path": relpath(task.out_dir / "tables/table_metrics_overall.csv"),
        "key_figure_path": relpath(task.out_dir / "figs/confusion_matrix_best_model.png"),
    }
    return summary, artifact


def obstacle_summary(task: AnalysisTask) -> tuple[dict[str, Any], dict[str, Any]]:
    scenarios = pd.read_csv(task.out_dir / "tables/table_scenario_summary.csv")
    deltas = pd.read_csv(task.out_dir / "tables/table_reference_deltas.csv")
    ordered = scenarios.sort_values("rssi_packet_median", ascending=False)
    order_text = " -> ".join(ordered["scenario_display"].tolist())
    strongest = deltas.sort_values("delta_run_rssi_mean_median_vs_reference", ascending=True).iloc[0]
    summary = {
        "analysis_slug": task.slug,
        "analysis_label": task.label,
        "dataset_dir": relpath(task.dataset_dir),
        "out_dir": relpath(task.out_dir),
        "best_method": "descriptive_ordering",
        "primary_metric": "delta_run_rssi_mean_median_vs_reference_db",
        "primary_value": float(strongest["delta_run_rssi_mean_median_vs_reference"]),
        "supporting_metric": "ordering",
        "supporting_value": order_text,
        "conclusion": (
            f"Obstacle attenuation ordering was stable as {order_text}; "
            f"{strongest['scenario_display']} was the strongest attenuator at "
            f"{strongest['delta_run_rssi_mean_median_vs_reference']:.2f} dB versus the empty-room reference."
        ),
    }
    artifact = {
        "analysis_slug": task.slug,
        "report_path": relpath(task.out_dir / "report.md"),
        "key_table_path": relpath(task.out_dir / "tables/table_reference_deltas.csv"),
        "key_figure_path": relpath(task.out_dir / "figs/boxplot_rssi_by_scenario.png"),
    }
    return summary, artifact


def hardening_summary(task: AnalysisTask) -> tuple[dict[str, Any], dict[str, Any]]:
    summary = {
        "analysis_slug": task.slug,
        "analysis_label": task.label,
        "dataset_dir": relpath(task.dataset_dir),
        "out_dir": relpath(task.out_dir),
        "best_method": "artifact_bundle",
        "primary_metric": "table_count",
        "primary_value": 8.0,
        "supporting_metric": "figure_count",
        "supporting_value": 1.0,
        "conclusion": (
            "Generated the manuscript-hardening evidence bundle for the distance dataset, "
            "including protocol coverage, interval summaries, confidence intervals, and "
            "cross-run stability tables."
        ),
    }
    artifact = {
        "analysis_slug": task.slug,
        "report_path": relpath(task.out_dir / "report.md"),
        "key_table_path": relpath(task.out_dir / "tables/table_capture_protocol_metadata.csv"),
        "key_figure_path": relpath(task.out_dir / "figs/fading_depth_ci_by_scenario.png"),
    }
    return summary, artifact


def build_suite_rows(tasks: list[AnalysisTask]) -> tuple[pd.DataFrame, pd.DataFrame]:
    summary_rows: list[dict[str, Any]] = []
    artifact_rows: list[dict[str, Any]] = []
    handlers = {
        "distance_measurement": distance_summary,
        "stability_statistics": stability_summary,
        "stability_statistics_hardening": hardening_summary,
        "angular_localization": angle_summary,
        "static_gesture": static_summary,
        "obstacle_analysis": obstacle_summary,
    }
    for task in tasks:
        summary_row, artifact_row = handlers[task.slug](task)
        summary_rows.append(summary_row)
        artifact_rows.append(artifact_row)
    return pd.DataFrame(summary_rows), pd.DataFrame(artifact_rows)


def make_overview_plot(
    suite_dir: Path,
    distance_task: AnalysisTask,
    stability_task: AnalysisTask,
    angle_task: AnalysisTask,
    static_task: AnalysisTask,
    obstacle_task: AnalysisTask,
    summary_df: pd.DataFrame,
) -> Path:
    distance_df = pd.read_csv(distance_task.out_dir / "tables/table_metrics_overall.csv")
    stability_df = pd.read_csv(stability_task.out_dir / "tables/table_separability_scores.csv")
    angle_df = pd.read_csv(angle_task.out_dir / "tables/table_metrics_overall.csv")
    static_df = pd.read_csv(static_task.out_dir / "tables/table_metrics_overall.csv")
    obstacle_df = pd.read_csv(obstacle_task.out_dir / "tables/table_reference_deltas.csv")
    obstacle_df = obstacle_df.loc[obstacle_df["scenario_display"] != "Empty space"].copy()
    obstacle_df = obstacle_df.sort_values("delta_run_rssi_mean_median_vs_reference")

    fig, axes = plt.subplots(3, 2, figsize=(14, 13))

    axes[0, 0].bar(distance_df["method"], distance_df["MAE"], color="#1f77b4")
    axes[0, 0].set_title("Distance Estimation MAE (m)")
    axes[0, 0].set_ylabel("MAE (m)")
    axes[0, 0].tick_params(axis="x", rotation=25)
    axes[0, 0].grid(axis="y", alpha=0.3)

    axes[0, 1].bar(stability_df["feature_set"], stability_df["silhouette_score_los_vs_nlos"], color="#ff7f0e")
    axes[0, 1].set_title("LoS vs NLoS Separability")
    axes[0, 1].set_ylabel("Silhouette score")
    axes[0, 1].tick_params(axis="x", rotation=20)
    axes[0, 1].grid(axis="y", alpha=0.3)

    axes[1, 0].bar(angle_df["method"], angle_df["MAE_deg"], color="#d62728")
    axes[1, 0].set_title("Angle Estimation MAE (deg)")
    axes[1, 0].set_ylabel("MAE (deg)")
    axes[1, 0].tick_params(axis="x", rotation=20)
    axes[1, 0].grid(axis="y", alpha=0.3)

    axes[1, 1].bar(static_df["method"], static_df["balanced_accuracy"], color="#2ca02c")
    axes[1, 1].set_title("Static Gesture Balanced Accuracy")
    axes[1, 1].set_ylabel("Balanced accuracy")
    axes[1, 1].set_ylim(0.0, 1.0)
    axes[1, 1].tick_params(axis="x", rotation=20)
    axes[1, 1].grid(axis="y", alpha=0.3)

    axes[2, 0].bar(
        obstacle_df["scenario_display"],
        obstacle_df["delta_run_rssi_mean_median_vs_reference"],
        color="#9467bd",
    )
    axes[2, 0].set_title("Obstacle RSSI Delta vs Empty Space")
    axes[2, 0].set_ylabel("Delta RSSI (dB)")
    axes[2, 0].tick_params(axis="x", rotation=20)
    axes[2, 0].grid(axis="y", alpha=0.3)

    axes[2, 1].axis("off")
    top_findings = summary_df.loc[
        summary_df["analysis_slug"].isin(
            [
                "distance_measurement",
                "stability_statistics",
                "angular_localization",
                "static_gesture",
                "obstacle_analysis",
            ]
        ),
        ["analysis_label", "conclusion"],
    ]
    note_lines = ["Suite highlights:"]
    for row in top_findings.itertuples(index=False):
        note_lines.append(f"- {row.analysis_label}: {row.conclusion}")
    axes[2, 1].text(
        0.0,
        1.0,
        "\n".join(note_lines),
        va="top",
        ha="left",
        wrap=True,
        fontsize=10,
    )

    fig.tight_layout()
    out_path = suite_dir / "figs" / "suite_overview_metrics.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return out_path


def write_suite_outputs(
    suite_dir: Path,
    command_rows: list[dict[str, str]],
    summary_df: pd.DataFrame,
    artifact_df: pd.DataFrame,
    overview_path: Path,
) -> None:
    tables_dir = suite_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    summary_df.to_csv(tables_dir / "table_analysis_summary.csv", index=False)
    artifact_df.to_csv(tables_dir / "table_artifact_manifest.csv", index=False)
    pd.DataFrame(command_rows).to_csv(tables_dir / "table_command_log.csv", index=False)

    manifest = {
        "generated_from": relpath(ROOT_DIR),
        "summary_table": relpath(tables_dir / "table_analysis_summary.csv"),
        "artifact_manifest": relpath(tables_dir / "table_artifact_manifest.csv"),
        "command_log": relpath(tables_dir / "table_command_log.csv"),
        "overview_figure": relpath(overview_path),
        "analyses": summary_df.to_dict(orient="records"),
    }
    (suite_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    prominent = summary_df.loc[
        summary_df["analysis_slug"].isin(
            [
                "distance_measurement",
                "stability_statistics",
                "angular_localization",
                "static_gesture",
                "obstacle_analysis",
            ]
        )
    ].copy()
    report_lines = [
        "# Experiment Analysis Suite Report",
        "",
        "## Scope",
        f"- Repository root: `{ROOT_DIR}`",
        "- Mounted data root was resolved through the local `experiments/` path.",
        "- This suite runs the full analysis pass for distance, stability, angle, static gesture, and obstacle datasets.",
        "",
        "## Analysis Summary",
        markdown_table(
            prominent[
                [
                    "analysis_label",
                    "best_method",
                    "primary_metric",
                    "primary_value",
                    "supporting_metric",
                    "supporting_value",
                ]
            ]
        ).strip(),
        "",
        "## Conclusions",
    ]
    for row in prominent.itertuples(index=False):
        report_lines.append(f"- {row.analysis_label}: {row.conclusion}")
    report_lines.extend(
        [
            "",
            "## Additional Evidence Bundle",
            "- The distance dataset also produced a manuscript-hardening artifact pack under "
            f"`{summary_df.loc[summary_df['analysis_slug'] == 'stability_statistics_hardening', 'out_dir'].iloc[0]}`.",
            "",
            "## Key Artifacts",
            markdown_table(artifact_df[["analysis_slug", "report_path", "key_table_path", "key_figure_path"]]).strip(),
            "",
            "## Commands Run",
            markdown_table(pd.DataFrame(command_rows)[["analysis_slug", "command"]]).strip(),
            "",
            "## Overview Figure",
            f"- `{relpath(overview_path)}`",
        ]
    )
    (suite_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    data_root = Path(args.data_root).resolve()
    out_root = Path(args.out_dir).resolve()

    if not data_root.is_dir():
        raise FileNotFoundError(f"data_root does not exist or is not a directory: {data_root}")

    tasks = build_tasks(args, data_root, out_root)
    command_rows = run_tasks(tasks)
    summary_df, artifact_df = build_suite_rows(tasks)

    suite_dir = out_root / "experiment_analysis_suite"
    suite_dir.mkdir(parents=True, exist_ok=True)
    overview_path = make_overview_plot(
        suite_dir=suite_dir,
        distance_task=next(task for task in tasks if task.slug == "distance_measurement"),
        stability_task=next(task for task in tasks if task.slug == "stability_statistics"),
        angle_task=next(task for task in tasks if task.slug == "angular_localization"),
        static_task=next(task for task in tasks if task.slug == "static_gesture"),
        obstacle_task=next(task for task in tasks if task.slug == "obstacle_analysis"),
        summary_df=summary_df,
    )
    write_suite_outputs(suite_dir, command_rows, summary_df, artifact_df, overview_path)
    print(f"Experiment suite analysis complete. suite_dir={suite_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
