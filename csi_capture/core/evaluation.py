from __future__ import annotations

from typing import Any, Sequence

import numpy as np


def classification_metrics(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    labels: Sequence[str],
    *,
    positive_label: str | None = None,
) -> dict[str, Any]:
    from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support

    y_true_arr = np.asarray(list(y_true), dtype=object)
    y_pred_arr = np.asarray(list(y_pred), dtype=object)

    selected_labels = list(labels)
    if not selected_labels:
        raise ValueError("labels must not be empty")
    if positive_label is not None and positive_label not in selected_labels:
        raise ValueError("positive_label must be present in labels")
    metric_options: dict[str, Any] = {
        "labels": selected_labels,
        "average": "binary" if positive_label is not None else "macro",
        "zero_division": 0,
    }
    if positive_label is not None:
        metric_options["pos_label"] = positive_label
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_true_arr,
        y_pred_arr,
        **metric_options,
    )
    cm = confusion_matrix(y_true_arr, y_pred_arr, labels=selected_labels)

    return {
        "accuracy": float(accuracy_score(y_true_arr, y_pred_arr)),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "labels": selected_labels,
        "confusion_matrix": cm.astype(int).tolist(),
        "num_samples": int(y_true_arr.size),
    }


def per_run_summary(
    run_ids: Sequence[str],
    y_true: Sequence[str],
    y_pred: Sequence[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    run_arr = np.asarray(list(run_ids), dtype=object)
    true_arr = np.asarray(list(y_true), dtype=object)
    pred_arr = np.asarray(list(y_pred), dtype=object)

    for run_id in sorted(set(run_arr.tolist())):
        mask = run_arr == run_id
        if not np.any(mask):
            continue
        run_true = true_arr[mask]
        run_pred = pred_arr[mask]
        rows.append(
            {
                "run_id": str(run_id),
                "num_windows": int(run_true.size),
                "accuracy": float(np.mean(run_true == run_pred)),
                "true_label_counts": _label_count_dict(run_true),
                "pred_label_counts": _label_count_dict(run_pred),
            }
        )

    return rows


def _label_count_dict(values: np.ndarray) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values.tolist():
        key = str(value)
        out[key] = out.get(key, 0) + 1
    return out
