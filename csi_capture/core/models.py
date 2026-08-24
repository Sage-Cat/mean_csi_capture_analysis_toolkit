from __future__ import annotations

import pickle
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ModelError(RuntimeError):
    """Raised for model creation/load/save issues."""


def create_classifier(model_name: str, *, random_state: int | None = None):
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.svm import LinearSVC

    normalized = model_name.strip().lower()
    if normalized in {"svm_linear", "linear_svm", "svm"}:
        estimator = LinearSVC(random_state=random_state)
    elif normalized in {"logreg", "logistic", "logistic_regression"}:
        estimator = LogisticRegression(max_iter=2000, random_state=random_state)
    else:
        raise ModelError(f"Unsupported model '{model_name}'. Use svm_linear or logreg")

    return Pipeline(
        [
            ("scaler", StandardScaler()),
            ("classifier", estimator),
        ]
    )


def save_model_artifact(path: Path, model: Any, metadata: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "model": model,
        "metadata": metadata,
    }
    with path.open("wb") as handle:
        pickle.dump(payload, handle)


def load_model_artifact(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ModelError(f"Model artifact does not exist: {path}")
    with path.open("rb") as handle:
        payload = pickle.load(handle)
    if not isinstance(payload, dict) or "model" not in payload:
        raise ModelError(f"Invalid model artifact format: {path}")
    return payload
