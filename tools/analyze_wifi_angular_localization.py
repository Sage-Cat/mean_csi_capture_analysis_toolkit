#!/usr/bin/env python3
"""Reproducible angular-localization analysis using RSSI and CSI."""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from collections import Counter
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
    extract_angle_from_text,
    infer_angle_from_path,
    infer_distance_from_path,
    infer_run_id_from_path,
    infer_scenario_from_path,
    iter_records,
    normalize_scenario,
    parse_angle_token,
    parse_csi_interleaved,
    parse_numeric_array,
    scenario_base_from_text,
    strip_angle_tag,
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

LOGGER = logging.getLogger("wifi_angular_localization")

METHOD_RSSI_LINEAR = "RSSI_linear"
METHOD_RSSI_POLY2 = "RSSI_poly2"
METHOD_CSI_KNN = "CSI_kNN"
METHOD_FUSION_KNN = "CSI_RSSI_kNN"
METHOD_CSI_RIDGE_FALLBACK = "CSI_ridge_fallback"
METHOD_FUSION_RIDGE_FALLBACK = "CSI_RSSI_ridge_fallback"


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
        default="out/angular_localization",
        help="Output directory for plots, tables, and report.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    parser.add_argument(
        "--use_pca",
        action="store_true",
        help="Use summary + PCA features only for CSI (otherwise include downsampled magnitudes).",
    )
    parser.add_argument("--knn_k", type=int, default=7, help="k for kNN regressors.")
    parser.add_argument(
        "--test_size",
        type=float,
        default=0.3,
        help="Fraction of groups for test split in GroupShuffleSplit.",
    )
    parser.add_argument(
        "--group_col",
        default="group_id",
        help="Grouping column for leakage-safe split (default: group_id=run|scenario|angle).",
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
    parser.add_argument(
        "--angle_bins",
        default="-60,-45,-30,-15,0,15,30,45,60",
        help="Comma-separated angle-bin centers in degrees for bin-level evaluation.",
    )
    return parser.parse_args()



def parse_float(value: Any, field_name: str) -> float:
    """Parse float with explicit error details."""
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid {field_name} value: {value!r}") from exc
    if not np.isfinite(parsed):
        raise ValueError(f"Non-finite {field_name} value: {value!r}")
    return parsed



def topk_mean(values: np.ndarray, ratio: float) -> float:
    """Mean of top-K values where K = ceil(ratio*N)."""
    k = max(1, int(math.ceil(ratio * values.size)))
    if k >= values.size:
        return float(np.mean(values))
    idx = np.argpartition(values, -k)[-k:]
    return float(np.mean(values[idx]))



def parse_angle_bins(text: str) -> np.ndarray:
    """Parse comma-separated angle bin centers."""
    parts = [p.strip() for p in text.split(",") if p.strip()]
    if not parts:
        raise ValueError("--angle_bins must include at least one value.")
    values: list[float] = []
    for part in parts:
        values.append(parse_float(part, "angle_bins"))
    unique_sorted = sorted(set(values))
    return np.asarray(unique_sorted, dtype=np.float32)



def format_angle_label(value: float) -> str:
    """Format compact angle-bin label."""
    if abs(value - round(value)) < 1e-6:
        return f"{int(round(value))}"
    return f"{value:.1f}"



def assign_angle_bins(angles_deg: np.ndarray, bin_centers_deg: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Assign each angle to the nearest configured angle-bin center."""
    if angles_deg.size == 0:
        return np.array([], dtype=np.float32), np.array([], dtype=object)
    if bin_centers_deg.size == 0:
        raise ValueError("bin_centers_deg must contain at least one value.")

    diffs = np.abs(angles_deg[:, None] - bin_centers_deg[None, :])
    idx = np.argmin(diffs, axis=1)
    assigned = bin_centers_deg[idx].astype(np.float32)
    labels = np.asarray([format_angle_label(v) for v in assigned], dtype=object)
    return assigned, labels



def build_packet_dataframe(
    files: Iterable[Path],
    downsample_step: int,
    topk_ratio: float,
) -> tuple[pd.DataFrame, np.ndarray]:
    """Create one-row-per-packet DataFrame and aligned downsampled amplitude matrix."""
    rows: list[dict[str, Any]] = []
    vectors: list[np.ndarray] = []
    total_records = 0
    parse_failures = 0
    run_id_mismatch_count = 0

    for path in files:
        for rec in iter_records(path):
            total_records += 1
            if not isinstance(rec, dict):
                parse_failures += 1
                continue
            try:
                scenario_raw = rec.get("scenario", None)
                scenario_base = scenario_base_from_text(scenario_raw)
                if scenario_base == "unknown":
                    inferred = infer_scenario_from_path(path)
                    if inferred is not None:
                        scenario_base = inferred

                record_run_id = rec.get("run_id")
                path_run_id = infer_run_id_from_path(path)
                if path_run_id is not None:
                    run_id = path_run_id
                    if record_run_id is not None and str(record_run_id) != str(path_run_id):
                        run_id_mismatch_count += 1
                else:
                    run_id = record_run_id
                if run_id is None:
                    raise ValueError(
                        "run_id missing in record and not inferable from path (expected run_<id>)."
                    )

                angle_candidate = rec.get("angle_deg")
                if angle_candidate is None:
                    angle_candidate = extract_angle_from_text(scenario_raw)
                if angle_candidate is None:
                    angle_candidate = infer_angle_from_path(path)
                angle_deg = parse_float(angle_candidate, "angle_deg")

                rssi_dbm = parse_float(rec.get("rssi_dbm", rec.get("rssi")), "rssi_dbm/rssi")

                distance_candidate = rec.get("distance_m", infer_distance_from_path(path))
                if distance_candidate is None:
                    distance_m = np.nan
                else:
                    try:
                        distance_m = float(distance_candidate)
                    except (TypeError, ValueError):
                        distance_m = np.nan

                interleaved = parse_csi_interleaved(rec)
                if interleaved.size < 2:
                    raise ValueError("CSI payload has fewer than 2 values.")
                if interleaved.size % 2 != 0:
                    interleaved = interleaved[:-1]

                i_vals = interleaved[0::2]
                q_vals = interleaved[1::2]
                amp = np.sqrt(i_vals * i_vals + q_vals * q_vals, dtype=np.float32)
                if amp.size == 0:
                    raise ValueError("Empty amplitude vector after CSI parsing.")
                amp_ds = amp[::downsample_step]

                timestamp_val = rec.get("timestamp")
                try:
                    timestamp_num = float(timestamp_val) if timestamp_val is not None else np.nan
                except (TypeError, ValueError):
                    timestamp_num = np.nan

                row = {
                    "timestamp": timestamp_num,
                    "distance_m": float(distance_m) if np.isfinite(distance_m) else np.nan,
                    "angle_deg": angle_deg,
                    "scenario_base": scenario_base,
                    "scenario_raw": str(scenario_raw) if scenario_raw is not None else "",
                    "run_id": str(run_id),
                    "record_run_id": str(record_run_id) if record_run_id is not None else "",
                    "path_run_id": str(path_run_id) if path_run_id is not None else "",
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
        raise ValueError(
            "No valid packet records were parsed. Ensure logs include angle metadata "
            "(angle_deg field or scenario tags like *_ang_m30)."
        )

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
    filtered_rows = [rows[idx] for idx in keep_idx]
    filtered_vectors = [vectors[idx] for idx in keep_idx]
    frame = pd.DataFrame(filtered_rows)
    amp_matrix = np.vstack(filtered_vectors).astype(np.float32)

    frame["seq"] = pd.to_numeric(frame["seq"], errors="coerce")
    group_keys = ["scenario_base", "run_id", "angle_deg"]
    frame = frame.sort_values(by=group_keys + ["timestamp", "source_file"]).reset_index(drop=True)

    if frame["seq"].isna().all():
        frame["packet_seq"] = frame.groupby(group_keys).cumcount()
    else:
        fallback_seq = frame.groupby(group_keys).cumcount()
        frame["packet_seq"] = frame["seq"].fillna(fallback_seq)

    frame["rssi_dbm_median_burst"] = frame.groupby(group_keys, sort=False)["rssi_dbm"].transform(
        "median"
    )

    frame["group_id"] = (
        frame["run_id"].astype(str)
        + "|"
        + frame["scenario_base"].astype(str)
        + "|"
        + frame["angle_deg"].map(lambda value: f"{value:.3f}")
    )

    if run_id_mismatch_count:
        LOGGER.warning(
            "Resolved %d rows with conflicting run_id metadata by preferring the path-derived run_<id>.",
            run_id_mismatch_count,
        )

    LOGGER.info(
        "Parsed %d records (%d valid, %d skipped).",
        total_records,
        len(frame),
        parse_failures,
    )
    LOGGER.info("Downsampled CSI feature length: %d", amp_matrix.shape[1])
    return frame, amp_matrix



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
            "Adjust --test_size or capture more groups."
        )
    return train_idx, test_idx



def compute_pca_components(
    amp_matrix: np.ndarray, train_idx: np.ndarray, n_components: int = 3
) -> tuple[np.ndarray, np.ndarray]:
    """Fit PCA on train-only magnitude vectors and transform full matrix."""
    x_train = amp_matrix[train_idx].astype(np.float64, copy=False)
    if x_train.shape[0] < 1:
        raise ValueError("Insufficient training data to compute PCA.")

    if SKLEARN_AVAILABLE:
        scaler = StandardScaler()
        x_train_scaled = scaler.fit_transform(x_train)
        components = min(n_components, x_train_scaled.shape[1], x_train_scaled.shape[0])
        if components < 1:
            raise ValueError("Insufficient training data to compute PCA components.")
        pca = PCA(n_components=components)
        pca.fit(x_train_scaled)
        x_scaled = scaler.transform(amp_matrix.astype(np.float64, copy=False))
        transformed = pca.transform(x_scaled)
        explained_ratio = np.asarray(pca.explained_variance_ratio_, dtype=np.float64)
    else:
        mean = x_train.mean(axis=0)
        std = x_train.std(axis=0)
        std[std == 0.0] = 1.0
        x_train_scaled = (x_train - mean) / std
        x_scaled = (amp_matrix.astype(np.float64, copy=False) - mean) / std
        components = min(n_components, x_train_scaled.shape[1], x_train_scaled.shape[0])
        if components < 1:
            raise ValueError("Insufficient training data to compute PCA components.")
        _, singular_values, vt = np.linalg.svd(x_train_scaled, full_matrices=False)
        basis = vt[:components].T
        transformed = x_scaled @ basis
        if x_train_scaled.shape[0] > 1:
            variances = (singular_values * singular_values) / (x_train_scaled.shape[0] - 1)
        else:
            variances = singular_values * singular_values
        total_var = float(np.sum(variances))
        if total_var > 0.0:
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



def build_csi_feature_matrix(frame: pd.DataFrame, amp_matrix: np.ndarray, use_pca_only: bool) -> np.ndarray:
    """Create CSI model feature matrix from summary and optional magnitude vectors."""
    summary = frame[
        ["mean_amp", "std_amp", "median_amp", "topK_amp_mean", "csi_pairs", "pca_1", "pca_2", "pca_3"]
    ].to_numpy(dtype=np.float32)
    if use_pca_only:
        return summary
    return np.hstack([summary, amp_matrix])



def build_fusion_feature_matrix(frame: pd.DataFrame, csi_matrix: np.ndarray) -> np.ndarray:
    """Build fused CSI+RSSI feature matrix."""
    rssi = frame[["rssi_dbm", "rssi_dbm_median_burst"]].to_numpy(dtype=np.float32)
    return np.hstack([csi_matrix, rssi])



def fit_predict_knn(
    x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray, k: int
) -> np.ndarray:
    """Fit standardized kNN regressor or fallback ridge when sklearn is unavailable."""
    if x_train.shape[0] == 0:
        raise ValueError("Cannot fit model with zero training samples.")
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
    return pipeline.predict(x_test).astype(np.float32)



def fit_predict_ridge(
    x_train: np.ndarray, y_train: np.ndarray, x_test: np.ndarray, alpha: float = 1.0
) -> np.ndarray:
    """Closed-form ridge with standardization and intercept handling."""
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



def wrap_angle_deg(values: np.ndarray) -> np.ndarray:
    """Wrap angle to [-180, 180) degrees."""
    return ((values + 180.0) % 360.0) - 180.0



def metrics_from_predictions(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """Compute angular metrics from wrapped prediction errors."""
    error = wrap_angle_deg(y_pred - y_true)
    abs_error = np.abs(error)
    return {
        "MAE_deg": float(np.mean(abs_error)),
        "RMSE_deg": float(np.sqrt(np.mean(error * error))),
        "MedAE_deg": float(np.median(abs_error)),
        "P_abs_err_le_5deg": float(np.mean(abs_error <= 5.0)),
        "P_abs_err_le_10deg": float(np.mean(abs_error <= 10.0)),
        "Bias_deg": float(np.mean(error)),
        "N": int(y_true.size),
    }



def summarize_metrics(
    result_df: pd.DataFrame, method_cols: dict[str, str]
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Create overall, scenario-level, and angle-bin-level metrics tables."""
    overall_rows: list[dict[str, Any]] = []
    scenario_rows: list[dict[str, Any]] = []
    bin_rows: list[dict[str, Any]] = []

    y_true_all = result_df["angle_deg"].to_numpy(dtype=float)
    for method, pred_col in method_cols.items():
        y_pred_all = result_df[pred_col].to_numpy(dtype=float)
        row = {"method": method}
        row.update(metrics_from_predictions(y_true_all, y_pred_all))
        overall_rows.append(row)

        for scenario, group in result_df.groupby("scenario_base", sort=True):
            y_true = group["angle_deg"].to_numpy(dtype=float)
            y_pred = group[pred_col].to_numpy(dtype=float)
            srow = {"method": method, "scenario_base": scenario}
            srow.update(metrics_from_predictions(y_true, y_pred))
            scenario_rows.append(srow)

        grouped_bins = result_df.groupby("angle_bin_deg", sort=True)
        for angle_bin_deg, group in grouped_bins:
            y_true = group["angle_deg"].to_numpy(dtype=float)
            y_pred = group[pred_col].to_numpy(dtype=float)
            brow = {
                "method": method,
                "angle_bin_deg": float(angle_bin_deg),
                "angle_bin_label": group["angle_bin_label"].iloc[0],
            }
            brow.update(metrics_from_predictions(y_true, y_pred))
            bin_rows.append(brow)

    overall_df = pd.DataFrame(overall_rows).sort_values(by=["MAE_deg", "RMSE_deg"]).reset_index(
        drop=True
    )
    scenario_df = (
        pd.DataFrame(scenario_rows)
        .sort_values(by=["scenario_base", "MAE_deg", "RMSE_deg"])
        .reset_index(drop=True)
    )
    bin_df = (
        pd.DataFrame(bin_rows)
        .sort_values(by=["angle_bin_deg", "MAE_deg", "RMSE_deg"])
        .reset_index(drop=True)
    )
    return overall_df, scenario_df, bin_df



def plot_cdf_abs_angle_error(result_df: pd.DataFrame, method_cols: dict[str, str], out_path: Path) -> None:
    """Plot empirical CDF of absolute angle errors."""
    plt.figure(figsize=(8, 5))
    for method, pred_col in method_cols.items():
        err = wrap_angle_deg(
            result_df[pred_col].to_numpy(dtype=float) - result_df["angle_deg"].to_numpy(dtype=float)
        )
        abs_err = np.sort(np.abs(err))
        cdf = np.arange(1, abs_err.size + 1) / abs_err.size
        plt.plot(abs_err, cdf, linewidth=2, label=method)

    plt.xlabel("|Angle error| (deg)")
    plt.ylabel("CDF")
    plt.title("CDF of Absolute Angle Error")
    plt.xlim(left=0.0)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()



def plot_scatter_pred_vs_true_angle(
    result_df: pd.DataFrame, method_cols: dict[str, str], out_path: Path
) -> None:
    """Scatter true vs predicted angle for each method."""
    n_methods = len(method_cols)
    n_cols = 2
    n_rows = int(math.ceil(n_methods / n_cols))

    fig, axes = plt.subplots(n_rows, n_cols, figsize=(12, 5 * n_rows), sharex=True, sharey=True)
    axes_arr = np.asarray(axes).reshape(-1)

    y_true = result_df["angle_deg"].to_numpy(dtype=float)
    min_a = float(np.min(y_true))
    max_a = float(np.max(y_true))
    pad = max(5.0, 0.1 * (max_a - min_a + 1.0))
    grid = np.linspace(min_a - pad, max_a + pad, 200)

    for axis, (method, pred_col) in zip(axes_arr, method_cols.items()):
        y_pred_raw = result_df[pred_col].to_numpy(dtype=float)
        y_pred = wrap_angle_deg(y_pred_raw)
        error = wrap_angle_deg(y_pred_raw - y_true)
        mae = float(np.mean(np.abs(error)))

        axis.scatter(y_true, y_pred, s=10, alpha=0.25)
        axis.plot(grid, grid, color="black", linestyle="--", linewidth=1)
        axis.set_title(method)
        axis.set_xlabel("True angle (deg)")
        axis.set_ylabel("Predicted angle (deg)")
        axis.grid(True, alpha=0.3)
        axis.text(
            0.02,
            0.98,
            f"MAE={mae:.2f} deg",
            transform=axis.transAxes,
            ha="left",
            va="top",
            fontsize=9,
            bbox={"facecolor": "white", "alpha": 0.7, "edgecolor": "none"},
        )

    for axis in axes_arr[n_methods:]:
        axis.axis("off")

    plt.xlim(min_a - pad, max_a + pad)
    plt.ylim(min_a - pad, max_a + pad)
    fig.suptitle("Predicted vs True Angle")
    fig.tight_layout()
    fig.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close(fig)



def method_short_label(method_name: str) -> str:
    """Short legend/x-label variant for compact plots."""
    mapping = {
        METHOD_RSSI_LINEAR: "RSSI-lin",
        METHOD_RSSI_POLY2: "RSSI-poly2",
        METHOD_CSI_KNN: "CSI-kNN",
        METHOD_FUSION_KNN: "Fusion-kNN",
        METHOD_CSI_RIDGE_FALLBACK: "CSI-ridge",
        METHOD_FUSION_RIDGE_FALLBACK: "Fusion-ridge",
    }
    return mapping.get(method_name, method_name)



def plot_boxplot_angle_error_by_scenario(
    result_df: pd.DataFrame, method_cols: dict[str, str], out_path: Path
) -> None:
    """Boxplot of absolute angle error per scenario and method."""
    scenarios = sorted(result_df["scenario_base"].astype(str).unique())
    methods = list(method_cols.keys())

    data: list[np.ndarray] = []
    labels: list[str] = []
    positions: list[int] = []
    position = 1

    for scenario in scenarios:
        subset = result_df[result_df["scenario_base"] == scenario]
        for method in methods:
            pred_col = method_cols[method]
            err = wrap_angle_deg(
                subset[pred_col].to_numpy(dtype=float) - subset["angle_deg"].to_numpy(dtype=float)
            )
            data.append(np.abs(err))
            labels.append(f"{scenario}\n{method_short_label(method)}")
            positions.append(position)
            position += 1
        position += 1

    fig_width = max(10, 0.65 * len(positions))
    plt.figure(figsize=(fig_width, 5))
    box = plt.boxplot(data, positions=positions, patch_artist=True, showfliers=False)

    palette = plt.get_cmap("tab10")
    for idx, patch in enumerate(box["boxes"]):
        patch.set_facecolor(palette(idx % len(method_cols)))
        patch.set_alpha(0.55)

    plt.xticks(positions, labels, rotation=0)
    plt.ylabel("|Angle error| (deg)")
    plt.title("Absolute Angle Error by Scenario")
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()



def plot_boxplot_angle_error_by_bin(
    result_df: pd.DataFrame, method_cols: dict[str, str], out_path: Path
) -> None:
    """Boxplot of absolute angle error per angle bin and method."""
    bin_values = sorted(result_df["angle_bin_deg"].astype(float).unique())
    methods = list(method_cols.keys())

    data: list[np.ndarray] = []
    labels: list[str] = []
    positions: list[int] = []
    position = 1

    for angle_bin_deg in bin_values:
        subset = result_df[result_df["angle_bin_deg"] == angle_bin_deg]
        bin_label = format_angle_label(float(angle_bin_deg))
        for method in methods:
            pred_col = method_cols[method]
            err = wrap_angle_deg(
                subset[pred_col].to_numpy(dtype=float) - subset["angle_deg"].to_numpy(dtype=float)
            )
            data.append(np.abs(err))
            labels.append(f"{bin_label}deg\n{method_short_label(method)}")
            positions.append(position)
            position += 1
        position += 1

    fig_width = max(10, 0.65 * len(positions))
    plt.figure(figsize=(fig_width, 5))
    box = plt.boxplot(data, positions=positions, patch_artist=True, showfliers=False)

    palette = plt.get_cmap("tab10")
    for idx, patch in enumerate(box["boxes"]):
        patch.set_facecolor(palette(idx % len(method_cols)))
        patch.set_alpha(0.55)

    plt.xticks(positions, labels, rotation=0)
    plt.ylabel("|Angle error| (deg)")
    plt.title("Absolute Angle Error by Angle Bin")
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()



def plot_polar_mean_error(result_df: pd.DataFrame, method_cols: dict[str, str], out_path: Path) -> None:
    """Polar plot of mean signed error by angle bin (per method)."""
    bin_values = sorted(result_df["angle_bin_deg"].astype(float).unique())
    theta = np.deg2rad(np.asarray(bin_values, dtype=float))

    mean_errors_by_method: dict[str, np.ndarray] = {}
    max_abs = 0.0
    for method, pred_col in method_cols.items():
        means: list[float] = []
        for angle_bin in bin_values:
            subset = result_df[result_df["angle_bin_deg"] == angle_bin]
            err = wrap_angle_deg(
                subset[pred_col].to_numpy(dtype=float) - subset["angle_deg"].to_numpy(dtype=float)
            )
            m = float(np.mean(err)) if err.size > 0 else 0.0
            means.append(m)
            max_abs = max(max_abs, abs(m))
        mean_errors_by_method[method] = np.asarray(means, dtype=float)

    max_abs = max(max_abs, 1.0)
    offset = max_abs + 1.0

    fig = plt.figure(figsize=(7, 7))
    axis = fig.add_subplot(111, projection="polar")
    axis.set_theta_zero_location("E")
    axis.set_theta_direction(-1)

    for method, means in mean_errors_by_method.items():
        theta_closed = np.append(theta, theta[0])
        radius_closed = np.append(means + offset, means[0] + offset)
        axis.plot(theta_closed, radius_closed, linewidth=2, marker="o", label=method_short_label(method))

    tick_values = np.array([-max_abs, 0.0, max_abs], dtype=float)
    axis.set_yticks(tick_values + offset)
    axis.set_yticklabels([f"{tick:.1f}" for tick in tick_values])
    axis.set_title("Polar Mean Signed Angle Error (deg)\n(radial tick labels are signed error)")
    axis.grid(True, alpha=0.35)
    axis.legend(loc="upper right", bbox_to_anchor=(1.25, 1.12))
    fig.tight_layout()
    fig.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close(fig)



def format_markdown_table(frame: pd.DataFrame) -> str:
    """Render DataFrame as markdown table without optional dependencies."""
    columns = list(frame.columns)
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"

    rows: list[str] = []
    for _, row in frame.iterrows():
        values: list[str] = []
        for col in columns:
            val = row[col]
            if isinstance(val, float):
                values.append(f"{val:.4f}")
            else:
                values.append(str(val))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, separator, *rows])



def format_latex_table(frame: pd.DataFrame) -> str:
    """Render compact LaTeX table."""
    columns = list(frame.columns)
    align = "l" + "r" * (len(columns) - 1)
    lines = [f"\\begin{{tabular}}{{{align}}}", "\\hline"]
    lines.append(" & ".join(columns) + r" \\")
    lines.append("\\hline")
    for _, row in frame.iterrows():
        values: list[str] = []
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
    result_df: pd.DataFrame,
    overall_df: pd.DataFrame,
    scenario_df: pd.DataFrame,
    bin_df: pd.DataFrame,
    method_cols: dict[str, str],
    out_dir: Path,
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    group_col: str,
    pca_ratio: np.ndarray,
    csi_estimator_desc: str,
    plots_available: bool,
) -> None:
    """Persist all required tables, plots, and markdown report."""
    figs_dir = out_dir / "figs"
    tables_dir = out_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)
    if plots_available:
        figs_dir.mkdir(parents=True, exist_ok=True)

    overall_path = tables_dir / "table_metrics_overall.csv"
    scenario_path = tables_dir / "table_metrics_by_scenario.csv"
    bin_path = tables_dir / "table_metrics_by_angle_bin.csv"

    overall_df.to_csv(overall_path, index=False)
    scenario_df.to_csv(scenario_path, index=False)
    bin_df.to_csv(bin_path, index=False)

    overall_md = format_markdown_table(overall_df)
    overall_tex = format_latex_table(overall_df)
    (tables_dir / "table_metrics_overall_snippet.md").write_text(overall_md + "\n", encoding="utf-8")
    (tables_dir / "table_metrics_overall_snippet.tex").write_text(overall_tex + "\n", encoding="utf-8")

    generated_artifacts = [
        "- tables/table_metrics_overall.csv",
        "- tables/table_metrics_by_scenario.csv",
        "- tables/table_metrics_by_angle_bin.csv",
    ]
    expected_files = [
        tables_dir / "table_metrics_overall.csv",
        tables_dir / "table_metrics_by_scenario.csv",
        tables_dir / "table_metrics_by_angle_bin.csv",
    ]
    if plots_available:
        plot_cdf_abs_angle_error(result_df, method_cols, figs_dir / "cdf_abs_angle_error.png")
        plot_scatter_pred_vs_true_angle(result_df, method_cols, figs_dir / "scatter_pred_vs_true_angle.png")
        plot_boxplot_angle_error_by_scenario(
            result_df,
            method_cols,
            figs_dir / "boxplot_angle_error_by_scenario.png",
        )
        plot_boxplot_angle_error_by_bin(
            result_df,
            method_cols,
            figs_dir / "boxplot_angle_error_by_bin.png",
        )
        plot_polar_mean_error(result_df, method_cols, figs_dir / "polar_mean_error.png")
        generated_artifacts.extend(
            [
                "- figs/cdf_abs_angle_error.png",
                "- figs/scatter_pred_vs_true_angle.png",
                "- figs/boxplot_angle_error_by_scenario.png",
                "- figs/boxplot_angle_error_by_bin.png",
                "- figs/polar_mean_error.png",
            ]
        )
        expected_files.extend(
            [
                figs_dir / "cdf_abs_angle_error.png",
                figs_dir / "scatter_pred_vs_true_angle.png",
                figs_dir / "boxplot_angle_error_by_scenario.png",
                figs_dir / "boxplot_angle_error_by_bin.png",
                figs_dir / "polar_mean_error.png",
            ]
        )

    train_groups = sorted(frame.iloc[train_idx][group_col].astype(str).unique().tolist())
    test_groups = sorted(frame.iloc[test_idx][group_col].astype(str).unique().tolist())
    run_id_conflict_count = 0
    if {"record_run_id", "path_run_id"}.issubset(frame.columns):
        mismatch_mask = (
            frame["record_run_id"].astype(str).ne("")
            & frame["path_run_id"].astype(str).ne("")
            & frame["record_run_id"].astype(str).ne(frame["path_run_id"].astype(str))
        )
        run_id_conflict_count = int(mismatch_mask.sum())

    best_row = overall_df.iloc[0]
    report_lines = [
        "# Angular Localization Analysis Report",
        "",
        "## Dataset Summary",
        f"- Packets: {len(frame):,}",
        f"- Source files: {frame['source_file'].nunique()}",
        f"- Scenarios: {', '.join(sorted(frame['scenario_base'].astype(str).unique()))}",
        f"- Angle range (deg): {frame['angle_deg'].min():.2f} to {frame['angle_deg'].max():.2f}",
        f"- Unique angle points: {frame['angle_deg'].nunique()}",
        f"- Runs: {', '.join(sorted(frame['run_id'].astype(str).unique()))}",
        "",
    ]
    if run_id_conflict_count:
        report_lines.extend(
            [
                "## Data Quality Note",
                (
                    f"- `{run_id_conflict_count}` packet rows had conflicting in-record `run_id` metadata. "
                    "The analysis used the path-derived `run_<id>` as authoritative."
                ),
                "",
            ]
        )
    if not plots_available:
        report_lines.extend(
            [
                "## Plot Generation Note",
                "- Figure generation was skipped because matplotlib is unavailable in the local Python stack.",
                "",
            ]
        )

    report_lines.extend(
        [
        "## Leakage-Safe Split",
        f"- Group column: `{group_col}`",
        f"- Train groups ({len(train_groups)}): {', '.join(train_groups)}",
        f"- Test groups ({len(test_groups)}): {', '.join(test_groups)}",
        f"- Train packets: {len(train_idx):,}",
        f"- Test packets: {len(test_idx):,}",
        "",
        "## Models",
        f"- {METHOD_RSSI_LINEAR}: ridge/OLS on RSSI-only linear feature.",
        f"- {METHOD_RSSI_POLY2}: ridge regression on RSSI polynomial features (2nd order).",
        f"- {csi_estimator_desc}",
        "",
        "## Feature Pipeline",
        f"- CSI summary features: mean/std/median/topK amplitude, CSI pair count.",
        f"- PCA (train fit only): explained ratio = {', '.join(f'{v:.4f}' for v in pca_ratio)}",
        "",
        "## Best Overall Method",
        (
            f"- `{best_row['method']}` with "
            f"MAE={best_row['MAE_deg']:.3f} deg, RMSE={best_row['RMSE_deg']:.3f} deg, "
            f"P(|error|<=10)={best_row['P_abs_err_le_10deg']:.3f}."
        ),
        "",
        "## Overall Metrics",
        overall_md,
        "",
        "## LaTeX Snippet (Overall)",
        "```latex",
        overall_tex,
        "```",
        "",
        "## Generated Artifacts",
        *generated_artifacts,
    ]
    )
    (out_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")

    expected_files.append(out_dir / "report.md")
    missing = [str(path) for path in expected_files if not path.exists()]
    if missing:
        raise FileNotFoundError(f"Missing expected output files: {missing}")



def run_analysis(args: argparse.Namespace) -> None:
    """Run full angular-localization pipeline."""
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    angle_bins = parse_angle_bins(args.angle_bins)

    files = discover_files(data_dir)
    LOGGER.info("Found %d candidate data files under %s", len(files), data_dir)

    frame, amp_matrix = build_packet_dataframe(
        files=files,
        downsample_step=args.downsample_step,
        topk_ratio=args.topk_ratio,
    )

    assigned_bins, assigned_labels = assign_angle_bins(
        frame["angle_deg"].to_numpy(dtype=float),
        angle_bins,
    )
    frame = frame.copy()
    frame["angle_bin_deg"] = assigned_bins
    frame["angle_bin_label"] = assigned_labels

    train_idx, test_idx = grouped_split(
        frame=frame,
        group_col=args.group_col,
        test_size=args.test_size,
        seed=args.seed,
    )
    train_scenarios = set(frame.iloc[train_idx]["scenario_base"].astype(str).unique().tolist())
    test_scenarios = set(frame.iloc[test_idx]["scenario_base"].astype(str).unique().tolist())
    unseen_test_scenarios = sorted(test_scenarios - train_scenarios)
    if unseen_test_scenarios:
        LOGGER.warning(
            "Test split contains scenarios unseen in train: %s",
            ", ".join(unseen_test_scenarios),
        )

    train_angle_bins = set(frame.iloc[train_idx]["angle_bin_label"].astype(str).unique().tolist())
    test_angle_bins = set(frame.iloc[test_idx]["angle_bin_label"].astype(str).unique().tolist())
    unseen_test_bins = sorted(test_angle_bins - train_angle_bins)
    if unseen_test_bins:
        LOGGER.warning(
            "Test split contains angle bins unseen in train: %s",
            ", ".join(unseen_test_bins),
        )

    pca_components, pca_ratio = compute_pca_components(amp_matrix, train_idx, n_components=3)
    frame[["pca_1", "pca_2", "pca_3"]] = pca_components
    LOGGER.info(
        "PCA explained variance ratio (train fit): %s",
        ", ".join(f"{value:.4f}" for value in pca_ratio),
    )

    if SKLEARN_AVAILABLE:
        method_csi = METHOD_CSI_KNN
        method_fusion = METHOD_FUSION_KNN
        csi_estimator_desc = "kNN regression on CSI-only and CSI+RSSI fused features (standardized)."
    else:
        method_csi = METHOD_CSI_RIDGE_FALLBACK
        method_fusion = METHOD_FUSION_RIDGE_FALLBACK
        csi_estimator_desc = (
            "ridge fallback on CSI-only and CSI+RSSI fused features "
            "(scikit-learn unavailable)."
        )

    y = frame["angle_deg"].to_numpy(dtype=np.float32)
    rssi = frame["rssi_dbm"].to_numpy(dtype=np.float32)
    rssi_med = frame["rssi_dbm_median_burst"].to_numpy(dtype=np.float32)

    x_rssi_linear = rssi.reshape(-1, 1)
    x_rssi_poly2 = np.column_stack([rssi, rssi * rssi, rssi_med, rssi_med * rssi_med]).astype(
        np.float32
    )

    x_csi = build_csi_feature_matrix(frame, amp_matrix, use_pca_only=args.use_pca)
    x_fusion = build_fusion_feature_matrix(frame, x_csi)

    pred_rssi_linear = fit_predict_ridge(
        x_rssi_linear[train_idx], y[train_idx], x_rssi_linear[test_idx], alpha=0.0
    )
    pred_rssi_poly2 = fit_predict_ridge(
        x_rssi_poly2[train_idx], y[train_idx], x_rssi_poly2[test_idx], alpha=1e-2
    )
    pred_csi = fit_predict_knn(
        x_csi[train_idx],
        y[train_idx],
        x_csi[test_idx],
        k=args.knn_k,
    )
    pred_fusion = fit_predict_knn(
        x_fusion[train_idx],
        y[train_idx],
        x_fusion[test_idx],
        k=args.knn_k,
    )

    result_df = frame.iloc[test_idx][
        [
            "angle_deg",
            "angle_bin_deg",
            "angle_bin_label",
            "scenario_base",
            "run_id",
            "group_id",
            "rssi_dbm",
            "mean_amp",
        ]
    ].copy()
    result_df["pred_rssi_linear"] = pred_rssi_linear
    result_df["pred_rssi_poly2"] = pred_rssi_poly2
    result_df["pred_csi"] = pred_csi
    result_df["pred_fusion"] = pred_fusion

    method_cols = {
        METHOD_RSSI_LINEAR: "pred_rssi_linear",
        METHOD_RSSI_POLY2: "pred_rssi_poly2",
        method_csi: "pred_csi",
        method_fusion: "pred_fusion",
    }

    overall_df, scenario_df, bin_df = summarize_metrics(result_df, method_cols)

    save_outputs(
        frame=frame,
        result_df=result_df,
        overall_df=overall_df,
        scenario_df=scenario_df,
        bin_df=bin_df,
        method_cols=method_cols,
        out_dir=out_dir,
        train_idx=train_idx,
        test_idx=test_idx,
        group_col=args.group_col,
        pca_ratio=pca_ratio,
        csi_estimator_desc=csi_estimator_desc,
        plots_available=MATPLOTLIB_AVAILABLE,
    )

    print("=== Dataset summary ===")
    print(
        f"rows={len(frame)}, scenarios={sorted(frame['scenario_base'].unique())}, "
        f"angles={sorted(frame['angle_deg'].unique())}, runs={sorted(frame['run_id'].unique())}"
    )
    print(f"train_rows={len(train_idx)}, test_rows={len(test_idx)}")
    print("=== Overall metrics (test split) ===")
    print(overall_df.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print(f"Outputs written to: {out_dir}")



def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if not SKLEARN_AVAILABLE:
        LOGGER.warning(
            "scikit-learn is not installed; using numpy fallback for split/PCA/CSI regression."
        )

    args = parse_args()
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
