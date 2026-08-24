#!/usr/bin/env python3
"""Stability/statistics analysis of RSSI and CSI on ESP32 logs."""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from csi_capture.analysis.common import (
    decode_payload_bytes,
    discover_files,
    discover_test_case_files,
    infer_distance_from_path,
    infer_run_id_from_path,
    infer_scenario_from_path,
    iter_records,
    normalize_scenario,
    parse_csi_interleaved,
    parse_numeric_array,
)

try:
    import matplotlib.pyplot as plt

    MATPLOTLIB_AVAILABLE = True
    MATPLOTLIB_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - environment-dependent
    plt = None  # type: ignore[assignment]
    MATPLOTLIB_AVAILABLE = False
    MATPLOTLIB_IMPORT_ERROR = exc

try:
    from sklearn.decomposition import PCA
    from sklearn.metrics import silhouette_score
    from sklearn.preprocessing import StandardScaler

    SKLEARN_AVAILABLE = True
    SKLEARN_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - environment-dependent
    PCA = None  # type: ignore[assignment]
    silhouette_score = None  # type: ignore[assignment]
    StandardScaler = None  # type: ignore[assignment]
    SKLEARN_AVAILABLE = False
    SKLEARN_IMPORT_ERROR = exc

LOGGER = logging.getLogger("wifi_channel_stats")

EPS = 1e-8


def _require_runtime_stack() -> None:
    if not MATPLOTLIB_AVAILABLE:
        raise RuntimeError(
            "matplotlib is unavailable for plotting; fix the local scientific Python stack."
        ) from MATPLOTLIB_IMPORT_ERROR
    if not SKLEARN_AVAILABLE:
        raise RuntimeError(
            "scikit-learn is unavailable for stability analysis; fix the local scientific Python stack."
        ) from SKLEARN_IMPORT_ERROR


def parse_args() -> argparse.Namespace:
    """Parse CLI options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data_dir", required=True, help="Input dataset root.")
    parser.add_argument("--out_dir", required=True, help="Experiment-owned output directory.")
    parser.add_argument(
        "--test_case_id",
        default="",
        help="Optional experiment-local test case used to select direct run directories.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument(
        "--window_sizes",
        default="25,50,100",
        help="Comma-separated rolling windows for temporal stats.",
    )
    parser.add_argument("--acf_max_lag", type=int, default=200, help="Maximum ACF lag.")
    parser.add_argument(
        "--scenario_collapse",
        default="true",
        choices=("true", "false"),
        help="If true, collapse labels to LoS vs NLoS for separability analysis.",
    )
    parser.add_argument(
        "--distance_focus",
        type=float,
        default=None,
        help="Distance to use for temporal analysis; if missing use most frequent distance.",
    )
    return parser.parse_args()


def discover_input_files(data_dir: Path) -> list[Path]:
    """Find CSV/JSONL files recursively."""
    return discover_files(data_dir, suffixes={".csv", ".jsonl"})


def parse_float(value: Any, field_name: str) -> float:
    """Parse float with explicit error."""
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name} value: {value!r}") from exc


def skewness_fisher(values: np.ndarray) -> float:
    """Fisher skewness g1 = E[(x-mu)^3] / sigma^3."""
    if values.size == 0:
        return float("nan")
    centered = values - np.mean(values)
    var = np.mean(centered * centered)
    if var <= 0.0:
        return 0.0
    m3 = np.mean(centered * centered * centered)
    return float(m3 / ((var ** 1.5) + EPS))


def kurtosis_excess_fisher(values: np.ndarray) -> float:
    """Fisher excess kurtosis = E[(x-mu)^4]/sigma^4 - 3."""
    if values.size == 0:
        return float("nan")
    centered = values - np.mean(values)
    var = np.mean(centered * centered)
    if var <= 0.0:
        return 0.0
    m4 = np.mean(centered**4)
    return float((m4 / ((var * var) + EPS)) - 3.0)


def build_dataset(files: Iterable[Path]) -> pd.DataFrame:
    """Build one-row-per-packet dataframe with RSSI/CSI features."""
    rows: list[dict[str, Any]] = []
    total = 0
    bad = 0
    csi_len_seen: set[int] = set()

    for file_path in files:
        for record in iter_records(file_path):
            total += 1
            try:
                run_id = record.get("run_id", infer_run_id_from_path(file_path))
                if run_id is None:
                    raise ValueError("run_id missing and not inferable")
                scenario_raw = record.get("scenario", infer_scenario_from_path(file_path))
                scenario = normalize_scenario(scenario_raw)

                distance_raw = record.get("distance_m", infer_distance_from_path(file_path))
                distance_m = np.nan
                if distance_raw is not None:
                    distance_m = parse_float(distance_raw, "distance_m")

                rssi_dbm = parse_float(record.get("rssi_dbm", record.get("rssi")), "rssi_dbm/rssi")
                interleaved = parse_csi_interleaved(record)
                if interleaved.size < 2:
                    raise ValueError("CSI payload too short")
                if interleaved.size % 2 != 0:
                    interleaved = interleaved[:-1]
                i_vals = interleaved[0::2]
                q_vals = interleaved[1::2]
                amp = np.sqrt(i_vals * i_vals + q_vals * q_vals, dtype=np.float32)
                csi_len_seen.add(amp.size)

                mean_amp = float(np.mean(amp))
                std_amp = float(np.std(amp))
                median_amp = float(np.median(amp))
                q75, q25 = np.percentile(amp, [75.0, 25.0])
                iqr_amp = float(q75 - q25)
                skew_amp = skewness_fisher(amp.astype(np.float64))
                kurt_amp = kurtosis_excess_fisher(amp.astype(np.float64))
                k = max(1, int(math.ceil(0.10 * amp.size)))
                top_idx = np.argpartition(amp, -k)[-k:]
                top10_mean_amp = float(np.mean(amp[top_idx]))
                cv_amp = float(std_amp / (mean_amp + EPS))
                subcarrier_var_mean = float(np.var(amp.astype(np.float64)))

                timestamp_raw = record.get("timestamp", record.get("ts_us"))
                timestamp = np.nan
                if timestamp_raw is not None:
                    try:
                        timestamp = float(timestamp_raw)
                    except (TypeError, ValueError):
                        timestamp = np.nan

                rows.append(
                    {
                        "timestamp": timestamp,
                        "distance_m": distance_m,
                        "scenario": scenario,
                        "scenario_raw": str(scenario_raw) if scenario_raw is not None else "",
                        "run_id": str(run_id),
                        "seq": record.get("seq"),
                        "rssi_dbm": rssi_dbm,
                        "mean_amp": mean_amp,
                        "median_amp": median_amp,
                        "std_amp": std_amp,
                        "iqr_amp": iqr_amp,
                        "skew_amp_fisher": skew_amp,
                        "kurtosis_amp_excess_fisher": kurt_amp,
                        "top10_mean_amp": top10_mean_amp,
                        "subcarrier_var_mean": subcarrier_var_mean,
                        "cv_amp": cv_amp,
                        "source_file": str(file_path),
                    }
                )
            except Exception as exc:  # pylint: disable=broad-except
                bad += 1
                LOGGER.debug("Skipping bad record in %s: %s", file_path, exc)

    if not rows:
        raise ValueError("No valid rows parsed from dataset.")
    frame = pd.DataFrame(rows)

    frame["seq"] = pd.to_numeric(frame["seq"], errors="coerce")
    if frame["seq"].isna().all():
        frame = frame.sort_values(
            by=["scenario", "run_id", "distance_m", "timestamp", "source_file"]
        ).reset_index(drop=True)
        frame["packet_idx"] = frame.groupby(["scenario", "run_id", "distance_m"]).cumcount()
    else:
        frame["packet_idx"] = frame["seq"].fillna(
            frame.groupby(["scenario", "run_id", "distance_m"]).cumcount()
        )
    LOGGER.info(
        "Parsed %d records (%d valid, %d skipped). Unique CSI subcarrier counts: %s",
        total,
        len(frame),
        bad,
        sorted(csi_len_seen),
    )
    return frame


def make_dataset_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """Create dataset summary table."""
    distances = sorted([d for d in frame["distance_m"].dropna().unique()])
    summary = pd.DataFrame(
        [
            {
                "total_packets": int(len(frame)),
                "num_scenarios": int(frame["scenario"].nunique()),
                "scenarios": ";".join(sorted(frame["scenario"].unique())),
                "num_runs": int(frame["run_id"].nunique()),
                "runs": ";".join(sorted(frame["run_id"].astype(str).unique())),
                "num_distances": int(len(distances)),
                "distances_m": ";".join(f"{d:.1f}" for d in distances),
                "num_source_files": int(frame["source_file"].nunique()),
            }
        ]
    )
    return summary


def ecdf(values: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Empirical CDF."""
    sorted_vals = np.sort(values)
    y = np.arange(1, sorted_vals.size + 1, dtype=np.float64) / sorted_vals.size
    return sorted_vals, y


def kde_curve(values: np.ndarray, points: int = 256) -> tuple[np.ndarray, np.ndarray]:
    """Simple Gaussian KDE-like curve using numpy only."""
    x_min = float(np.min(values))
    x_max = float(np.max(values))
    if x_min == x_max:
        x = np.linspace(x_min - 1.0, x_max + 1.0, points)
        y = np.zeros_like(x)
        y[len(y) // 2] = 1.0
        return x, y
    x = np.linspace(x_min, x_max, points)
    n = values.size
    std = float(np.std(values))
    iqr = float(np.percentile(values, 75) - np.percentile(values, 25))
    sigma = min(std, iqr / 1.34) if iqr > 0 else std
    bw = 0.9 * sigma * (n ** (-1.0 / 5.0))
    if bw <= 0:
        bw = (x_max - x_min) / 100.0
    diff = (x[:, None] - values[None, :]) / bw
    y = np.exp(-0.5 * diff * diff).sum(axis=1) / (n * bw * math.sqrt(2.0 * math.pi))
    return x, y


def acf_manual(values: np.ndarray, max_lag: int) -> np.ndarray:
    """Autocorrelation function at lags 0..max_lag."""
    x = values.astype(np.float64)
    x = x - np.mean(x)
    denom = float(np.dot(x, x))
    if denom <= 0:
        return np.ones(max_lag + 1, dtype=np.float64)
    out = np.empty(max_lag + 1, dtype=np.float64)
    out[0] = 1.0
    for lag in range(1, max_lag + 1):
        out[lag] = float(np.dot(x[:-lag], x[lag:]) / denom) if lag < x.size else np.nan
    return out


def first_lag_below(acf_vals: np.ndarray, threshold: float = 0.2) -> int | None:
    """First lag where ACF drops below threshold."""
    valid = np.where(acf_vals[1:] < threshold)[0]
    if valid.size == 0:
        return None
    return int(valid[0] + 1)


def cliffs_delta(a: np.ndarray, b: np.ndarray) -> float:
    """Compute Cliff's delta effect size."""
    if a.size == 0 or b.size == 0:
        return float("nan")
    diff = a[:, None] - b[None, :]
    greater = float(np.sum(diff > 0))
    less = float(np.sum(diff < 0))
    return (greater - less) / float(a.size * b.size)


def percentile_range(values: np.ndarray, low: float = 1.0, high: float = 99.0) -> tuple[float, float]:
    """Percentile-based plotting range helper."""
    lo, hi = np.percentile(values, [low, high])
    lo_f = float(lo)
    hi_f = float(hi)
    if lo_f == hi_f:
        lo_f -= 0.5
        hi_f += 0.5
    return lo_f, hi_f


def save_hist_with_kde(
    values_los: np.ndarray,
    values_nlos: np.ndarray,
    xlabel: str,
    title: str,
    out_path: Path,
) -> None:
    """Save comparative histogram with KDE-like overlays (full + zoom)."""
    combined = np.concatenate([values_los, values_nlos])
    x_zoom_min, x_zoom_max = percentile_range(combined, 1.0, 99.0)
    x_los, y_los = kde_curve(values_los)
    x_nlos, y_nlos = kde_curve(values_nlos)

    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    for axis in axes:
        axis.hist(
            values_los,
            bins=60,
            density=True,
            histtype="stepfilled",
            alpha=0.25,
            label="LoS",
            color="#1f77b4",
        )
        axis.hist(
            values_nlos,
            bins=60,
            density=True,
            histtype="stepfilled",
            alpha=0.25,
            label="NLoS",
            color="#ff7f0e",
        )
        axis.plot(x_los, y_los, linewidth=2, label="LoS KDE-like", color="#2ca02c")
        axis.plot(x_nlos, y_nlos, linewidth=2, label="NLoS KDE-like", color="#d62728")
        axis.grid(True, alpha=0.3)
        axis.set_xlabel(xlabel)
    axes[0].set_title("Full Range")
    axes[0].set_ylabel("Density")
    axes[1].set_title("Zoomed (1st to 99th percentile)")
    axes[1].set_xlim(x_zoom_min, x_zoom_max)
    axes[1].legend()
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def save_ecdf_by_scenario(
    frame: pd.DataFrame, value_col: str, xlabel: str, title: str, out_path: Path
) -> None:
    """Save ECDF lines grouped by scenario (full + zoom)."""
    all_vals = frame[value_col].to_numpy(dtype=float)
    x_zoom_min, x_zoom_max = percentile_range(all_vals, 1.0, 99.0)
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    for scenario, group in frame.groupby("scenario", sort=True):
        vals = group[value_col].to_numpy(dtype=float)
        x, y = ecdf(vals)
        axes[0].plot(x, y, linewidth=2, label=scenario)
        axes[1].plot(x, y, linewidth=2, label=scenario)
    axes[0].set_xlabel(xlabel)
    axes[0].set_ylabel("ECDF")
    axes[0].set_title("Full Range")
    axes[0].grid(True, alpha=0.3)
    axes[1].set_xlabel(xlabel)
    axes[1].set_title("Zoomed (1st to 99th percentile)")
    axes[1].set_xlim(x_zoom_min, x_zoom_max)
    axes[1].grid(True, alpha=0.3)
    axes[1].legend()
    fig.suptitle(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def choose_focus_distance(frame: pd.DataFrame, distance_focus: float | None) -> float:
    """Select distance for temporal analysis."""
    valid = frame["distance_m"].dropna()
    if valid.empty:
        raise ValueError("distance_m is missing for all packets; required for temporal focus.")
    if distance_focus is not None:
        if not np.isclose(valid.to_numpy(), distance_focus).any():
            raise ValueError(
                f"Requested --distance_focus={distance_focus} not present in dataset distances: "
                f"{sorted(valid.unique())}"
            )
        return float(distance_focus)
    mode_distance = valid.mode().iloc[0]
    return float(mode_distance)


def prepare_temporal_subset(frame: pd.DataFrame, focus_distance: float) -> pd.DataFrame:
    """Select one run per scenario at focus distance, keep first 2000 packets per scenario."""
    subsets: list[pd.DataFrame] = []
    for scenario, group in frame[np.isclose(frame["distance_m"], focus_distance)].groupby("scenario"):
        run_counts = group.groupby("run_id").size().sort_values(ascending=False)
        run_id = run_counts.index[0]
        selected = group[group["run_id"] == run_id].sort_values(
            by=["packet_idx", "timestamp"]
        ).head(2000)
        selected = selected.copy()
        selected["focus_run_id"] = run_id
        subsets.append(selected)
    if not subsets:
        raise ValueError("No temporal subsets available for selected distance.")
    return pd.concat(subsets, ignore_index=True)


def save_time_series_plots(temporal_df: pd.DataFrame, out_dir: Path) -> None:
    """Save 2000-packet time-series plots for RSSI and mean amplitude."""
    # RSSI
    fig, axes = plt.subplots(
        nrows=temporal_df["scenario"].nunique(),
        ncols=1,
        figsize=(10, 3.5 * temporal_df["scenario"].nunique()),
        sharex=True,
    )
    if not isinstance(axes, np.ndarray):
        axes = np.asarray([axes])
    for axis, (scenario, group) in zip(axes, temporal_df.groupby("scenario", sort=True)):
        x = np.arange(len(group))
        axis.plot(x, group["rssi_dbm"].to_numpy(dtype=float), linewidth=1.0)
        axis.set_title(f"RSSI time series - {scenario}")
        axis.set_ylabel("RSSI (dBm)")
        axis.grid(True, alpha=0.3)
    axes[-1].set_xlabel("Packet index")
    fig.tight_layout()
    fig.savefig(out_dir / "timeseries_rssi_focus.png", dpi=300)
    plt.close(fig)

    # mean_amp
    fig, axes = plt.subplots(
        nrows=temporal_df["scenario"].nunique(),
        ncols=1,
        figsize=(10, 3.5 * temporal_df["scenario"].nunique()),
        sharex=True,
    )
    if not isinstance(axes, np.ndarray):
        axes = np.asarray([axes])
    for axis, (scenario, group) in zip(axes, temporal_df.groupby("scenario", sort=True)):
        x = np.arange(len(group))
        axis.plot(x, group["mean_amp"].to_numpy(dtype=float), linewidth=1.0, color="#ff7f0e")
        axis.set_title(f"Mean amplitude time series - {scenario}")
        axis.set_ylabel("mean_amp")
        axis.grid(True, alpha=0.3)
    axes[-1].set_xlabel("Packet index")
    fig.tight_layout()
    fig.savefig(out_dir / "timeseries_mean_amp_focus.png", dpi=300)
    plt.close(fig)


def save_rolling_std_plots(
    temporal_df: pd.DataFrame, window_sizes: list[int], out_dir: Path
) -> None:
    """Save rolling STD plots for RSSI and CSI features."""
    scenarios = sorted(temporal_df["scenario"].unique())
    fig, axes = plt.subplots(
        nrows=len(scenarios), ncols=1, figsize=(10, 3.5 * len(scenarios)), sharex=True
    )
    if not isinstance(axes, np.ndarray):
        axes = np.asarray([axes])
    for axis, scenario in zip(axes, scenarios):
        group = temporal_df[temporal_df["scenario"] == scenario].reset_index(drop=True)
        for w in window_sizes:
            roll = group["rssi_dbm"].rolling(window=w, min_periods=w).std()
            axis.plot(roll.to_numpy(dtype=float), label=f"w={w}")
        axis.set_title(f"Rolling STD of RSSI - {scenario}")
        axis.set_ylabel("rolling std (dBm)")
        axis.grid(True, alpha=0.3)
        axis.legend()
    axes[-1].set_xlabel("Packet index")
    fig.tight_layout()
    fig.savefig(out_dir / "rolling_std_rssi.png", dpi=300)
    plt.close(fig)

    fig, axes = plt.subplots(
        nrows=len(scenarios),
        ncols=2,
        figsize=(13, 3.5 * len(scenarios)),
        sharex=True,
        sharey=False,
    )
    if len(scenarios) == 1:
        axes = np.asarray([axes])
    for row_axes, scenario in zip(axes, scenarios):
        axis_mean = row_axes[0]
        axis_cv = row_axes[1]
        group = temporal_df[temporal_df["scenario"] == scenario].reset_index(drop=True)
        for w in window_sizes:
            roll_mean_amp = group["mean_amp"].rolling(window=w, min_periods=w).std()
            roll_cv_amp = group["cv_amp"].rolling(window=w, min_periods=w).std()
            axis_mean.plot(roll_mean_amp.to_numpy(dtype=float), label=f"w={w}")
            axis_cv.plot(roll_cv_amp.to_numpy(dtype=float), label=f"w={w}")
        axis_mean.set_title(f"{scenario} - mean_amp rolling STD")
        axis_mean.set_ylabel("rolling std(mean_amp)")
        axis_mean.grid(True, alpha=0.3)
        axis_mean.legend(fontsize=8)
        axis_cv.set_title(f"{scenario} - CV_amp rolling STD")
        axis_cv.set_ylabel("rolling std(CV_amp)")
        axis_cv.grid(True, alpha=0.3)
        axis_cv.legend(fontsize=8)
    axes[-1][0].set_xlabel("Packet index")
    axes[-1][1].set_xlabel("Packet index")
    fig.tight_layout()
    fig.savefig(out_dir / "rolling_std_csi.png", dpi=300)
    plt.close(fig)


def save_acf_plots(
    temporal_df: pd.DataFrame, acf_max_lag: int, out_dir: Path
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Compute and save ACF plots; return first-lag-below-0.2 tables."""
    scenarios = sorted(temporal_df["scenario"].unique())
    lags = np.arange(0, acf_max_lag + 1)

    lag_rows_rssi: list[dict[str, Any]] = []
    lag_rows_amp: list[dict[str, Any]] = []

    plt.figure(figsize=(8, 5))
    for scenario in scenarios:
        vals = temporal_df[temporal_df["scenario"] == scenario]["rssi_dbm"].to_numpy(dtype=float)
        acf_vals = acf_manual(vals, acf_max_lag)
        plt.plot(lags, acf_vals, linewidth=2, label=scenario)
        lag_rows_rssi.append(
            {"scenario": scenario, "first_lag_acf_below_0p2": first_lag_below(acf_vals, 0.2)}
        )
    plt.axhline(0.2, color="black", linestyle="--", linewidth=1)
    plt.xlabel("Lag")
    plt.ylabel("ACF")
    plt.title("ACF of RSSI")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "acf_rssi.png", dpi=300)
    plt.close()

    plt.figure(figsize=(8, 5))
    for scenario in scenarios:
        vals = temporal_df[temporal_df["scenario"] == scenario]["mean_amp"].to_numpy(dtype=float)
        acf_vals = acf_manual(vals, acf_max_lag)
        plt.plot(lags, acf_vals, linewidth=2, label=scenario)
        lag_rows_amp.append(
            {"scenario": scenario, "first_lag_acf_below_0p2": first_lag_below(acf_vals, 0.2)}
        )
    plt.axhline(0.2, color="black", linestyle="--", linewidth=1)
    plt.xlabel("Lag")
    plt.ylabel("ACF")
    plt.title("ACF of mean_amp")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_dir / "acf_csi_mean_amp.png", dpi=300)
    plt.close()

    return pd.DataFrame(lag_rows_rssi), pd.DataFrame(lag_rows_amp)


def scenario_binary_labels(series: pd.Series) -> np.ndarray:
    """Map scenarios to binary labels: LoS=0, NLoS*=1."""
    labels = np.where(series.to_numpy() == "LoS", 0, 1)
    return labels.astype(int)


def write_report(
    out_path: Path,
    dataset_summary: pd.DataFrame,
    skew_kurt: pd.DataFrame,
    fading_by_scenario: pd.DataFrame,
    separability_scores: pd.DataFrame,
    acf_rssi_lag_table: pd.DataFrame,
    acf_amp_lag_table: pd.DataFrame,
    focus_distance: float,
    multipath_summary: dict[str, float],
) -> None:
    """Write short narrative report for channel statistics."""
    ds = dataset_summary.iloc[0]
    lines = [
        "# Stability Statistics Analysis Report: RSSI and CSI",
        "",
        "This report focuses on measurement/channel statistics only (no distance-estimation error metrics).",
        "",
        "## Dataset",
        f"- Packets: {int(ds['total_packets']):,}",
        f"- Scenarios: {ds['scenarios']}",
        f"- Runs: {ds['runs']}",
        f"- Distances (m): {ds['distances_m']}",
        f"- Temporal analysis focus distance (m): {focus_distance:.1f}",
        "",
        "## Distribution Shape (Fisher moments)",
        "- Skewness and excess kurtosis are provided in `table_skew_kurt_by_scenario.csv`.",
        "- Positive skew indicates heavier right tail; high positive excess kurtosis indicates heavy-tailed behavior.",
        "",
        "## Temporal Stability",
        "- Rolling STD plots (`rolling_std_rssi.png`, `rolling_std_csi.png`) show packet-to-packet variability across windows 25/50/100.",
        "- ACF decay is summarized below (first lag where ACF < 0.2).",
        "",
        "### ACF RSSI first-lag-below-0.2",
        acf_rssi_lag_table.to_string(index=False),
        "",
        "### ACF mean_amp first-lag-below-0.2",
        acf_amp_lag_table.to_string(index=False),
        "",
        "## Fading Depth",
        "- Fading depth definitions: `fd_rssi = p95(rssi)-p5(rssi)`, `fd_amp = p95(mean_amp)-p5(mean_amp)` per (scenario, run, distance).",
        "- Scenario medians are in `table_fading_depth_by_scenario.csv`.",
        "",
        "## Multipath Sensitivity (LoS vs NLoS)",
        f"- Median RSSI IQR difference (NLoS - LoS): {multipath_summary['rssi_iqr_median_diff_nlos_minus_los']:.4f}",
        f"- Cliff's delta for RSSI IQR (NLoS vs LoS): {multipath_summary['rssi_iqr_cliffs_delta_nlos_vs_los']:.4f}",
        f"- Median CV_amp difference (NLoS - LoS): {multipath_summary['cv_amp_median_diff_nlos_minus_los']:.4f}",
        f"- Cliff's delta for CV_amp (NLoS vs LoS): {multipath_summary['cv_amp_cliffs_delta_nlos_vs_los']:.4f}",
        "",
        "## Scenario Separability (LoS vs NLoS)",
        separability_scores.to_string(index=False),
        "",
        "Higher silhouette values indicate better separation.",
    ]
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run(args: argparse.Namespace) -> None:
    """Execute full stability-statistics analysis pipeline."""
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    figs_dir = out_dir / "figs"
    tables_dir = out_dir / "tables"
    figs_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)

    window_sizes = [int(x.strip()) for x in args.window_sizes.split(",") if x.strip()]
    if not window_sizes:
        raise ValueError("No valid --window_sizes provided.")
    if any(w <= 1 for w in window_sizes):
        raise ValueError("All --window_sizes must be > 1.")
    if args.acf_max_lag < 1:
        raise ValueError("--acf_max_lag must be > 0.")

    files = (
        discover_test_case_files(data_dir, args.test_case_id, suffixes={".csv", ".jsonl"})
        if args.test_case_id
        else discover_input_files(data_dir)
    )
    LOGGER.info("Found %d input files.", len(files))
    frame = build_dataset(files)

    # Required fields validation.
    required_cols = {"scenario", "run_id", "rssi_dbm", "mean_amp"}
    missing = required_cols - set(frame.columns)
    if missing:
        raise KeyError(f"Missing required columns after parsing: {sorted(missing)}")

    dataset_summary = make_dataset_summary(frame)
    dataset_summary.to_csv(tables_dir / "table_dataset_summary.csv", index=False)

    # Distribution stats table.
    skew_rows: list[dict[str, Any]] = []
    for scenario, group in frame.groupby("scenario", sort=True):
        rssi_vals = group["rssi_dbm"].to_numpy(dtype=float)
        amp_vals = group["mean_amp"].to_numpy(dtype=float)
        cv_vals = group["cv_amp"].to_numpy(dtype=float)
        skew_rows.append(
            {
                "scenario": scenario,
                "n_packets": int(len(group)),
                "rssi_skew_fisher": skewness_fisher(rssi_vals),
                "rssi_kurtosis_excess_fisher": kurtosis_excess_fisher(rssi_vals),
                "mean_amp_skew_fisher": skewness_fisher(amp_vals),
                "mean_amp_kurtosis_excess_fisher": kurtosis_excess_fisher(amp_vals),
                "cv_amp_skew_fisher": skewness_fisher(cv_vals),
                "cv_amp_kurtosis_excess_fisher": kurtosis_excess_fisher(cv_vals),
            }
        )
    skew_kurt_df = pd.DataFrame(skew_rows)
    skew_kurt_df.to_csv(tables_dir / "table_skew_kurt_by_scenario.csv", index=False)

    # ECDF plots.
    save_ecdf_by_scenario(
        frame,
        value_col="rssi_dbm",
        xlabel="RSSI (dBm)",
        title="ECDF of RSSI by Scenario",
        out_path=figs_dir / "ecdf_rssi_by_scenario.png",
    )
    save_ecdf_by_scenario(
        frame,
        value_col="mean_amp",
        xlabel="CSI mean_amp",
        title="ECDF of CSI mean_amp by Scenario",
        out_path=figs_dir / "ecdf_csi_mean_amp_by_scenario.png",
    )

    # Hist + KDE-like LoS vs NLoS.
    los_mask = frame["scenario"] == "LoS"
    nlos_mask = ~los_mask
    if not np.any(los_mask) or not np.any(nlos_mask):
        raise ValueError("LoS/NLoS split is empty; cannot run separability/hist comparison.")

    save_hist_with_kde(
        values_los=frame.loc[los_mask, "rssi_dbm"].to_numpy(dtype=float),
        values_nlos=frame.loc[nlos_mask, "rssi_dbm"].to_numpy(dtype=float),
        xlabel="RSSI (dBm)",
        title="RSSI Distribution: LoS vs NLoS",
        out_path=figs_dir / "hist_rssi_los_vs_nlos.png",
    )
    save_hist_with_kde(
        values_los=frame.loc[los_mask, "cv_amp"].to_numpy(dtype=float),
        values_nlos=frame.loc[nlos_mask, "cv_amp"].to_numpy(dtype=float),
        xlabel="CV_amp = std_amp / mean_amp",
        title="CSI CV_amp Distribution: LoS vs NLoS",
        out_path=figs_dir / "hist_csi_cv_los_vs_nlos.png",
    )

    # Temporal analysis.
    focus_distance = choose_focus_distance(frame, args.distance_focus)
    temporal_df = prepare_temporal_subset(frame, focus_distance=focus_distance)
    save_time_series_plots(temporal_df, figs_dir)
    save_rolling_std_plots(temporal_df, window_sizes, figs_dir)
    acf_rssi_lag_table, acf_amp_lag_table = save_acf_plots(
        temporal_df, acf_max_lag=args.acf_max_lag, out_dir=figs_dir
    )

    # Fading depth.
    fading_group = frame.groupby(["scenario", "run_id", "distance_m"], dropna=False, as_index=False).agg(
        rssi_p95=("rssi_dbm", lambda x: np.percentile(x, 95)),
        rssi_p5=("rssi_dbm", lambda x: np.percentile(x, 5)),
        amp_p95=("mean_amp", lambda x: np.percentile(x, 95)),
        amp_p5=("mean_amp", lambda x: np.percentile(x, 5)),
    )
    fading_group["fd_rssi"] = fading_group["rssi_p95"] - fading_group["rssi_p5"]
    fading_group["fd_amp"] = fading_group["amp_p95"] - fading_group["amp_p5"]
    fading_by_scenario = (
        fading_group.groupby("scenario", as_index=False)
        .agg(
            fd_rssi_median=("fd_rssi", "median"),
            fd_amp_median=("fd_amp", "median"),
            n_groups=("fd_rssi", "size"),
        )
        .sort_values("scenario")
    )
    fading_by_scenario.to_csv(tables_dir / "table_fading_depth_by_scenario.csv", index=False)

    plt.figure(figsize=(8, 5))
    order = sorted(fading_group["scenario"].unique())
    data = [fading_group.loc[fading_group["scenario"] == sc, "fd_rssi"].to_numpy(dtype=float) for sc in order]
    plt.boxplot(data, labels=order, showfliers=False)
    plt.ylabel("fd_rssi = p95 - p5 (dB)")
    plt.title("Fading Depth of RSSI by Scenario")
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(figs_dir / "boxplot_fading_depth_rssi.png", dpi=300)
    plt.close()

    plt.figure(figsize=(8, 5))
    data = [fading_group.loc[fading_group["scenario"] == sc, "fd_amp"].to_numpy(dtype=float) for sc in order]
    plt.boxplot(data, labels=order, showfliers=False)
    plt.ylabel("fd_amp = p95 - p5 (mean_amp units)")
    plt.title("Fading Depth of CSI mean_amp by Scenario")
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(figs_dir / "boxplot_fading_depth_csi.png", dpi=300)
    plt.close()

    # Multipath sensitivity effect sizes on grouped features.
    spread_group = frame.groupby(["scenario", "run_id", "distance_m"], dropna=False, as_index=False).agg(
        rssi_iqr=("rssi_dbm", lambda x: np.percentile(x, 75) - np.percentile(x, 25)),
        cv_amp_median=("cv_amp", "median"),
    )
    spread_group["is_nlos"] = spread_group["scenario"] != "LoS"
    los_spread = spread_group[~spread_group["is_nlos"]]
    nlos_spread = spread_group[spread_group["is_nlos"]]

    multipath_summary = {
        "rssi_iqr_median_diff_nlos_minus_los": float(
            nlos_spread["rssi_iqr"].median() - los_spread["rssi_iqr"].median()
        ),
        "rssi_iqr_cliffs_delta_nlos_vs_los": cliffs_delta(
            nlos_spread["rssi_iqr"].to_numpy(dtype=float),
            los_spread["rssi_iqr"].to_numpy(dtype=float),
        ),
        "cv_amp_median_diff_nlos_minus_los": float(
            nlos_spread["cv_amp_median"].median() - los_spread["cv_amp_median"].median()
        ),
        "cv_amp_cliffs_delta_nlos_vs_los": cliffs_delta(
            nlos_spread["cv_amp_median"].to_numpy(dtype=float),
            los_spread["cv_amp_median"].to_numpy(dtype=float),
        ),
    }

    # Separability: LoS vs NLoS.
    collapse = args.scenario_collapse.lower() == "true"
    if not collapse:
        LOGGER.warning(
            "--scenario_collapse=false is set; silhouette is still computed for binary LoS vs NLoS."
        )
    labels_binary = scenario_binary_labels(frame["scenario"])

    # Exploratory PCA on all standardized features.
    feature_cols_all = [
        "rssi_dbm",
        "mean_amp",
        "std_amp",
        "cv_amp",
        "skew_amp_fisher",
        "kurtosis_amp_excess_fisher",
    ]
    x_all = frame[feature_cols_all].to_numpy(dtype=float)
    scaler = StandardScaler()
    x_scaled = scaler.fit_transform(x_all)
    pca = PCA(n_components=2, random_state=args.seed)
    x_pca = pca.fit_transform(x_scaled)

    rng = np.random.default_rng(args.seed)
    los_idx = np.where(labels_binary == 0)[0]
    nlos_idx = np.where(labels_binary == 1)[0]
    n_plot = min(15000, los_idx.size, nlos_idx.size)
    los_plot_idx = rng.choice(los_idx, size=n_plot, replace=False)
    nlos_plot_idx = rng.choice(nlos_idx, size=n_plot, replace=False)
    plt.figure(figsize=(8, 6))
    plt.scatter(x_pca[los_plot_idx, 0], x_pca[los_plot_idx, 1], s=8, alpha=0.30, label="LoS")
    plt.scatter(x_pca[nlos_plot_idx, 0], x_pca[nlos_plot_idx, 1], s=8, alpha=0.30, label="NLoS")
    los_center = np.mean(x_pca[los_idx], axis=0)
    nlos_center = np.mean(x_pca[nlos_idx], axis=0)
    plt.scatter([los_center[0]], [los_center[1]], marker="X", s=120, color="#1f77b4", edgecolor="black")
    plt.scatter([nlos_center[0]], [nlos_center[1]], marker="X", s=120, color="#ff7f0e", edgecolor="black")
    plt.xlabel("PC1")
    plt.ylabel("PC2")
    plt.title(
        "PCA Scatter: LoS vs NLoS "
        f"(balanced sample n={n_plot} each, EVR={pca.explained_variance_ratio_[0]:.3f}/{pca.explained_variance_ratio_[1]:.3f})"
    )
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(figs_dir / "pca_separability.png", dpi=300)
    plt.close()

    # Silhouette scores with deterministic sampling (O(N^2) otherwise).
    sample_size = min(10000, len(frame))
    sil_rows: list[dict[str, Any]] = []
    feature_sets = {
        "RSSI_only": frame[["rssi_dbm"]].to_numpy(dtype=float),
        "CSI_only": frame[
            ["mean_amp", "std_amp", "cv_amp", "skew_amp_fisher", "kurtosis_amp_excess_fisher"]
        ].to_numpy(dtype=float),
        "Combined": x_all,
    }
    for name, x_vals in feature_sets.items():
        x_std = StandardScaler().fit_transform(x_vals)
        score = silhouette_score(
            x_std,
            labels_binary,
            metric="euclidean",
            sample_size=sample_size,
            random_state=args.seed,
        )
        sil_rows.append(
            {
                "feature_set": name,
                "silhouette_score_los_vs_nlos": float(score),
                "sample_size_used": int(sample_size),
            }
        )
    separability_scores = pd.DataFrame(sil_rows).sort_values(
        by="silhouette_score_los_vs_nlos", ascending=False
    )
    separability_scores.to_csv(tables_dir / "table_separability_scores.csv", index=False)

    write_report(
        out_path=out_dir / "report.md",
        dataset_summary=dataset_summary,
        skew_kurt=skew_kurt_df,
        fading_by_scenario=fading_by_scenario,
        separability_scores=separability_scores,
        acf_rssi_lag_table=acf_rssi_lag_table,
        acf_amp_lag_table=acf_amp_lag_table,
        focus_distance=focus_distance,
        multipath_summary=multipath_summary,
    )

    expected_files = [
        tables_dir / "table_dataset_summary.csv",
        tables_dir / "table_skew_kurt_by_scenario.csv",
        tables_dir / "table_fading_depth_by_scenario.csv",
        tables_dir / "table_separability_scores.csv",
        figs_dir / "ecdf_rssi_by_scenario.png",
        figs_dir / "ecdf_csi_mean_amp_by_scenario.png",
        figs_dir / "hist_rssi_los_vs_nlos.png",
        figs_dir / "hist_csi_cv_los_vs_nlos.png",
        figs_dir / "rolling_std_rssi.png",
        figs_dir / "rolling_std_csi.png",
        figs_dir / "acf_rssi.png",
        figs_dir / "acf_csi_mean_amp.png",
        figs_dir / "boxplot_fading_depth_rssi.png",
        figs_dir / "boxplot_fading_depth_csi.png",
        figs_dir / "pca_separability.png",
        out_dir / "report.md",
    ]
    missing = [str(p) for p in expected_files if not p.exists()]
    if missing:
        raise FileNotFoundError(f"Missing expected outputs: {missing}")

    print("=== Stability statistics dataset summary ===")
    print(dataset_summary.to_string(index=False))
    print("=== Separability scores (LoS vs NLoS) ===")
    print(separability_scores.to_string(index=False, float_format=lambda x: f"{x:.6f}"))
    print(f"Outputs written to: {out_dir}")


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    _require_runtime_stack()
    run(args)


if __name__ == "__main__":
    main()
