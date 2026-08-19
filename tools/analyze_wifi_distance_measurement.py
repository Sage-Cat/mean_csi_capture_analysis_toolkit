#!/usr/bin/env python3
"""Reproducible ESP32 distance_measurement analysis using RSSI and CSI."""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from csi_capture.analysis.common import (
    DATA_SUFFIXES,
    decode_payload_bytes,
    discover_files,
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
    from sklearn.model_selection import GroupShuffleSplit
    from sklearn.neighbors import KNeighborsRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    SKLEARN_AVAILABLE = True
    SKLEARN_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover - environment-dependent
    PCA = None  # type: ignore[assignment]
    GroupShuffleSplit = None  # type: ignore[assignment]
    KNeighborsRegressor = None  # type: ignore[assignment]
    Pipeline = None  # type: ignore[assignment]
    StandardScaler = None  # type: ignore[assignment]
    SKLEARN_AVAILABLE = False
    SKLEARN_IMPORT_ERROR = exc

LOGGER = logging.getLogger("wifi_distance_measurement_analysis")

METHOD_RSSI = "RSSI_log_distance"
METHOD_RSSI_MED = "RSSI_log_distance_median"
METHOD_CSI_UNIFIED = "CSI_kNN_unified"
METHOD_CSI_PER_SCENARIO = "CSI_kNN_per_scenario"
METHOD_CSI_UNIFIED_FALLBACK = "CSI_ridge_unified"
METHOD_CSI_PER_SCENARIO_FALLBACK = "CSI_ridge_per_scenario"


@dataclass(frozen=True)
class RSSIModel:
    """Log-distance RSSI model parameters."""

    rssi0: float
    n: float
    d0: float = 1.0


def _require_plotting_stack() -> None:
    if not MATPLOTLIB_AVAILABLE:
        raise RuntimeError(
            "matplotlib is unavailable for plotting; fix the local scientific Python stack."
        ) from MATPLOTLIB_IMPORT_ERROR


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--data_dir",
        required=True,
        help="Root directory containing experiment logs (CSV/JSONL/JSON/TXT).",
    )
    parser.add_argument(
        "--out_dir",
        default="../../private/experiments/csi_capture_characterization/analysis/distance_measurement",
        help="Output directory for plots, tables, and report.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument(
        "--use_pca",
        action="store_true",
        help="Use only summary + PCA features for CSI kNN (otherwise include downsampled magnitude).",
    )
    parser.add_argument("--knn_k", type=int, default=7, help="k for kNN regression.")
    parser.add_argument(
        "--test_size",
        type=float,
        default=0.3,
        help="Fraction of groups for test split in GroupShuffleSplit.",
    )
    parser.add_argument(
        "--group_col",
        default="run_id",
        help="Grouping column for leakage-safe split (default: run_id).",
    )
    parser.add_argument(
        "--downsample_step",
        type=int,
        default=4,
        help="Keep every Nth amplitude sample for magnitude-vector features.",
    )
    parser.add_argument(
        "--topk_ratio",
        type=float,
        default=0.10,
        help="Top-K ratio for amplitude topK mean feature.",
    )
    return parser.parse_args()


def parse_float(value: Any, field_name: str) -> float:
    """Parse a float with actionable errors."""
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name} value: {value!r}") from exc


def topk_mean(values: np.ndarray, ratio: float) -> float:
    """Mean of top-K amplitudes, where K = ceil(ratio * N)."""
    k = max(1, int(math.ceil(ratio * values.size)))
    if k >= values.size:
        return float(np.mean(values))
    idx = np.argpartition(values, -k)[-k:]
    return float(np.mean(values[idx]))


def build_packet_dataframe(
    files: Iterable[Path],
    downsample_step: int,
    topk_ratio: float,
) -> tuple[pd.DataFrame, np.ndarray]:
    """Create one-row-per-packet DataFrame and aligned downsampled magnitude matrix."""
    rows: list[dict[str, Any]] = []
    vectors: list[np.ndarray] = []
    parse_failures = 0
    total_records = 0

    for path in files:
        for rec in iter_records(path):
            total_records += 1
            if not isinstance(rec, dict):
                parse_failures += 1
                continue
            try:
                distance_m = parse_float(
                    rec.get("distance_m", infer_distance_from_path(path)), "distance_m"
                )
                rssi_dbm = parse_float(rec.get("rssi_dbm", rec.get("rssi")), "rssi_dbm/rssi")
                scenario_raw = rec.get("scenario", infer_scenario_from_path(path))
                scenario = normalize_scenario(scenario_raw)
                run_id = rec.get("run_id", infer_run_id_from_path(path))
                if run_id is None:
                    raise ValueError(
                        "run_id missing in record and cannot be inferred from path (expected run_<id>)."
                    )
                interleaved = parse_csi_interleaved(rec)
                if interleaved.size < 2:
                    raise ValueError("CSI payload has fewer than 2 elements.")
                if interleaved.size % 2 != 0:
                    interleaved = interleaved[:-1]
                i_vals = interleaved[0::2]
                q_vals = interleaved[1::2]
                amp = np.sqrt(i_vals * i_vals + q_vals * q_vals, dtype=np.float32)
                if amp.size == 0:
                    raise ValueError("CSI payload produced empty amplitude vector.")
                amp_ds = amp[::downsample_step]
                timestamp_val = rec.get("timestamp")
                try:
                    timestamp_num = float(timestamp_val) if timestamp_val is not None else np.nan
                except (TypeError, ValueError):
                    timestamp_num = np.nan
                row = {
                    "timestamp": timestamp_num,
                    "distance_m": distance_m,
                    "scenario": scenario,
                    "scenario_raw": str(scenario_raw) if scenario_raw is not None else "",
                    "run_id": str(run_id),
                    "seq": rec.get("seq"),
                    "rssi_dbm": rssi_dbm,
                    "csi_pairs": int(i_vals.size),
                    "mean_amp": float(np.mean(amp)),
                    "std_amp": float(np.std(amp)),
                    "median_amp": float(np.median(amp)),
                    "topK_amp_mean": topk_mean(amp, topk_ratio),
                    "source_file": str(path),
                }
                rows.append(row)
                vectors.append(amp_ds.astype(np.float32))
            except Exception as exc:  # pylint: disable=broad-except
                parse_failures += 1
                LOGGER.debug("Skipping malformed record from %s: %s", path, exc)

    if not rows:
        raise ValueError("No valid packet records found after parsing. Check input format.")

    length_counts = Counter(len(v) for v in vectors)
    target_len, target_count = length_counts.most_common(1)[0]
    if target_count < len(vectors):
        LOGGER.warning(
            "Keeping dominant downsampled CSI length=%d (%d/%d rows); dropping mismatched rows.",
            target_len,
            target_count,
            len(vectors),
        )
    keep_idx = [idx for idx, vec in enumerate(vectors) if len(vec) == target_len]
    if not keep_idx:
        raise ValueError("No rows with consistent CSI vector length found.")

    filtered_rows = [rows[idx] for idx in keep_idx]
    filtered_vectors = [vectors[idx] for idx in keep_idx]
    frame = pd.DataFrame(filtered_rows)
    matrix = np.vstack(filtered_vectors).astype(np.float32)

    # Build packet sequence if missing.
    frame["seq"] = pd.to_numeric(frame["seq"], errors="coerce")
    if frame["seq"].isna().all():
        frame = frame.sort_values(
            by=["scenario", "run_id", "distance_m", "timestamp", "source_file"]
        ).reset_index(drop=True)
        frame["packet_seq"] = frame.groupby(["scenario", "run_id", "distance_m"]).cumcount()
    else:
        frame["packet_seq"] = frame["seq"].fillna(
            frame.groupby(["scenario", "run_id", "distance_m"]).cumcount()
        )

    frame["rssi_dbm_median_burst"] = frame.groupby(
        ["scenario", "run_id", "distance_m"], sort=False
    )["rssi_dbm"].transform("median")

    LOGGER.info(
        "Parsed %d records (%d valid, %d skipped).",
        total_records,
        len(frame),
        parse_failures,
    )
    LOGGER.info("Downsampled CSI feature length: %d", matrix.shape[1])
    return frame, matrix


def grouped_split(
    frame: pd.DataFrame, group_col: str, test_size: float, seed: int
) -> tuple[np.ndarray, np.ndarray]:
    """Group-aware train/test split to avoid packet leakage."""
    if group_col not in frame.columns:
        available = ", ".join(frame.columns)
        raise KeyError(f"Group column '{group_col}' is missing. Available columns: {available}")

    groups = frame[group_col].astype(str)
    if groups.isna().any():
        raise ValueError(f"Group column '{group_col}' contains missing values.")
    if groups.nunique() < 2:
        raise ValueError(
            f"Need at least 2 unique groups in '{group_col}' for grouped split; got {groups.nunique()}."
        )

    indices = np.arange(len(frame))
    if SKLEARN_AVAILABLE:
        splitter = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=seed)
        train_idx, test_idx = next(splitter.split(indices, groups=groups))
        return train_idx, test_idx

    unique_groups = np.array(sorted(groups.unique()))
    n_groups = unique_groups.size
    n_test_groups = max(1, int(round(test_size * n_groups)))
    n_test_groups = min(n_test_groups, n_groups - 1)
    rng = np.random.default_rng(seed)
    test_groups = set(rng.choice(unique_groups, size=n_test_groups, replace=False).tolist())
    test_mask = groups.isin(test_groups).to_numpy()
    train_idx = np.where(~test_mask)[0]
    test_idx = np.where(test_mask)[0]
    if train_idx.size == 0 or test_idx.size == 0:
        raise ValueError(
            "Fallback grouped split produced an empty train/test partition. "
            "Adjust --test_size or provide more unique groups."
        )
    return train_idx, test_idx


def fit_rssi_model(distance_m: np.ndarray, rssi_dbm: np.ndarray, d0: float = 1.0) -> RSSIModel:
    """Fit log-distance RSSI model using least squares."""
    if np.any(distance_m <= 0):
        raise ValueError("distance_m must be > 0 for log-distance model fitting.")
    x = np.log10(distance_m / d0)
    y = rssi_dbm
    design = np.column_stack([np.ones_like(x), x])
    params, *_ = np.linalg.lstsq(design, y, rcond=None)
    rssi0, slope = params
    n = -slope / 10.0
    if abs(n) < 1e-9:
        raise ValueError("Estimated path-loss exponent is near zero; RSSI model is unstable.")
    return RSSIModel(rssi0=float(rssi0), n=float(n), d0=d0)


def predict_rssi_distance(model: RSSIModel, rssi_dbm: np.ndarray) -> np.ndarray:
    """Predict distance from RSSI using fitted log-distance model."""
    exponent = (model.rssi0 - rssi_dbm) / (10.0 * model.n)
    return model.d0 * np.power(10.0, exponent)


def compute_pca_components(
    amp_matrix: np.ndarray, train_idx: np.ndarray, n_components: int = 3
) -> tuple[np.ndarray, np.ndarray]:
    """Fit PCA on train-only magnitude vectors and transform full set."""
    x_train = amp_matrix[train_idx].astype(np.float64, copy=False)
    if x_train.shape[0] < 1:
        raise ValueError("Insufficient training data to compute PCA components.")

    if SKLEARN_AVAILABLE:
        scaler = StandardScaler()
        x_train_scaled = scaler.fit_transform(x_train)
        components = min(n_components, x_train_scaled.shape[1], x_train_scaled.shape[0])
        if components < 1:
            raise ValueError("Insufficient training data to compute PCA components.")
        pca = PCA(n_components=components)
        pca.fit(x_train_scaled)
        x_scaled_full = scaler.transform(amp_matrix.astype(np.float64, copy=False))
        transformed = pca.transform(x_scaled_full)
        explained_ratio = np.asarray(pca.explained_variance_ratio_, dtype=np.float64)
    else:
        mean = x_train.mean(axis=0)
        std = x_train.std(axis=0)
        std[std == 0.0] = 1.0
        x_train_scaled = (x_train - mean) / std
        x_full_scaled = (amp_matrix.astype(np.float64, copy=False) - mean) / std
        components = min(n_components, x_train_scaled.shape[1], x_train_scaled.shape[0])
        if components < 1:
            raise ValueError("Insufficient training data to compute PCA components.")
        _, singular_values, vt = np.linalg.svd(x_train_scaled, full_matrices=False)
        basis = vt[:components].T
        transformed = x_full_scaled @ basis
        if x_train_scaled.shape[0] > 1:
            variances = (singular_values * singular_values) / (x_train_scaled.shape[0] - 1)
        else:
            variances = singular_values * singular_values
        total_var = float(np.sum(variances))
        if total_var > 0:
            explained_ratio = np.asarray(variances[:components] / total_var, dtype=np.float64)
        else:
            explained_ratio = np.zeros(components, dtype=np.float64)

    if transformed.shape[1] < n_components:
        padded = np.zeros((transformed.shape[0], n_components), dtype=np.float32)
        padded[:, : transformed.shape[1]] = transformed
        transformed = padded
    if explained_ratio.size < n_components:
        ratio_padded = np.zeros(n_components, dtype=np.float64)
        ratio_padded[: explained_ratio.size] = explained_ratio
        explained_ratio = ratio_padded

    return transformed[:, :n_components].astype(np.float32), explained_ratio[:n_components]


def build_csi_feature_matrix(
    frame: pd.DataFrame, amp_matrix: np.ndarray, use_pca_only: bool
) -> np.ndarray:
    """Create CSI model input matrix from summary features and optional magnitude vectors."""
    summary = frame[
        ["mean_amp", "std_amp", "median_amp", "topK_amp_mean", "pca_1", "pca_2", "pca_3"]
    ].to_numpy(dtype=np.float32)
    if use_pca_only:
        return summary
    return np.hstack([summary, amp_matrix])


def fit_predict_knn(
    x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray, k: int
) -> np.ndarray:
    """Fit CSI regressor and return predictions.

    Uses standardized kNN when sklearn is available.
    Falls back to standardized Ridge regression (closed-form) otherwise.
    """
    if x_train.shape[0] == 0:
        raise ValueError("Cannot fit CSI model with zero training samples.")

    if not SKLEARN_AVAILABLE:
        return fit_predict_ridge(x_train, y_train, x_test, alpha=1.0)

    k_eff = max(1, min(k, x_train.shape[0]))
    pipeline = Pipeline(
        [
            ("scaler", StandardScaler()),
            ("knn", KNeighborsRegressor(n_neighbors=k_eff, weights="distance")),
        ]
    )
    pipeline.fit(x_train, y_train)
    return pipeline.predict(x_test)


def fit_predict_ridge(
    x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray, alpha: float = 1.0
) -> np.ndarray:
    """Closed-form Ridge fallback for environments without sklearn."""
    x_train_f = x_train.astype(np.float64, copy=False)
    x_test_f = x_test.astype(np.float64, copy=False)
    y_train_f = y_train.astype(np.float64, copy=False)

    mean = x_train_f.mean(axis=0)
    std = x_train_f.std(axis=0)
    std[std == 0.0] = 1.0

    x_train_s = (x_train_f - mean) / std
    x_test_s = (x_test_f - mean) / std
    y_mean = float(np.mean(y_train_f))
    y_center = y_train_f - y_mean

    n_features = x_train_s.shape[1]
    reg = alpha * np.eye(n_features, dtype=np.float64)
    lhs = x_train_s.T @ x_train_s + reg
    rhs = x_train_s.T @ y_center
    try:
        weights = np.linalg.solve(lhs, rhs)
    except np.linalg.LinAlgError:
        weights, *_ = np.linalg.lstsq(lhs, rhs, rcond=None)

    y_pred = x_test_s @ weights + y_mean
    return y_pred.astype(np.float32)


def metrics_from_predictions(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """Compute MAE, RMSE, std(|error|), p50, p90."""
    error = y_pred - y_true
    abs_error = np.abs(error)
    return {
        "MAE": float(np.mean(abs_error)),
        "RMSE": float(np.sqrt(np.mean(error * error))),
        "STD_ABS": float(np.std(abs_error)),
        "P50_ABS": float(np.percentile(abs_error, 50)),
        "P90_ABS": float(np.percentile(abs_error, 90)),
        "N": int(y_true.size),
    }


def summarize_metrics(
    result_df: pd.DataFrame, method_cols: dict[str, str]
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Create overall and per-scenario metrics tables."""
    overall_rows: list[dict[str, Any]] = []
    scenario_rows: list[dict[str, Any]] = []
    y_true = result_df["distance_m"].to_numpy(dtype=float)
    for method_name, pred_col in method_cols.items():
        y_pred = result_df[pred_col].to_numpy(dtype=float)
        row = {"method": method_name}
        row.update(metrics_from_predictions(y_true, y_pred))
        overall_rows.append(row)

        for scenario, group in result_df.groupby("scenario", sort=True):
            y_s_true = group["distance_m"].to_numpy(dtype=float)
            y_s_pred = group[pred_col].to_numpy(dtype=float)
            srow = {"method": method_name, "scenario": scenario}
            srow.update(metrics_from_predictions(y_s_true, y_s_pred))
            scenario_rows.append(srow)

    overall_df = pd.DataFrame(overall_rows).sort_values(by=["MAE", "RMSE"]).reset_index(drop=True)
    scenario_df = (
        pd.DataFrame(scenario_rows)
        .sort_values(by=["scenario", "MAE", "RMSE"])
        .reset_index(drop=True)
    )
    return overall_df, scenario_df


def plot_cdf_error(
    result_df: pd.DataFrame, method_cols: dict[str, str], out_path: Path
) -> None:
    """Plot CDF of absolute distance error with full+zoomed views."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharey=True)
    all_errors: list[np.ndarray] = []
    for method_name, pred_col in method_cols.items():
        abs_error = np.abs(result_df[pred_col].to_numpy() - result_df["distance_m"].to_numpy())
        sorted_err = np.sort(abs_error)
        cdf = np.arange(1, sorted_err.size + 1) / sorted_err.size
        all_errors.append(abs_error)
        axes[0].plot(sorted_err, cdf, linewidth=2, label=method_name)
        axes[1].plot(sorted_err, cdf, linewidth=2, label=method_name)

    combined = np.concatenate(all_errors)
    nonzero = combined[combined > 0]
    left_min = max(float(np.min(nonzero)) if nonzero.size else 1e-2, 1e-2)
    left_max = float(np.max(combined)) if combined.size else 1.0
    right_max = float(np.max(result_df["distance_m"].to_numpy(dtype=float)) + 1.0)
    if right_max <= 0:
        right_max = 1.0

    axes[0].set_xscale("log")
    axes[0].set_xlim(left_min, max(left_max, left_min * 10.0))
    axes[0].set_xlabel("|Error| (m) [log scale]")
    axes[0].set_ylabel("CDF")
    axes[0].set_title("Full Range")
    axes[0].grid(True, alpha=0.3)

    axes[1].set_xlim(0.0, right_max)
    axes[1].set_xlabel("|Error| (m) [linear]")
    axes[1].set_title("Zoomed (0 to max(true)+1m)")
    axes[1].grid(True, alpha=0.3)
    axes[1].legend()

    fig.suptitle("CDF of Absolute Distance Error")
    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def plot_scatter_pred_vs_true(
    result_df: pd.DataFrame, out_path: Path, csi_method_label: str
) -> None:
    """Scatter plots with robust clipping to keep structure visible."""
    fig, axes = plt.subplots(1, 2, figsize=(12, 5), sharex=True, sharey=False)
    configs = [
        (METHOD_RSSI_MED, "pred_rssi_median"),
        (csi_method_label, "pred_csi_per_scenario"),
    ]
    y_true = result_df["distance_m"].to_numpy(dtype=float)
    min_d, max_d = float(np.min(y_true)), float(np.max(y_true))
    diag = np.linspace(min_d, max_d, 100)

    for axis, (title, pred_col) in zip(axes, configs):
        y_pred = result_df[pred_col].to_numpy(dtype=float)
        clip_high = max_d + 1.0
        y_plot = np.minimum(y_pred, clip_high)
        clipped_frac = float(np.mean(y_pred > clip_high) * 100.0)
        axis.scatter(y_true, y_plot, s=10, alpha=0.25)
        if clipped_frac > 0:
            clipped_mask = y_pred > clip_high
            axis.scatter(
                y_true[clipped_mask],
                y_plot[clipped_mask],
                s=12,
                marker="^",
                color="red",
                alpha=0.45,
            )
        axis.plot(diag, diag, color="black", linestyle="--", linewidth=1)
        axis.set_title(title)
        axis.set_xlabel("True distance (m)")
        axis.set_xlim(min_d - 0.2, max_d + 0.2)
        axis.set_ylim(0, max(clip_high * 1.05, max_d + 0.5))
        axis.text(
            0.02,
            0.98,
            f"display cap={clip_high:.2f}m\ncapped={clipped_frac:.2f}%",
            transform=axis.transAxes,
            ha="left",
            va="top",
            fontsize=8,
            bbox={"facecolor": "white", "alpha": 0.7, "edgecolor": "none"},
        )
        axis.grid(True, alpha=0.3)
    axes[0].set_ylabel("Estimated distance (m)")
    fig.suptitle("Predicted vs True Distance (display-capped for readability)", y=1.02)
    fig.tight_layout()
    fig.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_boxplot_error_by_scenario(result_df: pd.DataFrame, out_path: Path) -> None:
    """Boxplot of absolute error by scenario for RSSI and CSI."""
    scenarios = sorted(result_df["scenario"].unique())
    data: list[np.ndarray] = []
    labels: list[str] = []
    positions: list[int] = []
    position = 1
    for scenario in scenarios:
        subset = result_df[result_df["scenario"] == scenario]
        err_rssi = np.abs(subset["pred_rssi_median"] - subset["distance_m"]).to_numpy()
        err_csi = np.abs(subset["pred_csi_per_scenario"] - subset["distance_m"]).to_numpy()
        data.extend([err_rssi, err_csi])
        labels.extend([f"{scenario}\nRSSI", f"{scenario}\nCSI"])
        positions.extend([position, position + 1])
        position += 3

    plt.figure(figsize=(max(8, len(scenarios) * 2.5), 5))
    box = plt.boxplot(data, positions=positions, patch_artist=True, showfliers=False)
    colors = ["#1f77b4", "#ff7f0e"] * len(scenarios)
    for patch, color in zip(box["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.5)
    plt.xticks(positions, labels)
    plt.ylabel("|Error| (m)")
    plt.title("Absolute Error by Scenario")
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


def plot_timeseries_stability(frame: pd.DataFrame, out_path: Path) -> None:
    """Plot RSSI and mean amplitude over packet sequence for one fixed condition."""
    preferred = frame[
        (frame["scenario"] == "LoS")
        & (frame["distance_m"] == frame["distance_m"].min())
    ]
    subset = preferred if not preferred.empty else frame
    first_row = subset.iloc[0]
    cond = (
        (frame["scenario"] == first_row["scenario"])
        & (frame["distance_m"] == first_row["distance_m"])
        & (frame["run_id"] == first_row["run_id"])
    )
    series = frame[cond].sort_values(by=["packet_seq", "timestamp"]).head(800)
    if series.empty:
        raise ValueError("Could not select a non-empty time-series subset for stability plot.")

    x = series["packet_seq"].to_numpy(dtype=float)
    rssi_series = series["rssi_dbm"].to_numpy(dtype=float)
    mean_amp_series = series["mean_amp"].to_numpy(dtype=float)
    fig, axis_left = plt.subplots(figsize=(10, 5))
    axis_left.plot(x, rssi_series, color="#1f77b4", linewidth=1, label="RSSI (dBm)")
    axis_left.set_xlabel("Packet sequence")
    axis_left.set_ylabel("RSSI (dBm)", color="#1f77b4")
    axis_left.tick_params(axis="y", labelcolor="#1f77b4")
    axis_left.grid(True, alpha=0.3)

    axis_right = axis_left.twinx()
    axis_right.plot(
        x,
        mean_amp_series,
        color="#ff7f0e",
        linewidth=1,
        label="Mean CSI amplitude",
    )
    axis_right.set_ylabel("Mean |H|", color="#ff7f0e")
    axis_right.tick_params(axis="y", labelcolor="#ff7f0e")
    title = (
        f"Stability Example: scenario={first_row['scenario']}, "
        f"distance={first_row['distance_m']} m, run={first_row['run_id']}"
    )
    plt.title(title)
    fig.tight_layout()
    fig.savefig(out_path, dpi=300)
    plt.close(fig)


def format_markdown_table(frame: pd.DataFrame) -> str:
    """Render a simple markdown table without optional dependencies."""
    columns = list(frame.columns)
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    rows = []
    for _, row in frame.iterrows():
        values = []
        for col in columns:
            val = row[col]
            if isinstance(val, float):
                values.append(f"{val:.4f}")
            else:
                values.append(str(val))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, separator, *rows])


def format_latex_table(frame: pd.DataFrame) -> str:
    """Render a compact LaTeX tabular snippet."""
    columns = list(frame.columns)
    align = "l" + "r" * (len(columns) - 1)
    lines = [f"\\begin{{tabular}}{{{align}}}", "\\hline"]
    lines.append(" & ".join(columns) + r" \\")
    lines.append("\\hline")
    for _, row in frame.iterrows():
        values = []
        for col in columns:
            val = row[col]
            if isinstance(val, float):
                values.append(f"{val:.4f}")
            else:
                values.append(str(val))
        lines.append(" & ".join(values) + r" \\")
    lines.extend(["\\hline", "\\end{tabular}"])
    return "\n".join(lines)


def save_outputs(
    frame: pd.DataFrame,
    overall_df: pd.DataFrame,
    scenario_df: pd.DataFrame,
    result_df: pd.DataFrame,
    method_cols: dict[str, str],
    out_dir: Path,
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    rssi_model: RSSIModel,
    rssi_median_model: RSSIModel,
    csi_method_unified: str,
    csi_method_per_scenario: str,
    csi_estimator_desc: str,
) -> None:
    """Save all required tables, plots, and report."""
    figs_dir = out_dir / "figs"
    tables_dir = out_dir / "tables"
    figs_dir.mkdir(parents=True, exist_ok=True)
    tables_dir.mkdir(parents=True, exist_ok=True)

    overall_path = tables_dir / "table_metrics_overall.csv"
    scenario_path = tables_dir / "table_metrics_by_scenario.csv"
    overall_df.to_csv(overall_path, index=False)
    scenario_df.to_csv(scenario_path, index=False)

    markdown_overall = format_markdown_table(overall_df)
    latex_overall = format_latex_table(overall_df)
    (tables_dir / "table_metrics_overall_snippet.md").write_text(
        markdown_overall + "\n", encoding="utf-8"
    )
    (tables_dir / "table_metrics_overall_snippet.tex").write_text(
        latex_overall + "\n", encoding="utf-8"
    )

    plot_cdf_error(result_df, method_cols, figs_dir / "cdf_error.png")
    plot_scatter_pred_vs_true(
        result_df, figs_dir / "scatter_pred_vs_true.png", csi_method_per_scenario
    )
    plot_boxplot_error_by_scenario(result_df, figs_dir / "boxplot_error_by_scenario.png")
    plot_timeseries_stability(frame, figs_dir / "timeseries_stability.png")

    train_groups = frame.iloc[train_idx]["run_id"].astype(str).unique().tolist()
    test_groups = frame.iloc[test_idx]["run_id"].astype(str).unique().tolist()
    report_lines = [
        "# Distance Measurement Analysis Report",
        "",
        "## Dataset Summary",
        f"- Packets: {len(frame):,}",
        f"- Source files: {frame['source_file'].nunique()}",
        f"- Scenarios: {', '.join(sorted(frame['scenario'].unique()))}",
        f"- Distances (m): {', '.join(f'{d:.1f}' for d in sorted(frame['distance_m'].unique()))}",
        f"- Runs: {', '.join(sorted(frame['run_id'].astype(str).unique()))}",
        "",
        "## Evaluation Split",
        f"- Group column: `run_id` (CLI `--group_col`)",
        f"- Train groups: {', '.join(train_groups)}",
        f"- Test groups: {', '.join(test_groups)}",
        f"- Train packets: {len(train_idx):,}",
        f"- Test packets: {len(test_idx):,}",
        "",
        "## Estimators",
        f"- {METHOD_RSSI}: log-distance fit on train RSSI only. "
        f"RSSI0={rssi_model.rssi0:.4f} dBm, n={rssi_model.n:.4f}.",
        f"- {METHOD_RSSI_MED}: same model form with per-burst median RSSI. "
        f"RSSI0={rssi_median_model.rssi0:.4f} dBm, n={rssi_median_model.n:.4f}.",
        f"- {csi_method_unified}: {csi_estimator_desc}.",
        f"- {csi_method_per_scenario}: same CSI pipeline fitted per scenario.",
        "",
        "## Key Metrics (Overall)",
        markdown_overall,
        "",
        "## LaTeX Snippet (Overall)",
        "```latex",
        latex_overall,
        "```",
    ]
    (out_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    expected_files = [
        tables_dir / "table_metrics_overall.csv",
        tables_dir / "table_metrics_by_scenario.csv",
        figs_dir / "cdf_error.png",
        figs_dir / "scatter_pred_vs_true.png",
        figs_dir / "boxplot_error_by_scenario.png",
        figs_dir / "timeseries_stability.png",
        out_dir / "report.md",
    ]
    missing = [str(path) for path in expected_files if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing expected output files: {missing}")


def run_analysis(args: argparse.Namespace) -> None:
    """Execute full ingestion, modeling, evaluation, and export pipeline."""
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    files = discover_files(data_dir)
    LOGGER.info("Found %d candidate data files under %s", len(files), data_dir)

    frame, amp_matrix = build_packet_dataframe(
        files=files,
        downsample_step=args.downsample_step,
        topk_ratio=args.topk_ratio,
    )
    train_idx, test_idx = grouped_split(
        frame=frame,
        group_col=args.group_col,
        test_size=args.test_size,
        seed=args.seed,
    )

    pca_components, pca_ratio = compute_pca_components(amp_matrix, train_idx, n_components=3)
    frame = frame.copy()
    frame[["pca_1", "pca_2", "pca_3"]] = pca_components
    LOGGER.info(
        "PCA explained variance ratio (train fit): %s",
        ", ".join(f"{v:.4f}" for v in pca_ratio),
    )

    if SKLEARN_AVAILABLE:
        csi_method_unified = METHOD_CSI_UNIFIED
        csi_method_per_scenario = METHOD_CSI_PER_SCENARIO
        csi_estimator_desc = "standardized kNN on CSI-derived features"
    else:
        csi_method_unified = METHOD_CSI_UNIFIED_FALLBACK
        csi_method_per_scenario = METHOD_CSI_PER_SCENARIO_FALLBACK
        csi_estimator_desc = (
            "standardized Ridge regression fallback on CSI-derived features "
            "(sklearn unavailable)"
        )

    x_csi = build_csi_feature_matrix(frame, amp_matrix, use_pca_only=args.use_pca)
    y = frame["distance_m"].to_numpy(dtype=np.float32)

    # RSSI baseline models.
    rssi_model = fit_rssi_model(
        frame.iloc[train_idx]["distance_m"].to_numpy(dtype=float),
        frame.iloc[train_idx]["rssi_dbm"].to_numpy(dtype=float),
    )
    rssi_median_model = fit_rssi_model(
        frame.iloc[train_idx]["distance_m"].to_numpy(dtype=float),
        frame.iloc[train_idx]["rssi_dbm_median_burst"].to_numpy(dtype=float),
    )
    pred_rssi = predict_rssi_distance(
        rssi_model, frame.iloc[test_idx]["rssi_dbm"].to_numpy(dtype=float)
    )
    pred_rssi_median = predict_rssi_distance(
        rssi_median_model, frame.iloc[test_idx]["rssi_dbm_median_burst"].to_numpy(dtype=float)
    )

    # CSI unified model.
    pred_csi_unified = fit_predict_knn(
        x_csi[train_idx],
        y[train_idx],
        x_csi[test_idx],
        k=args.knn_k,
    )

    # CSI per-scenario models.
    pred_csi_per = np.full(shape=len(test_idx), fill_value=np.nan, dtype=np.float32)
    scenario_all = frame["scenario"].to_numpy()
    test_scenarios = scenario_all[test_idx]
    for scenario in sorted(np.unique(test_scenarios)):
        train_mask = scenario_all[train_idx] == scenario
        test_local_idx = np.where(test_scenarios == scenario)[0]
        scenario_train_idx = train_idx[train_mask]
        scenario_test_idx = test_idx[test_local_idx]
        if scenario_train_idx.size == 0:
            LOGGER.warning(
                "No training samples for scenario '%s'; using unified CSI predictions for those rows.",
                scenario,
            )
            pred_csi_per[test_local_idx] = pred_csi_unified[test_local_idx]
            continue
        pred_local = fit_predict_knn(
            x_csi[scenario_train_idx],
            y[scenario_train_idx],
            x_csi[scenario_test_idx],
            k=args.knn_k,
        )
        pred_csi_per[test_local_idx] = pred_local.astype(np.float32)

    # Final fallback if any NaN remained.
    nan_mask = np.isnan(pred_csi_per)
    if np.any(nan_mask):
        pred_csi_per[nan_mask] = pred_csi_unified[nan_mask]

    result_df = frame.iloc[test_idx][
        ["distance_m", "scenario", "run_id", "rssi_dbm", "mean_amp"]
    ].copy()
    result_df["pred_rssi"] = pred_rssi
    result_df["pred_rssi_median"] = pred_rssi_median
    result_df["pred_csi_unified"] = pred_csi_unified
    result_df["pred_csi_per_scenario"] = pred_csi_per

    method_cols = {
        METHOD_RSSI: "pred_rssi",
        METHOD_RSSI_MED: "pred_rssi_median",
        csi_method_unified: "pred_csi_unified",
        csi_method_per_scenario: "pred_csi_per_scenario",
    }
    overall_df, scenario_df = summarize_metrics(result_df, method_cols)

    save_outputs(
        frame=frame,
        overall_df=overall_df,
        scenario_df=scenario_df,
        result_df=result_df,
        method_cols=method_cols,
        out_dir=out_dir,
        train_idx=train_idx,
        test_idx=test_idx,
        rssi_model=rssi_model,
        rssi_median_model=rssi_median_model,
        csi_method_unified=csi_method_unified,
        csi_method_per_scenario=csi_method_per_scenario,
        csi_estimator_desc=csi_estimator_desc,
    )

    print("=== Dataset summary ===")
    print(
        f"rows={len(frame)}, scenarios={sorted(frame['scenario'].unique())}, "
        f"distances={sorted(frame['distance_m'].unique())}, runs={sorted(frame['run_id'].unique())}"
    )
    print(f"train_rows={len(train_idx)}, test_rows={len(test_idx)}")
    print("=== Overall metrics (test split) ===")
    print(
        overall_df.to_string(
            index=False,
            float_format=lambda value: f"{value:.4f}",
        )
    )
    print(f"Outputs written to: {out_dir}")


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not SKLEARN_AVAILABLE:
        LOGGER.warning(
            "scikit-learn is not installed; using numpy fallback for grouped split/PCA/CSI regression."
        )
    args = parse_args()
    _require_plotting_stack()
    if not (0.0 < args.test_size < 1.0):
        raise ValueError("--test_size must be in (0, 1).")
    if args.knn_k < 1:
        raise ValueError("--knn_k must be >= 1.")
    if args.downsample_step < 1:
        raise ValueError("--downsample_step must be >= 1.")
    if not (0.0 < args.topk_ratio <= 1.0):
        raise ValueError("--topk_ratio must be in (0, 1].")
    run_analysis(args)


if __name__ == "__main__":
    main()
