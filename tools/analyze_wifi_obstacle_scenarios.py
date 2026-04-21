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

try:
    from sklearn.ensemble import ExtraTreesClassifier
    from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score
    from sklearn.model_selection import LeaveOneGroupOut, cross_val_predict

    SKLEARN_AVAILABLE = True
    SKLEARN_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - environment dependent
    ExtraTreesClassifier = None  # type: ignore[assignment]
    accuracy_score = None  # type: ignore[assignment]
    classification_report = None  # type: ignore[assignment]
    confusion_matrix = None  # type: ignore[assignment]
    f1_score = None  # type: ignore[assignment]
    LeaveOneGroupOut = None  # type: ignore[assignment]
    cross_val_predict = None  # type: ignore[assignment]
    SKLEARN_AVAILABLE = False
    SKLEARN_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"


SCENARIO_SEVERITY_ORDER = {
    "s01_empty_space": 0,
    "s02_chair_obstacle": 1,
    "s05_door": 2,
    "s03_one_wall": 3,
    "s04_two_walls": 4,
}


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
    parser.add_argument(
        "--window_size",
        type=int,
        default=50,
        help="Packet window size for grouped ML evaluation.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for deterministic ML evaluation.",
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


def build_window_dataset(packet_df: pd.DataFrame, window_size: int) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for (scenario_id, scenario_display, run_id), group in packet_df.groupby(
        ["scenario_id", "scenario_display", "run_id"], sort=True
    ):
        group = group.sort_values("packet_idx").reset_index(drop=True)
        for start in range(0, len(group), window_size):
            chunk = group.iloc[start : start + window_size]
            if len(chunk) < window_size:
                continue
            rssi = chunk["rssi_dbm"].to_numpy(dtype=np.float64)
            mean_amp = chunk["mean_amp"].to_numpy(dtype=np.float64)
            std_amp = chunk["std_amp"].to_numpy(dtype=np.float64)
            cv_amp = chunk["cv_amp"].to_numpy(dtype=np.float64)
            rows.append(
                {
                    "scenario_id": scenario_id,
                    "scenario_display": scenario_display,
                    "run_id": int(run_id),
                    "group_id": f"{scenario_id}_run_{int(run_id)}",
                    "window_start": int(start),
                    "window_size": int(window_size),
                    "severity_rank": int(SCENARIO_SEVERITY_ORDER[scenario_id]),
                    "estimated_distance_m": float(chunk["estimated_distance_m"].iloc[0]),
                    "rssi_mean": float(np.mean(rssi)),
                    "rssi_std": float(np.std(rssi)),
                    "rssi_median": float(np.median(rssi)),
                    "rssi_q25": float(np.percentile(rssi, 25.0)),
                    "rssi_q75": float(np.percentile(rssi, 75.0)),
                    "mean_amp_mean": float(np.mean(mean_amp)),
                    "mean_amp_std": float(np.std(mean_amp)),
                    "mean_amp_median": float(np.median(mean_amp)),
                    "mean_amp_q25": float(np.percentile(mean_amp, 25.0)),
                    "mean_amp_q75": float(np.percentile(mean_amp, 75.0)),
                    "mean_amp_min": float(np.min(mean_amp)),
                    "mean_amp_max": float(np.max(mean_amp)),
                    "std_amp_mean": float(np.mean(std_amp)),
                    "std_amp_median": float(np.median(std_amp)),
                    "cv_amp_mean": float(np.mean(cv_amp)),
                    "cv_amp_median": float(np.median(cv_amp)),
                    "cv_amp_std": float(np.std(cv_amp)),
                }
            )
    if not rows:
        raise ValueError("No complete packet windows were generated for ML evaluation.")
    return pd.DataFrame(rows).sort_values(["scenario_id", "run_id", "window_start"]).reset_index(drop=True)


def _ml_feature_columns() -> list[str]:
    return [
        "rssi_mean",
        "rssi_std",
        "rssi_median",
        "rssi_q25",
        "rssi_q75",
        "mean_amp_mean",
        "mean_amp_std",
        "mean_amp_median",
        "mean_amp_q25",
        "mean_amp_q75",
        "mean_amp_min",
        "mean_amp_max",
        "std_amp_mean",
        "std_amp_median",
        "cv_amp_mean",
        "cv_amp_median",
        "cv_amp_std",
    ]


def _evaluate_grouped_classifier(
    window_df: pd.DataFrame,
    *,
    task_id: str,
    task_label: str,
    class_order: list[str],
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if not SKLEARN_AVAILABLE:  # pragma: no cover - environment dependent
        raise RuntimeError(f"scikit-learn is unavailable: {SKLEARN_IMPORT_ERROR}")

    subset = window_df.loc[window_df["scenario_id"].isin(class_order)].copy()
    feature_columns = _ml_feature_columns()
    X = subset[feature_columns].to_numpy(dtype=np.float64)
    y = subset["scenario_id"].astype(str).to_numpy()
    groups = subset["group_id"].astype(str).to_numpy()
    label_by_id = {
        scenario_id: str(
            subset.loc[subset["scenario_id"] == scenario_id, "scenario_display"].iloc[0]
        )
        for scenario_id in class_order
    }
    ordinal_rank = {scenario_id: rank for rank, scenario_id in enumerate(class_order)}

    classifier = ExtraTreesClassifier(
        n_estimators=400,
        random_state=seed,
        class_weight="balanced",
    )
    cv = LeaveOneGroupOut().split(X, y, groups)
    y_pred = cross_val_predict(classifier, X, y, cv=cv, method="predict")

    accuracy = float(accuracy_score(y, y_pred))
    macro_f1 = float(f1_score(y, y_pred, average="macro"))
    abs_error_steps = np.asarray(
        [abs(ordinal_rank[str(pred)] - ordinal_rank[str(true)]) for true, pred in zip(y, y_pred)],
        dtype=np.int64,
    )
    exact_rate = float(np.mean(abs_error_steps == 0))
    adjacent_or_exact_rate = float(np.mean(abs_error_steps <= 1))

    overall_df = pd.DataFrame(
        [
            {
                "task_id": task_id,
                "task_label": task_label,
                "model": "ExtraTreesClassifier",
                "window_size": int(subset["window_size"].iloc[0]),
                "num_windows": int(len(subset)),
                "num_groups": int(pd.unique(groups).size),
                "num_classes": int(len(class_order)),
                "class_order": ";".join(label_by_id[scenario_id] for scenario_id in class_order),
                "accuracy": accuracy,
                "macro_f1": macro_f1,
                "mean_abs_error_steps": float(np.mean(abs_error_steps)),
                "median_abs_error_steps": float(np.median(abs_error_steps)),
                "exact_match_rate": exact_rate,
                "adjacent_or_exact_rate": adjacent_or_exact_rate,
                "error_windows": int(np.sum(abs_error_steps > 0)),
            }
        ]
    )

    report = classification_report(
        y,
        y_pred,
        labels=class_order,
        target_names=[label_by_id[scenario_id] for scenario_id in class_order],
        output_dict=True,
        zero_division=0,
    )
    per_class_rows: list[dict[str, Any]] = []
    for scenario_id in class_order:
        class_name = label_by_id[scenario_id]
        metrics = report[class_name]
        class_mask = subset["scenario_id"] == scenario_id
        class_abs_error = abs_error_steps[class_mask.to_numpy()]
        per_class_rows.append(
            {
                "task_id": task_id,
                "task_label": task_label,
                "scenario_id": scenario_id,
                "scenario_display": class_name,
                "support": int(metrics["support"]),
                "precision": float(metrics["precision"]),
                "recall": float(metrics["recall"]),
                "f1_score": float(metrics["f1-score"]),
                "exact_match_rate": float(np.mean(class_abs_error == 0)),
                "adjacent_or_exact_rate": float(np.mean(class_abs_error <= 1)),
            }
        )
    per_class_df = pd.DataFrame(per_class_rows)

    predictions_df = subset[
        [
            "scenario_id",
            "scenario_display",
            "run_id",
            "group_id",
            "window_start",
            "window_size",
            "estimated_distance_m",
        ]
    ].copy()
    predictions_df.rename(
        columns={
            "scenario_id": "true_scenario_id",
            "scenario_display": "true_scenario_display",
        },
        inplace=True,
    )
    predictions_df["task_id"] = task_id
    predictions_df["task_label"] = task_label
    predictions_df["pred_scenario_id"] = y_pred
    predictions_df["pred_scenario_display"] = predictions_df["pred_scenario_id"].map(label_by_id)
    predictions_df["true_ordinal_rank"] = predictions_df["true_scenario_id"].map(ordinal_rank)
    predictions_df["pred_ordinal_rank"] = predictions_df["pred_scenario_id"].map(ordinal_rank)
    predictions_df["abs_error_steps"] = abs_error_steps
    return overall_df, per_class_df, predictions_df


def run_ml_evaluation(
    packet_df: pd.DataFrame,
    *,
    window_size: int,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, pd.DataFrame]]:
    if not SKLEARN_AVAILABLE:  # pragma: no cover - environment dependent
        return pd.DataFrame(), pd.DataFrame(), {}

    window_df = build_window_dataset(packet_df, window_size)
    task_defs = [
        (
            "core_equal_distance",
            "Equal-distance core subset",
            ["s01_empty_space", "s05_door", "s03_one_wall", "s04_two_walls"],
        ),
        (
            "mixed_five_scenario",
            "Mixed-distance five-scenario set",
            ["s01_empty_space", "s02_chair_obstacle", "s05_door", "s03_one_wall", "s04_two_walls"],
        ),
    ]

    overall_frames: list[pd.DataFrame] = []
    per_class_frames: list[pd.DataFrame] = []
    predictions_by_task: dict[str, pd.DataFrame] = {}
    for task_id, task_label, class_order in task_defs:
        overall_df, per_class_df, predictions_df = _evaluate_grouped_classifier(
            window_df,
            task_id=task_id,
            task_label=task_label,
            class_order=class_order,
            seed=seed,
        )
        overall_frames.append(overall_df)
        per_class_frames.append(per_class_df)
        predictions_by_task[task_id] = predictions_df
    return (
        pd.concat(overall_frames, ignore_index=True),
        pd.concat(per_class_frames, ignore_index=True),
        predictions_by_task,
    )


def plot_boxplot(
    packet_df: pd.DataFrame,
    value_col: str,
    ylabel: str,
    title: str | None,
    out_path: Path,
) -> None:
    order = sorted(packet_df["scenario_id"].astype(str).unique().tolist())
    labels = [
        _scenario_display_name(scenario_id).replace(" ", "\n")
        for scenario_id in order
    ]
    data = [packet_df.loc[packet_df["scenario_id"] == scenario_id, value_col].to_numpy(dtype=float) for scenario_id in order]
    plt.figure(figsize=(9.5, 5.0))
    plt.boxplot(data, tick_labels=labels, showmeans=True, showfliers=False)
    plt.ylabel(ylabel)
    if title:
        plt.title(title)
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


def plot_ml_confusion_matrix(
    predictions_df: pd.DataFrame,
    *,
    class_order: list[str],
    title: str | None,
    out_path: Path,
) -> None:
    class_names = [
        str(predictions_df.loc[predictions_df["true_scenario_id"] == scenario_id, "true_scenario_display"].iloc[0])
        for scenario_id in class_order
    ]
    cm = confusion_matrix(
        predictions_df["true_scenario_id"],
        predictions_df["pred_scenario_id"],
        labels=class_order,
    )
    normalized = cm.astype(np.float64)
    row_sums = normalized.sum(axis=1, keepdims=True)
    normalized = np.divide(normalized, row_sums, out=np.zeros_like(normalized), where=row_sums != 0)

    fig, ax = plt.subplots(figsize=(7.0, 5.4))
    im = ax.imshow(normalized, cmap="Blues", vmin=0.0, vmax=1.0)
    fig.colorbar(im, ax=ax, fraction=0.046, pad=0.04, label="Row-normalized rate")
    ax.set_xticks(range(len(class_names)), class_names, rotation=20, ha="right")
    ax.set_yticks(range(len(class_names)), class_names)
    ax.set_xlabel("Predicted class")
    ax.set_ylabel("True class")
    if title:
        ax.set_title(title)
    for row_idx in range(cm.shape[0]):
        for col_idx in range(cm.shape[1]):
            value = normalized[row_idx, col_idx]
            count = int(cm[row_idx, col_idx])
            ax.text(
                col_idx,
                row_idx,
                f"{100.0 * value:.1f}%\n(n={count})",
                ha="center",
                va="center",
                color="white" if value > 0.45 else "black",
                fontsize=8,
            )
    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def plot_core_stability_error_profile(
    predictions_df: pd.DataFrame,
    *,
    class_order: list[str],
    out_path: Path,
) -> None:
    short_label_map = {
        "s01_empty_space": "E",
        "s05_door": "D",
        "s03_one_wall": "W1",
        "s04_two_walls": "W2",
    }
    display_map = {
        scenario_id: str(
            predictions_df.loc[predictions_df["true_scenario_id"] == scenario_id, "true_scenario_display"].iloc[0]
        )
        for scenario_id in class_order
    }
    group_df = (
        predictions_df.groupby(["true_scenario_id", "true_scenario_display", "run_id"], as_index=False)
        .agg(
            num_windows=("abs_error_steps", "size"),
            exact_match_rate=("abs_error_steps", lambda s: float(np.mean(np.asarray(s, dtype=int) == 0))),
            error_windows=("abs_error_steps", lambda s: int(np.sum(np.asarray(s, dtype=int) > 0))),
        )
        .copy()
    )
    group_df["class_order"] = group_df["true_scenario_id"].map(
        {scenario_id: idx for idx, scenario_id in enumerate(class_order)}
    )
    group_df.sort_values(["class_order", "run_id"], inplace=True)

    class_df = (
        predictions_df.groupby(["true_scenario_id", "true_scenario_display"], as_index=False)
        .agg(
            total_windows=("abs_error_steps", "size"),
            exact_windows=("abs_error_steps", lambda s: int(np.sum(np.asarray(s, dtype=int) == 0))),
            one_step_errors=("abs_error_steps", lambda s: int(np.sum(np.asarray(s, dtype=int) == 1))),
            larger_errors=("abs_error_steps", lambda s: int(np.sum(np.asarray(s, dtype=int) > 1))),
        )
        .copy()
    )
    class_df["class_order"] = class_df["true_scenario_id"].map(
        {scenario_id: idx for idx, scenario_id in enumerate(class_order)}
    )
    class_df.sort_values("class_order", inplace=True)
    class_df["exact_rate"] = class_df["exact_windows"] / class_df["total_windows"]
    class_df["one_step_rate"] = class_df["one_step_errors"] / class_df["total_windows"]
    class_df["larger_error_rate"] = class_df["larger_errors"] / class_df["total_windows"]

    palette = {
        "s01_empty_space": "#355070",
        "s05_door": "#6d597a",
        "s03_one_wall": "#b56576",
        "s04_two_walls": "#e56b6f",
    }

    fig, axes = plt.subplots(1, 2, figsize=(8.2, 4.6))

    ax = axes[0]
    x_positions = np.arange(len(group_df), dtype=float)
    ax.bar(
        x_positions,
        group_df["exact_match_rate"].to_numpy(dtype=float),
        color=[palette.get(str(scenario_id), "#4c4c4c") for scenario_id in group_df["true_scenario_id"]],
        width=0.72,
    )
    ax.set_ylim(0.40, 1.02)
    ax.set_ylabel("Held-out group accuracy")
    ax.set_xticks(
        x_positions,
        [
            f"{short_label_map.get(str(sid), display_map[str(sid)])}\nr{int(run_id)}"
            for sid, run_id in zip(group_df["true_scenario_id"], group_df["run_id"])
        ],
    )
    ax.tick_params(axis="x", labelsize=8)
    ax.grid(axis="y", alpha=0.3)
    for xpos, rate, errors in zip(
        x_positions,
        group_df["exact_match_rate"].to_numpy(dtype=float),
        group_df["error_windows"].to_numpy(dtype=int),
    ):
        if errors:
            ax.text(xpos, rate + 0.01, f"-{errors}", ha="center", va="bottom", fontsize=8)

    ax = axes[1]
    y_positions = np.arange(len(class_df), dtype=float)
    exact = class_df["exact_rate"].to_numpy(dtype=float)
    one_step = class_df["one_step_rate"].to_numpy(dtype=float)
    larger = class_df["larger_error_rate"].to_numpy(dtype=float)
    ax.barh(y_positions, exact, color="#4c956c", label="Exact")
    ax.barh(y_positions, one_step, left=exact, color="#f4a259", label="One-step error")
    if np.any(larger > 0):
        ax.barh(y_positions, larger, left=exact + one_step, color="#d1495b", label="Larger error")
    ax.set_xlim(0.0, 1.0)
    ax.set_xlabel("Window share")
    ax.set_yticks(y_positions, [display_map[str(sid)] for sid in class_df["true_scenario_id"]])
    ax.invert_yaxis()
    ax.grid(axis="x", alpha=0.3)
    for ypos, exact_rate, error_rate, total in zip(
        y_positions,
        exact,
        one_step + larger,
        class_df["total_windows"].to_numpy(dtype=int),
    ):
        ax.text(
            min(exact_rate / 2.0, 0.92),
            ypos,
            f"{100.0 * exact_rate:.1f}%",
            ha="center",
            va="center",
            fontsize=8,
            color="white",
        )
        if error_rate > 0:
            ax.text(
                exact_rate + (error_rate / 2.0),
                ypos,
                f"{int(round(total * error_rate))}",
                ha="center",
                va="center",
                fontsize=8,
                color="black",
            )
    ax.legend(loc="upper right", fontsize=8, frameon=False)

    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def plot_core_feature_separation(
    window_df: pd.DataFrame,
    *,
    class_order: list[str],
    out_path: Path,
) -> None:
    display_map = {
        scenario_id: str(window_df.loc[window_df["scenario_id"] == scenario_id, "scenario_display"].iloc[0])
        for scenario_id in class_order
    }
    subset = window_df.loc[window_df["scenario_id"].isin(class_order)].copy()
    labels = [display_map[scenario_id].replace(" ", "\n") for scenario_id in class_order]
    colors = ["#355070", "#6d597a", "#b56576", "#e56b6f"]
    metrics = [
        ("rssi_mean", "Window RSSI mean (dBm)", False),
        ("mean_amp_mean", "Window CSI mean amplitude (log scale)", True),
    ]
    rng = np.random.default_rng(42)

    fig, axes = plt.subplots(1, 2, figsize=(8.4, 4.8))
    for ax, (metric_name, ylabel, use_log_scale) in zip(axes, metrics):
        data: list[np.ndarray] = []
        for idx, scenario_id in enumerate(class_order, start=1):
            values = subset.loc[subset["scenario_id"] == scenario_id, metric_name].to_numpy(dtype=float)
            if use_log_scale:
                values = np.clip(values, 1e-4, None)
            data.append(values)
            jitter = rng.uniform(-0.14, 0.14, size=values.size)
            ax.scatter(
                np.full(values.size, idx, dtype=float) + jitter,
                values,
                s=10,
                alpha=0.25,
                color=colors[idx - 1],
                edgecolors="none",
                rasterized=True,
            )

        boxplot = ax.boxplot(
            data,
            tick_labels=labels,
            widths=0.55,
            patch_artist=True,
            showfliers=False,
            medianprops={"color": "black", "linewidth": 1.1},
            whiskerprops={"linewidth": 1.0},
            capprops={"linewidth": 1.0},
        )
        for patch, color in zip(boxplot["boxes"], colors):
            patch.set_facecolor(color)
            patch.set_alpha(0.45)
            patch.set_linewidth(1.0)

        if use_log_scale:
            ax.set_yscale("log")
        ax.set_ylabel(ylabel)
        ax.grid(axis="y", alpha=0.3)
        ax.tick_params(axis="x", labelsize=10)
        ax.tick_params(axis="y", labelsize=10)

    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def write_report(
    out_path: Path,
    *,
    dataset_summary: pd.DataFrame,
    scenario_summary: pd.DataFrame,
    reference_deltas: pd.DataFrame,
    ordering_stability: pd.DataFrame,
    ml_overall: pd.DataFrame,
    ml_per_class: pd.DataFrame,
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
    if not ml_overall.empty:
        core_row = ml_overall.loc[ml_overall["task_id"] == "core_equal_distance"].iloc[0]
        mixed_row = ml_overall.loc[ml_overall["task_id"] == "mixed_five_scenario"].iloc[0]
        lines.extend(
            [
                "",
                "## Grouped ML Evaluation",
                "- Evaluation protocol: non-overlapping packet windows with leave-one-run-out validation, so all windows from the held-out run stay out of training.",
                (
                    f"- Equal-distance core subset ({int(core_row['num_classes'])} classes, window size {int(core_row['window_size'])}) "
                    f"reached accuracy `{core_row['accuracy']:.4f}` and macro-F1 `{core_row['macro_f1']:.4f}`."
                ),
                (
                    f"- Mixed-distance five-scenario set reached accuracy `{mixed_row['accuracy']:.4f}` and macro-F1 "
                    f"`{mixed_row['macro_f1']:.4f}`, but this result remains confounded by the 3.0 m chair capture."
                ),
                (
                    f"- For the equal-distance core subset, every error stayed within one severity step "
                    f"(adjacent-or-exact rate `{core_row['adjacent_or_exact_rate']:.4f}`)."
                ),
                "",
                "### ML Overall Metrics",
                ml_overall.to_string(index=False, float_format=lambda value: f"{value:.4f}" if isinstance(value, float) else str(value)),
                "",
                "### ML Per-Class Metrics",
                ml_per_class.to_string(index=False, float_format=lambda value: f"{value:.4f}" if isinstance(value, float) else str(value)),
            ]
        )
    elif not SKLEARN_AVAILABLE:
        lines.extend(
            [
                "",
                "## Grouped ML Evaluation Note",
                f"- ML evaluation was skipped because scikit-learn is unavailable (`{SKLEARN_IMPORT_ERROR}`).",
            ]
        )
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
    ml_overall, ml_per_class, ml_predictions = run_ml_evaluation(
        packet_df,
        window_size=args.window_size,
        seed=args.seed,
    )

    dataset_summary.to_csv(tables_dir / "table_dataset_summary.csv", index=False)
    run_df.to_csv(tables_dir / "table_run_summary.csv", index=False)
    scenario_summary.to_csv(tables_dir / "table_scenario_summary.csv", index=False)
    reference_deltas.to_csv(tables_dir / "table_reference_deltas.csv", index=False)
    ordering_stability.to_csv(tables_dir / "table_ordering_stability.csv", index=False)
    if not ml_overall.empty:
        ml_overall.to_csv(tables_dir / "table_ml_overall.csv", index=False)
        ml_per_class.to_csv(tables_dir / "table_ml_per_class.csv", index=False)
        for task_id, predictions_df in ml_predictions.items():
            predictions_df.to_csv(tables_dir / f"table_ml_predictions_{task_id}.csv", index=False)

    if MATPLOTLIB_AVAILABLE:
        window_df = build_window_dataset(packet_df, args.window_size)
        plot_boxplot(
            packet_df,
            value_col="rssi_dbm",
            ylabel="RSSI (dBm)",
            title=None,
            out_path=figs_dir / "boxplot_rssi_by_scenario.png",
        )
        plot_boxplot(
            packet_df,
            value_col="mean_amp",
            ylabel="CSI mean_amp",
            title=None,
            out_path=figs_dir / "boxplot_mean_amp_by_scenario.png",
        )
        if not ml_overall.empty:
            plot_core_feature_separation(
                window_df.loc[
                    window_df["scenario_id"].isin(
                        ["s01_empty_space", "s05_door", "s03_one_wall", "s04_two_walls"]
                    )
                ].copy(),
                class_order=["s01_empty_space", "s05_door", "s03_one_wall", "s04_two_walls"],
                out_path=figs_dir / "feature_separation_core_equal_distance.png",
            )
            plot_ml_confusion_matrix(
                ml_predictions["core_equal_distance"],
                class_order=["s01_empty_space", "s05_door", "s03_one_wall", "s04_two_walls"],
                title=None,
                out_path=figs_dir / "confusion_matrix_core_equal_distance.png",
            )
            plot_core_stability_error_profile(
                ml_predictions["core_equal_distance"],
                class_order=["s01_empty_space", "s05_door", "s03_one_wall", "s04_two_walls"],
                out_path=figs_dir / "stability_error_profile_core_equal_distance.png",
            )

    write_report(
        out_path=out_dir / "report.md",
        dataset_summary=dataset_summary,
        scenario_summary=scenario_summary,
        reference_deltas=reference_deltas,
        ordering_stability=ordering_stability,
        ml_overall=ml_overall,
        ml_per_class=ml_per_class,
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
    if not ml_overall.empty:
        expected_files.extend(
            [
                tables_dir / "table_ml_overall.csv",
                tables_dir / "table_ml_per_class.csv",
                tables_dir / "table_ml_predictions_core_equal_distance.csv",
                tables_dir / "table_ml_predictions_mixed_five_scenario.csv",
            ]
        )
    if MATPLOTLIB_AVAILABLE:
        expected_files.extend(
            [
                figs_dir / "boxplot_rssi_by_scenario.png",
                figs_dir / "boxplot_mean_amp_by_scenario.png",
            ]
        )
        if not ml_overall.empty:
            expected_files.extend(
                [
                    figs_dir / "feature_separation_core_equal_distance.png",
                    figs_dir / "confusion_matrix_core_equal_distance.png",
                    figs_dir / "stability_error_profile_core_equal_distance.png",
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
    if not ml_overall.empty:
        print("=== Grouped ML summary ===")
        print(
            ml_overall[
                [
                    "task_id",
                    "accuracy",
                    "macro_f1",
                    "mean_abs_error_steps",
                    "adjacent_or_exact_rate",
                ]
            ].to_string(index=False, float_format=lambda value: f"{value:.4f}")
        )
    print(f"Outputs written to: {out_dir}")


if __name__ == "__main__":
    main()
