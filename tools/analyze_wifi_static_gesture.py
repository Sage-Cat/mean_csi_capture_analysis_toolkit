#!/usr/bin/env python3
"""Deep static-gesture analysis for the static_sign_v1 ESP32 CSI dataset."""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    MATPLOTLIB_AVAILABLE = True
    MATPLOTLIB_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - environment dependent
    matplotlib = None
    plt = None
    MATPLOTLIB_AVAILABLE = False
    MATPLOTLIB_IMPORT_ERROR = f"{type(exc).__name__}: {exc}"
import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
    roc_auc_score,
    roc_curve,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import LinearSVC

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from csi_capture.core.dataset import load_static_sign_runs
from csi_capture.core.features import iq_to_amplitude, parse_csi_array

LOGGER = logging.getLogger("wifi_static_gesture_analysis")
LABELS = ("baseline", "hands_up")
POS_LABEL = "hands_up"
EPS = 1e-8
PARTICIPANT_NAME_ALIASES = {
    "макс": "Дядюк",
}

RSSI_FEATURES = ["mean_rssi", "std_rssi", "median_rssi", "range_rssi"]
CSI_FEATURES = ["mean_amp", "std_amp", "median_amp", "rms_amp", "iqr_amp", "entropy_amp"]
FUSION_FEATURES = RSSI_FEATURES + CSI_FEATURES


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data_dir", required=True, help="Dataset root containing baseline/hands_up runs.")
    parser.add_argument(
        "--out_dir",
        default="out/static_gesture",
        help="Output directory for figures, tables, and report.",
    )
    parser.add_argument(
        "--window_s",
        type=float,
        default=1.0,
        help="Window size in seconds for feature aggregation.",
    )
    parser.add_argument(
        "--overlap",
        type=float,
        default=0.5,
        help="Window overlap ratio in [0, 1).",
    )
    parser.add_argument(
        "--test_size",
        type=float,
        default=0.3,
        help="Per-label holdout fraction at the run level.",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed.")
    return parser.parse_args()


def _entropy(values: np.ndarray, bins: int = 16) -> float:
    if values.size == 0:
        return 0.0
    hist, _ = np.histogram(values, bins=bins)
    total = float(np.sum(hist))
    if total <= 0:
        return 0.0
    probs = hist.astype(np.float64) / total
    probs = probs[probs > 0]
    if probs.size == 0:
        return 0.0
    return float(-np.sum(probs * np.log2(probs)))


def _window_ranges(timestamps_ms: np.ndarray, window_ms: int, overlap: float) -> list[tuple[int, int]]:
    if window_ms <= 0:
        raise ValueError("window_ms must be > 0")
    if not (0.0 <= overlap < 1.0):
        raise ValueError("overlap must be in [0, 1)")
    if timestamps_ms.size == 0:
        return []

    step_ms = max(1, int(math.floor(window_ms * (1.0 - overlap))))
    current = int(timestamps_ms.min())
    stop = int(timestamps_ms.max())
    windows: list[tuple[int, int]] = []
    while current <= stop:
        windows.append((current, current + window_ms))
        current += step_ms
    return windows


def _parse_iso_utc(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _participant_alias_from_metadata(metadata: dict[str, Any]) -> str | None:
    note = str(metadata.get("notes") or "").strip()
    if not note:
        return None
    alias = note.split("|", 1)[0].strip()
    return alias or None


def _participant_name_from_alias(alias: str | None) -> str | None:
    if not alias:
        return None
    normalized = re.sub(r"\s+", " ", alias).strip()
    normalized = re.sub(r"\s*\d+\s*$", "", normalized).strip()
    if not normalized:
        return None
    return PARTICIPANT_NAME_ALIASES.get(normalized.casefold(), normalized)


def build_window_dataframe(
    data_dir: Path,
    *,
    window_s: float,
    overlap: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    runs = load_static_sign_runs(data_dir)
    window_ms = int(round(window_s * 1000.0))
    window_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []

    for run in runs:
        metadata = run.metadata
        run_id = str(metadata["run_id"])
        label = str(metadata["label"]).strip().lower()
        valid_frames: list[tuple[int, float, np.ndarray]] = []

        for frame in run.frames:
            timestamp = frame.get("timestamp")
            try:
                ts = int(timestamp)
            except (TypeError, ValueError):
                continue

            csi_array = parse_csi_array(frame)
            if csi_array is None:
                continue
            try:
                amplitude = iq_to_amplitude(csi_array)
            except ValueError:
                continue
            if amplitude.size == 0:
                continue

            rssi_raw = frame.get("rssi_dbm", frame.get("rssi"))
            try:
                rssi = float(rssi_raw) if rssi_raw is not None else np.nan
            except (TypeError, ValueError):
                rssi = np.nan

            valid_frames.append((ts, rssi, amplitude.astype(np.float32, copy=False)))

        if not valid_frames:
            LOGGER.warning("Skipping run %s because no valid CSI frames were parsed.", run_id)
            continue

        valid_frames.sort(key=lambda item: item[0])
        timestamps = np.asarray([item[0] for item in valid_frames], dtype=np.int64)
        windows = _window_ranges(timestamps, window_ms=window_ms, overlap=overlap)

        start_dt = _parse_iso_utc(metadata.get("start_time"))
        end_dt = _parse_iso_utc(metadata.get("end_time"))
        metadata_duration_s = (
            float((end_dt - start_dt).total_seconds())
            if start_dt is not None and end_dt is not None
            else float("nan")
        )

        run_window_count = 0
        for window_index, (start_ms, end_ms) in enumerate(windows):
            amplitudes: list[np.ndarray] = []
            rssis: list[float] = []
            for ts, rssi, amp in valid_frames:
                if start_ms <= ts < end_ms:
                    amplitudes.append(amp)
                    rssis.append(rssi)
            if not amplitudes:
                continue

            joined_amp = np.concatenate(amplitudes).astype(np.float32, copy=False)
            valid_rssi = np.asarray(rssis, dtype=np.float32)
            valid_rssi = valid_rssi[np.isfinite(valid_rssi)]

            q25, q75 = np.quantile(joined_amp, [0.25, 0.75])
            if valid_rssi.size:
                rssi_min = float(np.min(valid_rssi))
                rssi_max = float(np.max(valid_rssi))
                mean_rssi = float(np.mean(valid_rssi))
                std_rssi = float(np.std(valid_rssi))
                median_rssi = float(np.median(valid_rssi))
                range_rssi = float(rssi_max - rssi_min)
            else:
                rssi_min = np.nan
                rssi_max = np.nan
                mean_rssi = np.nan
                std_rssi = np.nan
                median_rssi = np.nan
                range_rssi = np.nan

            window_rows.append(
                {
                    "run_id": run_id,
                    "label": label,
                    "window_index": window_index,
                    "window_start_ms": int(start_ms),
                    "window_end_ms": int(end_ms),
                    "window_duration_ms": int(end_ms - start_ms),
                    "frame_count": int(len(amplitudes)),
                    "mean_rssi": mean_rssi,
                    "std_rssi": std_rssi,
                    "median_rssi": median_rssi,
                    "min_rssi": rssi_min,
                    "max_rssi": rssi_max,
                    "range_rssi": range_rssi,
                    "mean_amp": float(np.mean(joined_amp)),
                    "std_amp": float(np.std(joined_amp)),
                    "median_amp": float(np.median(joined_amp)),
                    "rms_amp": float(np.sqrt(np.mean(joined_amp * joined_amp))),
                    "iqr_amp": float(q75 - q25),
                    "entropy_amp": _entropy(joined_amp),
                }
            )
            run_window_count += 1

        run_rows.append(
            {
                "run_id": run_id,
                "label": label,
                "raw_frame_count": int(len(run.frames)),
                "valid_frame_count": int(len(valid_frames)),
                "window_count": int(run_window_count),
                "capture_duration_s_from_timestamps": float(
                    (timestamps.max() - timestamps.min()) / 1000.0 if timestamps.size > 1 else 0.0
                ),
                "capture_duration_s_from_metadata": metadata_duration_s,
                "subject_id": metadata.get("subject_id"),
                "participant_alias": _participant_alias_from_metadata(metadata),
                "participant_name": _participant_name_from_alias(_participant_alias_from_metadata(metadata)),
                "environment_id": metadata.get("environment_id"),
                "target_profile": metadata.get("target_profile"),
            }
        )

    if not window_rows:
        raise ValueError(f"No valid window features extracted from dataset: {data_dir}")

    window_df = pd.DataFrame(window_rows).sort_values(["label", "run_id", "window_index"]).reset_index(
        drop=True
    )
    run_df = pd.DataFrame(run_rows).sort_values(["label", "run_id"]).reset_index(drop=True)
    return window_df, run_df


def summarize_dataset(run_df: pd.DataFrame) -> pd.DataFrame:
    summary = (
        run_df.groupby("label", dropna=False)
        .agg(
            num_runs=("run_id", "nunique"),
            total_raw_frames=("raw_frame_count", "sum"),
            total_valid_frames=("valid_frame_count", "sum"),
            total_windows=("window_count", "sum"),
            mean_windows_per_run=("window_count", "mean"),
            median_windows_per_run=("window_count", "median"),
            mean_capture_duration_s=("capture_duration_s_from_timestamps", "mean"),
        )
        .reset_index()
        .sort_values("label")
        .reset_index(drop=True)
    )
    return summary


def summarize_participants(run_df: pd.DataFrame) -> pd.DataFrame:
    participant_df = run_df.copy()
    participant_df["participant_name"] = participant_df["participant_name"].fillna("[unknown]")
    participant_df["participant_alias"] = participant_df["participant_alias"].fillna("[unknown]")
    summary = (
        participant_df.groupby("participant_name", dropna=False)
        .agg(
            session_count=("participant_alias", "nunique"),
            num_runs=("run_id", "nunique"),
            labels_observed=("label", lambda s: ",".join(sorted(set(str(item) for item in s)))),
        )
        .reset_index()
        .sort_values(["participant_name"])
        .reset_index(drop=True)
    )
    return summary


def participant_lookup(run_df: pd.DataFrame) -> dict[str, str]:
    lookup = {}
    for _, row in run_df.iterrows():
        run_id = str(row["run_id"])
        participant_name = row.get("participant_name")
        lookup[run_id] = str(participant_name) if pd.notna(participant_name) and participant_name else "[unknown]"
    return lookup


def summarize_window_features(window_df: pd.DataFrame) -> pd.DataFrame:
    feature_cols = ["mean_rssi", "std_rssi", "mean_amp", "std_amp", "rms_amp", "entropy_amp"]
    summary = (
        window_df.groupby("label", dropna=False)[feature_cols]
        .agg(["mean", "std", "median"])
        .reset_index()
    )
    summary.columns = [
        "label" if col == ("label", "") else f"{col[0]}_{col[1]}" for col in summary.columns.to_flat_index()
    ]
    return summary.sort_values("label").reset_index(drop=True)


def cohens_d(x_a: np.ndarray, x_b: np.ndarray) -> float:
    x_a = x_a[np.isfinite(x_a)]
    x_b = x_b[np.isfinite(x_b)]
    if x_a.size < 2 or x_b.size < 2:
        return float("nan")
    var_a = float(np.var(x_a, ddof=1))
    var_b = float(np.var(x_b, ddof=1))
    pooled = ((x_a.size - 1) * var_a + (x_b.size - 1) * var_b) / max(1, x_a.size + x_b.size - 2)
    if pooled <= 0.0:
        return 0.0
    return float((np.mean(x_a) - np.mean(x_b)) / (math.sqrt(pooled) + EPS))


def compute_feature_effect_sizes(window_df: pd.DataFrame) -> pd.DataFrame:
    baseline = window_df[window_df["label"] == "baseline"]
    hands_up = window_df[window_df["label"] == "hands_up"]
    rows: list[dict[str, Any]] = []
    for feature in FUSION_FEATURES:
        base_values = baseline[feature].to_numpy(dtype=float)
        hands_values = hands_up[feature].to_numpy(dtype=float)
        rows.append(
            {
                "feature": feature,
                "baseline_mean": float(np.nanmean(base_values)),
                "hands_up_mean": float(np.nanmean(hands_values)),
                "delta_hands_up_minus_baseline": float(np.nanmean(hands_values) - np.nanmean(base_values)),
                "cohens_d_hands_up_vs_baseline": cohens_d(hands_values, base_values),
            }
        )
    effect_df = pd.DataFrame(rows)
    effect_df["abs_cohens_d"] = np.abs(effect_df["cohens_d_hands_up_vs_baseline"])
    effect_df = effect_df.sort_values("abs_cohens_d", ascending=False).reset_index(drop=True)
    return effect_df


def balanced_group_split(
    window_df: pd.DataFrame,
    *,
    test_size: float,
    seed: int,
) -> tuple[np.ndarray, np.ndarray, pd.DataFrame]:
    group_df = window_df.groupby("run_id", as_index=False).agg(label=("label", "first"))
    rng = np.random.default_rng(seed)
    test_runs: set[str] = set()
    split_rows: list[dict[str, str]] = []

    for label in LABELS:
        label_runs = sorted(group_df.loc[group_df["label"] == label, "run_id"].astype(str).tolist())
        if len(label_runs) < 2:
            raise ValueError(f"Need at least 2 runs for label '{label}' to create a holdout split.")
        n_test = max(1, int(round(test_size * len(label_runs))))
        n_test = min(n_test, len(label_runs) - 1)
        chosen = rng.choice(np.asarray(label_runs, dtype=object), size=n_test, replace=False)
        for run_id in label_runs:
            phase = "test" if run_id in chosen.tolist() else "train"
            split_rows.append({"phase": phase, "label": label, "run_id": run_id})
        test_runs.update(str(item) for item in chosen.tolist())

    test_mask = window_df["run_id"].astype(str).isin(test_runs).to_numpy()
    train_idx = np.flatnonzero(~test_mask)
    test_idx = np.flatnonzero(test_mask)
    if train_idx.size == 0 or test_idx.size == 0:
        raise ValueError("Split produced an empty train or test partition.")

    split_df = pd.DataFrame(split_rows).sort_values(["phase", "label", "run_id"]).reset_index(drop=True)
    return train_idx, test_idx, split_df


def build_classifier(model_kind: str, seed: int) -> Pipeline:
    if model_kind == "logreg":
        estimator = LogisticRegression(max_iter=2000, random_state=seed)
    elif model_kind == "linear_svm":
        estimator = LinearSVC(random_state=seed)
    else:
        raise ValueError(f"Unsupported model kind: {model_kind}")

    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("classifier", estimator),
        ]
    )


def decision_scores(model: Pipeline, x: np.ndarray) -> np.ndarray | None:
    if hasattr(model, "decision_function"):
        raw = model.decision_function(x)
        arr = np.asarray(raw, dtype=float)
        if arr.ndim == 1:
            return arr
        classes = list(model.named_steps["classifier"].classes_)
        pos_idx = classes.index(POS_LABEL)
        return arr[:, pos_idx]
    if hasattr(model, "predict_proba"):
        probs = model.predict_proba(x)
        classes = list(model.named_steps["classifier"].classes_)
        pos_idx = classes.index(POS_LABEL)
        return np.asarray(probs[:, pos_idx], dtype=float)
    return None


def classification_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    *,
    scores: np.ndarray | None,
) -> dict[str, float]:
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=list(LABELS),
        average="binary",
        pos_label=POS_LABEL,
        zero_division=0,
    )
    metrics = {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "num_windows": int(y_true.size),
    }
    if scores is not None and len(np.unique(y_true)) == 2:
        y_binary = (y_true == POS_LABEL).astype(int)
        metrics["roc_auc"] = float(roc_auc_score(y_binary, scores))
    else:
        metrics["roc_auc"] = float("nan")
    return metrics


def majority_label(values: list[str]) -> str:
    counts = Counter(values)
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def evaluate_models(
    window_df: pd.DataFrame,
    *,
    train_idx: np.ndarray,
    test_idx: np.ndarray,
    seed: int,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    feature_sets = {
        "RSSI_logreg": {"columns": RSSI_FEATURES, "model_kind": "logreg"},
        "CSI_logreg": {"columns": CSI_FEATURES, "model_kind": "logreg"},
        "CSI_linear_svm": {"columns": CSI_FEATURES, "model_kind": "linear_svm"},
        "Fusion_logreg": {"columns": FUSION_FEATURES, "model_kind": "logreg"},
        "Fusion_linear_svm": {"columns": FUSION_FEATURES, "model_kind": "linear_svm"},
    }

    train_df = window_df.iloc[train_idx].reset_index(drop=True)
    test_df = window_df.iloc[test_idx].reset_index(drop=True)
    y_train = train_df["label"].astype(str).to_numpy()
    y_test = test_df["label"].astype(str).to_numpy()

    overall_rows: list[dict[str, Any]] = []
    per_run_rows: list[dict[str, Any]] = []
    prediction_rows: list[dict[str, Any]] = []
    artifacts: dict[str, Any] = {"models": {}, "confusion_matrices": {}, "scores": {}}

    for method, spec in feature_sets.items():
        cols = list(spec["columns"])
        model = build_classifier(str(spec["model_kind"]), seed=seed)
        x_train = train_df[cols].to_numpy(dtype=float)
        x_test = test_df[cols].to_numpy(dtype=float)

        model.fit(x_train, y_train)
        pred_test = np.asarray(model.predict(x_test), dtype=object)
        scores = decision_scores(model, x_test)

        metrics = classification_metrics(y_test, pred_test, scores=scores)
        cm = confusion_matrix(y_test, pred_test, labels=list(LABELS))

        run_majority_rows: list[dict[str, Any]] = []
        grouped = pd.DataFrame(
            {
                "run_id": test_df["run_id"].astype(str).to_numpy(),
                "true_label": y_test,
                "pred_label": pred_test,
            }
        ).groupby("run_id", sort=True)
        for run_id, group in grouped:
            run_true = str(group["true_label"].iloc[0])
            run_pred = majority_label(group["pred_label"].astype(str).tolist())
            run_accuracy = float(np.mean(group["true_label"].astype(str) == group["pred_label"].astype(str)))
            run_majority_rows.append(
                {
                    "method": method,
                    "run_id": str(run_id),
                    "true_label": run_true,
                    "pred_majority_label": run_pred,
                    "majority_vote_correct": bool(run_pred == run_true),
                    "window_accuracy": run_accuracy,
                    "num_windows": int(len(group)),
                }
            )
        run_majority_acc = float(
            np.mean([row["majority_vote_correct"] for row in run_majority_rows])
        )

        row = {
            "method": method,
            "feature_columns": ",".join(cols),
            "accuracy": metrics["accuracy"],
            "balanced_accuracy": metrics["balanced_accuracy"],
            "precision": metrics["precision"],
            "recall": metrics["recall"],
            "f1": metrics["f1"],
            "roc_auc": metrics["roc_auc"],
            "run_majority_acc": run_majority_acc,
            "num_windows": metrics["num_windows"],
        }
        overall_rows.append(row)
        per_run_rows.extend(run_majority_rows)

        for idx, (_, record) in enumerate(test_df.iterrows()):
            prediction_rows.append(
                {
                    "method": method,
                    "run_id": str(record["run_id"]),
                    "label": str(record["label"]),
                    "window_index": int(record["window_index"]),
                    "pred_label": str(pred_test[idx]),
                    "score_hands_up": float(scores[idx]) if scores is not None else np.nan,
                }
            )

        artifacts["models"][method] = model
        artifacts["confusion_matrices"][method] = cm.astype(int).tolist()
        artifacts["scores"][method] = scores

    overall_df = pd.DataFrame(overall_rows).sort_values(
        by=["f1", "balanced_accuracy", "accuracy", "run_majority_acc"],
        ascending=False,
    ).reset_index(drop=True)
    per_run_df = pd.DataFrame(per_run_rows).sort_values(["method", "run_id"]).reset_index(drop=True)
    prediction_df = pd.DataFrame(prediction_rows).sort_values(
        ["method", "run_id", "window_index"]
    ).reset_index(drop=True)
    return overall_df, per_run_df, prediction_df, artifacts


def format_markdown_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "_No rows_"
    columns = list(frame.columns)
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join(["---"] * len(columns)) + " |"
    rows = []
    for _, row in frame.iterrows():
        values: list[str] = []
        for col in columns:
            value = row[col]
            if isinstance(value, float):
                values.append(f"{value:.4f}" if np.isfinite(value) else "nan")
            else:
                values.append(str(value))
        rows.append("| " + " | ".join(values) + " |")
    return "\n".join([header, sep, *rows])


def format_latex_table(frame: pd.DataFrame) -> str:
    if frame.empty:
        return "% No rows"

    def render(value: Any) -> str:
        if isinstance(value, float):
            text = f"{value:.4f}" if np.isfinite(value) else "nan"
        else:
            text = str(value)
        return (
            text.replace("\\", "\\textbackslash{}")
            .replace("_", "\\_")
            .replace("%", "\\%")
            .replace("&", "\\&")
        )

    columns = list(frame.columns)
    lines = [
        "\\begin{tabular}{" + "l" * len(columns) + "}",
        "\\hline",
        " & ".join(render(col) for col in columns) + " \\\\",
        "\\hline",
    ]
    for _, row in frame.iterrows():
        lines.append(" & ".join(render(row[col]) for col in columns) + " \\\\")
    lines.extend(["\\hline", "\\end{tabular}"])
    return "\n".join(lines)


def plot_boxplot(window_df: pd.DataFrame, feature: str, title: str, out_path: Path) -> None:
    plt.figure(figsize=(7.5, 4.5))
    data = [window_df.loc[window_df["label"] == label, feature].dropna().to_numpy() for label in LABELS]
    plt.boxplot(data, tick_labels=list(LABELS), showmeans=True)
    plt.title(title)
    plt.ylabel(feature)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


def plot_effect_sizes(effect_df: pd.DataFrame, out_path: Path) -> None:
    top = effect_df.head(8).iloc[::-1]
    plt.figure(figsize=(8.5, 5.5))
    plt.barh(
        top["feature"].astype(str),
        top["cohens_d_hands_up_vs_baseline"].to_numpy(dtype=float),
        color="#4c78a8",
    )
    plt.axvline(0.0, color="black", linewidth=1.0)
    plt.title("Feature Effect Sizes: hands_up vs baseline")
    plt.xlabel("Cohen's d")
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


def plot_pca_windows(
    window_df: pd.DataFrame,
    *,
    train_idx: np.ndarray,
    seed: int,
    out_path: Path,
) -> None:
    train_df = window_df.iloc[train_idx].reset_index(drop=True)
    x_train = train_df[FUSION_FEATURES].to_numpy(dtype=float)
    x_all = window_df[FUSION_FEATURES].to_numpy(dtype=float)

    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    x_train_scaled = scaler.fit_transform(imputer.fit_transform(x_train))
    x_all_scaled = scaler.transform(imputer.transform(x_all))

    pca = PCA(n_components=2, random_state=seed)
    x_train_pca = pca.fit_transform(x_train_scaled)
    x_all_pca = pca.transform(x_all_scaled)
    _ = x_train_pca

    split_mask = np.zeros(len(window_df), dtype=bool)
    split_mask[train_idx] = True
    colors = {"baseline": "#4c78a8", "hands_up": "#f58518"}
    markers = {True: "o", False: "^"}
    labels = {True: "train", False: "test"}

    plt.figure(figsize=(7.5, 6.0))
    for phase in (True, False):
        for label in LABELS:
            mask = split_mask & (window_df["label"].to_numpy() == label) if phase else (
                (~split_mask) & (window_df["label"].to_numpy() == label)
            )
            if not np.any(mask):
                continue
            plt.scatter(
                x_all_pca[mask, 0],
                x_all_pca[mask, 1],
                s=24,
                alpha=0.75,
                marker=markers[phase],
                color=colors[label],
                label=f"{label} ({labels[phase]})",
            )
    plt.title("PCA of Window-Level RSSI + CSI Features")
    plt.xlabel("PC1")
    plt.ylabel("PC2")
    plt.legend()
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


def plot_confusion_matrix_figure(cm: np.ndarray, out_path: Path, title: str) -> None:
    plt.figure(figsize=(5.5, 4.5))
    plt.imshow(cm, cmap="Blues")
    plt.colorbar()
    plt.xticks(range(len(LABELS)), LABELS)
    plt.yticks(range(len(LABELS)), LABELS)
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            plt.text(j, i, str(int(cm[i, j])), ha="center", va="center", color="black")
    plt.xlabel("Predicted label")
    plt.ylabel("True label")
    plt.title(title)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


def plot_roc_curve_figure(y_true: np.ndarray, scores: np.ndarray, out_path: Path, title: str) -> None:
    y_binary = (y_true == POS_LABEL).astype(int)
    fpr, tpr, _ = roc_curve(y_binary, scores)
    auc = roc_auc_score(y_binary, scores)
    plt.figure(figsize=(5.5, 4.5))
    plt.plot(fpr, tpr, label=f"AUC={auc:.3f}", color="#4c78a8", linewidth=2.0)
    plt.plot([0, 1], [0, 1], linestyle="--", color="gray", linewidth=1.0)
    plt.xlabel("False positive rate")
    plt.ylabel("True positive rate")
    plt.title(title)
    plt.legend(loc="lower right")
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()


def plot_example_timeseries(window_df: pd.DataFrame, out_path: Path) -> None:
    example_runs = []
    for label in LABELS:
        label_runs = sorted(window_df.loc[window_df["label"] == label, "run_id"].astype(str).unique().tolist())
        if label_runs:
            example_runs.append((label, label_runs[0]))

    if not example_runs:
        return

    fig, axes = plt.subplots(len(example_runs), 2, figsize=(11, 4.2 * len(example_runs)), sharex=False)
    if len(example_runs) == 1:
        axes = np.asarray([axes])

    for row_idx, (label, run_id) in enumerate(example_runs):
        subset = window_df.loc[window_df["run_id"] == run_id].copy()
        subset = subset.sort_values("window_start_ms")
        t = (subset["window_start_ms"] - subset["window_start_ms"].min()) / 1000.0
        axes[row_idx, 0].plot(t, subset["mean_amp"], color="#4c78a8", linewidth=1.8)
        axes[row_idx, 0].set_title(f"{label}: mean_amp over time ({run_id})")
        axes[row_idx, 0].set_xlabel("seconds")
        axes[row_idx, 0].set_ylabel("mean_amp")

        axes[row_idx, 1].plot(t, subset["mean_rssi"], color="#f58518", linewidth=1.8)
        axes[row_idx, 1].set_title(f"{label}: mean_rssi over time ({run_id})")
        axes[row_idx, 1].set_xlabel("seconds")
        axes[row_idx, 1].set_ylabel("mean_rssi")

    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close(fig)


def save_outputs(
    *,
    out_dir: Path,
    data_dir: Path,
    window_df: pd.DataFrame,
    run_df: pd.DataFrame,
    dataset_summary_df: pd.DataFrame,
    participant_summary_df: pd.DataFrame,
    feature_summary_df: pd.DataFrame,
    effect_df: pd.DataFrame,
    split_df: pd.DataFrame,
    overall_df: pd.DataFrame,
    per_run_df: pd.DataFrame,
    prediction_df: pd.DataFrame,
    artifacts: dict[str, Any],
    test_idx: np.ndarray,
    train_idx: np.ndarray,
    seed: int,
    window_s: float,
    overlap: float,
    plots_available: bool,
) -> None:
    tables_dir = out_dir / "tables"
    figs_dir = out_dir / "figs"
    tables_dir.mkdir(parents=True, exist_ok=True)
    if plots_available:
        figs_dir.mkdir(parents=True, exist_ok=True)

    dataset_summary_df.to_csv(tables_dir / "table_dataset_summary.csv", index=False)
    run_df.to_csv(tables_dir / "table_run_summary.csv", index=False)
    participant_summary_df.to_csv(tables_dir / "table_participant_summary.csv", index=False)
    split_df.to_csv(tables_dir / "table_split_summary.csv", index=False)
    feature_summary_df.to_csv(tables_dir / "table_feature_summary_by_label.csv", index=False)
    effect_df.to_csv(tables_dir / "table_feature_effect_sizes.csv", index=False)
    overall_df.to_csv(tables_dir / "table_metrics_overall.csv", index=False)
    per_run_df.to_csv(tables_dir / "table_metrics_by_run.csv", index=False)
    prediction_df.to_csv(tables_dir / "table_predictions_test.csv", index=False)
    (tables_dir / "table_metrics_overall_snippet.md").write_text(
        format_markdown_table(overall_df), encoding="utf-8"
    )
    (tables_dir / "table_metrics_overall_snippet.tex").write_text(
        format_latex_table(overall_df),
        encoding="utf-8",
    )
    (tables_dir / "confusion_matrices.json").write_text(
        json.dumps(artifacts["confusion_matrices"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    best_method = str(overall_df.iloc[0]["method"])
    if plots_available:
        plot_boxplot(
            window_df,
            feature="mean_rssi",
            title="Window Mean RSSI by Label",
            out_path=figs_dir / "boxplot_mean_rssi_by_label.png",
        )
        plot_boxplot(
            window_df,
            feature="mean_amp",
            title="Window Mean CSI Amplitude by Label",
            out_path=figs_dir / "boxplot_mean_amp_by_label.png",
        )
        plot_effect_sizes(effect_df, figs_dir / "feature_effect_sizes.png")
        plot_pca_windows(window_df, train_idx=train_idx, seed=seed, out_path=figs_dir / "pca_windows.png")
        plot_example_timeseries(window_df, figs_dir / "timeseries_example_runs.png")

        cm = np.asarray(artifacts["confusion_matrices"][best_method], dtype=int)
        plot_confusion_matrix_figure(
            cm,
            figs_dir / "confusion_matrix_best_model.png",
            title=f"Confusion Matrix ({best_method})",
        )

        best_predictions = prediction_df.loc[prediction_df["method"] == best_method].copy()
        best_scores = best_predictions["score_hands_up"].to_numpy(dtype=float)
        if np.isfinite(best_scores).any():
            y_true = best_predictions["label"].astype(str).to_numpy()
            plot_roc_curve_figure(
                y_true,
                best_scores,
                figs_dir / "roc_curve_best_model.png",
                title=f"ROC Curve ({best_method})",
            )

    split_train = split_df.loc[split_df["phase"] == "train", "run_id"].astype(str).tolist()
    split_test = split_df.loc[split_df["phase"] == "test", "run_id"].astype(str).tolist()
    participant_by_run = participant_lookup(run_df)
    train_participants = sorted({participant_by_run.get(run_id, "[unknown]") for run_id in split_train})
    test_participants = sorted({participant_by_run.get(run_id, "[unknown]") for run_id in split_test})
    participant_counts = ", ".join(
        f"{row['participant_name']} x{int(row['session_count'])}"
        for _, row in participant_summary_df.iterrows()
        if str(row["participant_name"]) != "[unknown]"
    )

    top_effect = effect_df.iloc[0]
    best_row = overall_df.iloc[0]
    label_means = (
        window_df.groupby("label")[["mean_rssi", "mean_amp"]].mean().reindex(list(LABELS))
    )

    report_lines = [
        "# Static Gesture Analysis Report",
        "",
        "## Task",
        "Classify `baseline` vs `hands_up` from ESP32 RSSI/CSI windows using leakage-safe run-level evaluation.",
        "",
        "## Dataset Summary",
        f"- Data root: `{data_dir}`",
        f"- Window size: `{window_s:.2f}s`, overlap: `{overlap:.2f}`",
        f"- Total runs: `{run_df['run_id'].nunique()}`",
        f"- Total windows: `{len(window_df)}`",
        "",
        format_markdown_table(dataset_summary_df),
        "",
        "## Participant Structure",
        "- Participants were recovered from `metadata.notes` because `subject_id` is `subject01` for all runs.",
        f"- Distinct participants: `{participant_summary_df['participant_name'].nunique()}`",
        f"- Session counts by participant: `{participant_counts}`",
        "",
        format_markdown_table(participant_summary_df),
        "",
        "## Split Protocol",
        "A label-balanced holdout split was applied at the `run_id` level so packets/windows from the same run never appear in both train and test.",
        f"- Train runs: `{', '.join(split_train)}`",
        f"- Test runs: `{', '.join(split_test)}`",
        f"- Train participants: `{', '.join(train_participants)}`",
        f"- Test participants: `{', '.join(test_participants)}`",
        f"- Train windows: `{len(train_idx)}`",
        f"- Test windows: `{len(test_idx)}`",
        "",
        "## Overall Metrics",
        "",
        format_markdown_table(overall_df),
        "",
        "## Best Model",
        (
            f"`{best_method}` achieved `accuracy={best_row['accuracy']:.4f}`, "
            f"`balanced_accuracy={best_row['balanced_accuracy']:.4f}`, "
            f"`precision={best_row['precision']:.4f}`, "
            f"`recall={best_row['recall']:.4f}`, "
            f"`f1={best_row['f1']:.4f}`, "
            f"`roc_auc={best_row['roc_auc']:.4f}`, "
            f"`run_majority_acc={best_row['run_majority_acc']:.4f}` on the held-out runs."
        ),
        (
            f"Window-level confusion matrix for `{best_method}` (`{LABELS[0]}`, `{LABELS[1]}` order): "
            f"`{artifacts['confusion_matrices'][best_method]}`"
        ),
        "",
        "## Feature Separation",
        (
            f"Strongest single-feature separation was `{top_effect['feature']}` with "
            f"`delta={top_effect['delta_hands_up_minus_baseline']:.4f}` and "
            f"`Cohen_d={top_effect['cohens_d_hands_up_vs_baseline']:.4f}` "
            "for `hands_up - baseline`."
        ),
        "",
        format_markdown_table(effect_df.head(6)),
        "",
        "## Observations",
        (
            f"- Mean RSSI: baseline `{label_means.loc['baseline', 'mean_rssi']:.3f}` vs "
            f"hands_up `{label_means.loc['hands_up', 'mean_rssi']:.3f}`."
        ),
        (
            f"- Mean CSI amplitude: baseline `{label_means.loc['baseline', 'mean_amp']:.3f}` vs "
            f"hands_up `{label_means.loc['hands_up', 'mean_amp']:.3f}`."
        ),
        "- Fusion models quantify whether CSI adds value beyond RSSI-only features on the same run-level split.",
        "- The dataset includes three participants with different body builds, so the task is not strictly single-subject.",
        "- However, the split is run-level rather than leave-one-subject-out: the same participant can appear in both train and test under different labels.",
        "- Therefore the reported metrics reflect posture discrimination with some inter-person variability, not clean cross-subject generalization.",
        "",
        "## Outputs",
        "- `tables/table_dataset_summary.csv`",
        "- `tables/table_run_summary.csv`",
        "- `tables/table_participant_summary.csv`",
        "- `tables/table_split_summary.csv`",
        "- `tables/table_feature_summary_by_label.csv`",
        "- `tables/table_feature_effect_sizes.csv`",
        "- `tables/table_metrics_overall.csv`",
        "- `tables/table_metrics_by_run.csv`",
        "- `tables/table_predictions_test.csv`",
    ]
    if not plots_available:
        report_lines.extend(
            [
                "",
                "## Plot Generation Note",
                (
                    "- Figure generation was skipped because matplotlib is unavailable in the local "
                    f"Python stack (`{MATPLOTLIB_IMPORT_ERROR}`)."
                ),
            ]
        )
    else:
        report_lines.extend(
            [
                "- `figs/boxplot_mean_rssi_by_label.png`",
                "- `figs/boxplot_mean_amp_by_label.png`",
                "- `figs/feature_effect_sizes.png`",
                "- `figs/pca_windows.png`",
                "- `figs/confusion_matrix_best_model.png`",
                "- `figs/roc_curve_best_model.png`",
                "- `figs/timeseries_example_runs.png`",
            ]
        )
    (out_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")


def run_analysis(args: argparse.Namespace) -> None:
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)

    window_df, run_df = build_window_dataframe(data_dir, window_s=args.window_s, overlap=args.overlap)
    dataset_summary_df = summarize_dataset(run_df)
    participant_summary_df = summarize_participants(run_df)
    feature_summary_df = summarize_window_features(window_df)
    effect_df = compute_feature_effect_sizes(window_df)
    train_idx, test_idx, split_df = balanced_group_split(window_df, test_size=args.test_size, seed=args.seed)
    overall_df, per_run_df, prediction_df, artifacts = evaluate_models(
        window_df,
        train_idx=train_idx,
        test_idx=test_idx,
        seed=args.seed,
    )

    save_outputs(
        out_dir=out_dir,
        data_dir=data_dir,
        window_df=window_df,
        run_df=run_df,
        dataset_summary_df=dataset_summary_df,
        participant_summary_df=participant_summary_df,
        feature_summary_df=feature_summary_df,
        effect_df=effect_df,
        split_df=split_df,
        overall_df=overall_df,
        per_run_df=per_run_df,
        prediction_df=prediction_df,
        artifacts=artifacts,
        test_idx=test_idx,
        train_idx=train_idx,
        seed=args.seed,
        window_s=args.window_s,
        overlap=args.overlap,
        plots_available=MATPLOTLIB_AVAILABLE,
    )

    print("=== Dataset summary ===")
    print(dataset_summary_df.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print("=== Overall metrics (test split) ===")
    print(overall_df.to_string(index=False, float_format=lambda value: f"{value:.4f}"))
    print(f"Outputs written to: {out_dir}")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    if args.window_s <= 0:
        raise ValueError("--window_s must be > 0")
    if not (0.0 <= args.overlap < 1.0):
        raise ValueError("--overlap must be in [0, 1)")
    if not (0.0 < args.test_size < 1.0):
        raise ValueError("--test_size must be in (0, 1)")
    run_analysis(args)


if __name__ == "__main__":
    main()
