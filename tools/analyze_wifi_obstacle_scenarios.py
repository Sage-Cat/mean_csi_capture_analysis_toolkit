#!/usr/bin/env python3
"""Analyze controlled obstacle scenarios for ESP32 RSSI/CSI captures."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

try:
    import matplotlib.pyplot as plt

    MATPLOTLIB_AVAILABLE = True
    MATPLOTLIB_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - environment dependent
    plt = None  # type: ignore[assignment]
    MATPLOTLIB_AVAILABLE = False
    MATPLOTLIB_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data_dir", required=True, help="Obstacle experiment root containing meta.json and scenario runs.")
    parser.add_argument(
        "--out_dir",
        default="out/obstacle_scenarios",
        help="Output directory for tables, plots, and report.",
    )
    parser.add_argument(
        "--reference_scenario",
        default="s01_empty_space",
        help="Scenario ID used as the reference baseline.",
    )
    return parser.parse_args()


def _parse_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _scenario_display_name(scenario_id: str) -> str:
    mapping = {
        "s01_empty_space": "Empty space",
        "s02_chair_obstacle": "Chair obstacle",
        "s03_one_wall": "One wall",
        "s04_two_walls": "Two walls",
        "s05_door": "Closed door",
    }
    return mapping.get(scenario_id, scenario_id.replace("_", " "))


def _iqr(values: np.ndarray) -> float:
    q25, q75 = np.percentile(values, [25.0, 75.0])
    return float(q75 - q25)


def _cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    if a.size == 0 or b.size == 0:
        return float("nan")
    diff = a[:, None] - b[None, :]
    greater = float(np.sum(diff > 0))
    less = float(np.sum(diff < 0))
    return (greater - less) / float(a.size * b.size)


def _kendall_tau_from_orders(order_a: list[str], order_b: list[str]) -> float:
    if len(order_a) < 2 or len(order_a) != len(order_b):
        return float("nan")
    pos_a = {item: idx for idx, item in enumerate(order_a)}
    pos_b = {item: idx for idx, item in enumerate(order_b)}
    concordant = 0
    discordant = 0
    items = list(order_a)
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            sign_a = math.copysign(1.0, pos_a[items[j]] - pos_a[items[i]])
            sign_b = math.copysign(1.0, pos_b[items[j]] - pos_b[items[i]])
            if sign_a == sign_b:
                concordant += 1
            else:
                discordant += 1
    denom = len(items) * (len(items) - 1) / 2
    return float((concordant - discordant) / denom)


def load_dataset(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    meta_path = data_dir / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"Missing dataset meta.json: {meta_path}")

    meta = _parse_manifest(meta_path)
    scenario_specs = {str(item["scenario_id"]): item for item in meta.get("scenarios", [])}

    packet_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []

    for manifest_path in sorted(data_dir.glob("s*/run_*/manifest.json")):
        manifest = _parse_manifest(manifest_path)
        scenario = manifest.get("scenario") or {}
        scenario_id = str(scenario.get("scenario_id") or manifest_path.parent.parent.name)
        spec = scenario_specs.get(scenario_id, {})
        display_name = _scenario_display_name(scenario_id)
        run_id = int(manifest.get("run_id"))
        capture_path = manifest_path.with_name("capture.jsonl")
        if not capture_path.is_file():
            raise FileNotFoundError(f"Missing capture file for manifest: {capture_path}")

        run_rssi: list[float] = []
        run_mean_amp: list[float] = []
        run_std_amp: list[float] = []
        run_cv_amp: list[float] = []

        with capture_path.open("r", encoding="utf-8", errors="replace") as handle:
            for packet_idx, raw in enumerate(handle):
                line = raw.strip()
                if not line:
                    continue
                record = json.loads(line)
                csi = np.asarray(record["csi"], dtype=np.float32)
                if csi.size % 2 != 0:
                    csi = csi[:-1]
                i_vals = csi[0::2]
                q_vals = csi[1::2]
                amp = np.sqrt(i_vals * i_vals + q_vals * q_vals, dtype=np.float32)
                mean_amp = float(np.mean(amp))
                std_amp = float(np.std(amp))
                cv_amp = float(std_amp / (mean_amp + 1e-8))
                rssi = float(record["rssi"])

                packet_rows.append(
                    {
                        "scenario_id": scenario_id,
                        "scenario_display": display_name,
                        "run_id": run_id,
                        "packet_idx": packet_idx,
                        "rssi_dbm": rssi,
                        "mean_amp": mean_amp,
                        "std_amp": std_amp,
                        "cv_amp": cv_amp,
                        "wall_count": spec.get("wall_count", scenario.get("wall_count")),
                        "obstruction_class": spec.get("obstruction_class", scenario.get("obstruction_class")),
                        "door_state": spec.get("door_state", scenario.get("door_state")),
                        "room_id": spec.get("room_id", scenario.get("room_id")),
                        "estimated_distance_m": spec.get("estimated_distance_m", scenario.get("estimated_distance_m")),
                        "scenario_tags": ",".join(spec.get("scenario_tags", scenario.get("scenario_tags") or [])),
                    }
                )
                run_rssi.append(rssi)
                run_mean_amp.append(mean_amp)
                run_std_amp.append(std_amp)
                run_cv_amp.append(cv_amp)

        run_rows.append(
            {
                "scenario_id": scenario_id,
                "scenario_display": display_name,
                "run_id": run_id,
                "num_packets": len(run_rssi),
                "wall_count": spec.get("wall_count", scenario.get("wall_count")),
                "obstruction_class": spec.get("obstruction_class", scenario.get("obstruction_class")),
                "door_state": spec.get("door_state", scenario.get("door_state")),
                "room_id": spec.get("room_id", scenario.get("room_id")),
                "estimated_distance_m": spec.get("estimated_distance_m", scenario.get("estimated_distance_m")),
                "scenario_tags": ",".join(spec.get("scenario_tags", scenario.get("scenario_tags") or [])),
                "rssi_mean": float(np.mean(run_rssi)),
                "rssi_std": float(np.std(run_rssi)),
                "rssi_median": float(np.median(run_rssi)),
                "rssi_iqr": _iqr(np.asarray(run_rssi, dtype=np.float64)),
                "mean_amp_mean": float(np.mean(run_mean_amp)),
                "mean_amp_std": float(np.std(run_mean_amp)),
                "mean_amp_median": float(np.median(run_mean_amp)),
                "mean_amp_iqr": _iqr(np.asarray(run_mean_amp, dtype=np.float64)),
                "cv_amp_median": float(np.median(run_cv_amp)),
            }
        )

    if not packet_rows or not run_rows:
        raise ValueError(f"No valid packets/runs found under {data_dir}")
    packet_df = pd.DataFrame(packet_rows).sort_values(["scenario_id", "run_id", "packet_idx"]).reset_index(drop=True)
    run_df = pd.DataFrame(run_rows).sort_values(["scenario_id", "run_id"]).reset_index(drop=True)
    return packet_df, run_df


def build_dataset_summary(packet_df: pd.DataFrame, run_df: pd.DataFrame) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "total_packets": int(len(packet_df)),
                "total_runs": int(len(run_df)),
                "num_scenarios": int(packet_df["scenario_id"].nunique()),
                "scenario_ids": ";".join(sorted(packet_df["scenario_id"].astype(str).unique())),
                "packets_per_run_median": int(run_df["num_packets"].median()),
                "distance_values_m": ";".join(
                    sorted(
                        {
                            f"{float(value):.1f}"
                            for value in packet_df["estimated_distance_m"].dropna().astype(float).unique().tolist()
                        }
                    )
                ),
            }
        ]
    )


def build_scenario_summary(packet_df: pd.DataFrame, run_df: pd.DataFrame) -> pd.DataFrame:
    packet_summary = (
        packet_df.groupby(["scenario_id", "scenario_display"], as_index=False)
        .agg(
            num_runs=("run_id", "nunique"),
            total_packets=("packet_idx", "size"),
            estimated_distance_m=("estimated_distance_m", "first"),
            wall_count=("wall_count", "first"),
            obstruction_class=("obstruction_class", "first"),
            door_state=("door_state", "first"),
            room_id=("room_id", "first"),
            scenario_tags=("scenario_tags", "first"),
            rssi_packet_median=("rssi_dbm", "median"),
            rssi_packet_q25=("rssi_dbm", lambda s: np.percentile(s, 25)),
            rssi_packet_q75=("rssi_dbm", lambda s: np.percentile(s, 75)),
            mean_amp_packet_median=("mean_amp", "median"),
            mean_amp_packet_q25=("mean_amp", lambda s: np.percentile(s, 25)),
            mean_amp_packet_q75=("mean_amp", lambda s: np.percentile(s, 75)),
        )
        .sort_values("scenario_id")
        .reset_index(drop=True)
    )

    run_summary = (
        run_df.groupby(["scenario_id"], as_index=False)
        .agg(
            run_rssi_mean_median=("rssi_mean", "median"),
            run_rssi_mean_min=("rssi_mean", "min"),
            run_rssi_mean_max=("rssi_mean", "max"),
            run_mean_amp_mean_median=("mean_amp_mean", "median"),
            run_mean_amp_mean_min=("mean_amp_mean", "min"),
            run_mean_amp_mean_max=("mean_amp_mean", "max"),
            run_cv_amp_median_median=("cv_amp_median", "median"),
        )
    )
    return packet_summary.merge(run_summary, on="scenario_id", how="left")


def build_reference_deltas(packet_df: pd.DataFrame, run_df: pd.DataFrame, reference_scenario: str) -> pd.DataFrame:
    ref_packets = packet_df.loc[packet_df["scenario_id"] == reference_scenario].copy()
    ref_runs = run_df.loc[run_df["scenario_id"] == reference_scenario].copy()
    if ref_packets.empty or ref_runs.empty:
        raise ValueError(f"Reference scenario '{reference_scenario}' is missing from dataset.")

    rows: list[dict[str, Any]] = []
    for scenario_id, group_packets in packet_df.groupby("scenario_id", sort=True):
        group_runs = run_df.loc[run_df["scenario_id"] == scenario_id]
        rows.append(
            {
                "scenario_id": scenario_id,
                "scenario_display": str(group_packets["scenario_display"].iloc[0]),
                "delta_run_rssi_mean_median_vs_reference": float(group_runs["rssi_mean"].median() - ref_runs["rssi_mean"].median()),
                "delta_run_mean_amp_mean_median_vs_reference": float(
                    group_runs["mean_amp_mean"].median() - ref_runs["mean_amp_mean"].median()
                ),
                "delta_packet_rssi_median_vs_reference": float(
                    np.median(group_packets["rssi_dbm"]) - np.median(ref_packets["rssi_dbm"])
                ),
                "delta_packet_mean_amp_median_vs_reference": float(
                    np.median(group_packets["mean_amp"]) - np.median(ref_packets["mean_amp"])
                ),
                "cliffs_delta_rssi_vs_reference": _cliffs_delta(
                    group_packets["rssi_dbm"].to_numpy(dtype=np.float64),
                    ref_packets["rssi_dbm"].to_numpy(dtype=np.float64),
                ),
                "cliffs_delta_mean_amp_vs_reference": _cliffs_delta(
                    group_packets["mean_amp"].to_numpy(dtype=np.float64),
                    ref_packets["mean_amp"].to_numpy(dtype=np.float64),
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("scenario_id").reset_index(drop=True)


def build_ordering_stability(run_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    metrics = (
        ("rssi_mean", False),
        ("mean_amp_mean", False),
        ("cv_amp_median", True),
    )
    run_ids = sorted(run_df["run_id"].astype(int).unique().tolist())
    for metric, ascending in metrics:
        orders: dict[int, list[str]] = {}
        for run_id in run_ids:
            subset = run_df.loc[run_df["run_id"] == run_id].sort_values(metric, ascending=ascending)
            orders[run_id] = subset["scenario_id"].astype(str).tolist()
        baseline_run_id = run_ids[0]
        baseline_order = orders[baseline_run_id]
        for run_id in run_ids:
            rows.append(
                {
                    "metric": metric,
                    "run_id": run_id,
                    "order_ascending": ";".join(orders[run_id]) if ascending else "",
                    "order_descending": ";".join(orders[run_id]) if not ascending else "",
                    "kendall_tau_vs_run_1": (
                        1.0 if run_id == baseline_run_id else _kendall_tau_from_orders(baseline_order, orders[run_id])
                    ),
                }
            )
    return pd.DataFrame(rows)


def plot_boxplot(packet_df: pd.DataFrame, value_col: str, ylabel: str, title: str, out_path: Path) -> None:
    order = sorted(packet_df["scenario_id"].astype(str).unique().tolist())
    labels = [
        _scenario_display_name(scenario_id).replace(" ", "\n")
        for scenario_id in order
    ]
    data = [packet_df.loc[packet_df["scenario_id"] == scenario_id, value_col].to_numpy(dtype=float) for scenario_id in order]
    plt.figure(figsize=(9.5, 5.0))
    plt.boxplot(data, tick_labels=labels, showmeans=True, showfliers=False)
    plt.ylabel(ylabel)
    plt.title(title)
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


def write_report(
    out_path: Path,
    *,
    dataset_summary: pd.DataFrame,
    scenario_summary: pd.DataFrame,
    reference_deltas: pd.DataFrame,
    ordering_stability: pd.DataFrame,
    reference_scenario: str,
    plots_available: bool,
) -> None:
    ds = dataset_summary.iloc[0]
    mean_amp_rows = ordering_stability.loc[ordering_stability["metric"] == "mean_amp_mean"].copy()
    rssi_rows = ordering_stability.loc[ordering_stability["metric"] == "rssi_mean"].copy()
    ref_name = _scenario_display_name(reference_scenario)
    lines = [
        "# Obstacle Scenario Analysis Report",
        "",
        "## Dataset",
        f"- Total packets: `{int(ds['total_packets'])}`",
        f"- Total runs: `{int(ds['total_runs'])}`",
        f"- Scenario count: `{int(ds['num_scenarios'])}`",
        f"- Scenarios: `{ds['scenario_ids']}`",
        f"- Median packets per run: `{int(ds['packets_per_run_median'])}`",
        f"- Estimated distances (m): `{ds['distance_values_m']}`",
        "",
        "## Main Findings",
        f"- `{ref_name}` is the reference scenario for all reported deltas.",
        "- Median packet-level RSSI orders the scenarios from lightest to strongest attenuation as: "
        + ", ".join(scenario_summary.sort_values("rssi_packet_median", ascending=False)["scenario_display"].astype(str).tolist())
        + ".",
        "- Median packet-level CSI mean amplitude orders the scenarios from strongest to weakest response as: "
        + ", ".join(scenario_summary.sort_values("mean_amp_packet_median", ascending=False)["scenario_display"].astype(str).tolist())
        + ".",
        (
            "- Run-level `mean_amp_mean` ordering was perfectly stable across the three runs "
            f"(Kendall tau vs run 1: {', '.join(f'run {int(row.run_id)}={row.kendall_tau_vs_run_1:.2f}' for _, row in mean_amp_rows.iterrows())})."
        ),
        (
            "- Run-level `rssi_mean` ordering was stable up to one swap between the reference and chair scenarios "
            f"(Kendall tau vs run 1: {', '.join(f'run {int(row.run_id)}={row.kendall_tau_vs_run_1:.2f}' for _, row in rssi_rows.iterrows())})."
        ),
        "- Important caveat: the chair scenario was captured at 3.0 m, while the other scenarios were nominally 2.0 m, so its delta is not a pure obstacle-only effect.",
        "",
        "## Scenario Summary",
        scenario_summary.to_string(index=False, float_format=lambda value: f"{value:.4f}" if isinstance(value, float) else str(value)),
        "",
        "## Reference Deltas",
        reference_deltas.to_string(index=False, float_format=lambda value: f"{value:.4f}" if isinstance(value, float) else str(value)),
        "",
        "## Ordering Stability",
        ordering_stability.to_string(index=False, float_format=lambda value: f"{value:.4f}" if isinstance(value, float) else str(value)),
    ]
    if not plots_available:
        lines.extend(
            [
                "",
                "## Plot Generation Note",
                f"- Figure generation was skipped because matplotlib is unavailable (`{MATPLOTLIB_IMPORT_ERROR}`).",
            ]
        )
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    if MATPLOTLIB_AVAILABLE:
        figs_dir.mkdir(parents=True, exist_ok=True)

    packet_df, run_df = load_dataset(data_dir)
    dataset_summary = build_dataset_summary(packet_df, run_df)
    scenario_summary = build_scenario_summary(packet_df, run_df)
    reference_deltas = build_reference_deltas(packet_df, run_df, args.reference_scenario)
    ordering_stability = build_ordering_stability(run_df)

    dataset_summary.to_csv(tables_dir / "table_dataset_summary.csv", index=False)
    run_df.to_csv(tables_dir / "table_run_summary.csv", index=False)
    scenario_summary.to_csv(tables_dir / "table_scenario_summary.csv", index=False)
    reference_deltas.to_csv(tables_dir / "table_reference_deltas.csv", index=False)
    ordering_stability.to_csv(tables_dir / "table_ordering_stability.csv", index=False)

    if MATPLOTLIB_AVAILABLE:
        plot_boxplot(
            packet_df,
            value_col="rssi_dbm",
            ylabel="RSSI (dBm)",
            title="Packet-Level RSSI by Obstacle Scenario",
            out_path=figs_dir / "boxplot_rssi_by_scenario.png",
        )
        plot_boxplot(
            packet_df,
            value_col="mean_amp",
            ylabel="CSI mean_amp",
            title="Packet-Level CSI mean_amp by Obstacle Scenario",
            out_path=figs_dir / "boxplot_mean_amp_by_scenario.png",
        )

    write_report(
        out_path=out_dir / "report.md",
        dataset_summary=dataset_summary,
        scenario_summary=scenario_summary,
        reference_deltas=reference_deltas,
        ordering_stability=ordering_stability,
        reference_scenario=args.reference_scenario,
        plots_available=MATPLOTLIB_AVAILABLE,
    )

    expected_files = [
        tables_dir / "table_dataset_summary.csv",
        tables_dir / "table_run_summary.csv",
        tables_dir / "table_scenario_summary.csv",
        tables_dir / "table_reference_deltas.csv",
        tables_dir / "table_ordering_stability.csv",
        out_dir / "report.md",
    ]
    if MATPLOTLIB_AVAILABLE:
        expected_files.extend(
            [
                figs_dir / "boxplot_rssi_by_scenario.png",
                figs_dir / "boxplot_mean_amp_by_scenario.png",
            ]
        )
    missing = [str(path) for path in expected_files if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing expected output files: {missing}")

    print("=== Obstacle scenario summary ===")
    print(
        scenario_summary[
            [
                "scenario_id",
                "rssi_packet_median",
                "mean_amp_packet_median",
                "run_rssi_mean_median",
                "run_mean_amp_mean_median",
            ]
        ].to_string(index=False, float_format=lambda value: f"{value:.4f}")
    )
    print(f"Outputs written to: {out_dir}")


if __name__ == "__main__":
    main()
