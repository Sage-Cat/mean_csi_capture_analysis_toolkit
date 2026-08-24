from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

LAYOUT_CANONICAL_V1 = "canonical_v1"


@dataclass(frozen=True)
class TrialPaths:
    trial_dir: Path
    packet_path: Path


@dataclass(frozen=True)
class RunLayout:
    root: Path
    experiment_id: str
    dataset_id: str
    run_id: str
    run_dir: Path
    manifest_path: Path
    layout_style: str = LAYOUT_CANONICAL_V1

    def trial_paths(self, trial_id: str, output_format: str = "jsonl") -> TrialPaths:
        if output_format not in {"jsonl", "csv"}:
            raise ValueError("output_format must be 'jsonl' or 'csv'")
        trial_dir = self.run_dir / "trials" / f"trial_{trial_id}"
        return TrialPaths(
            trial_dir=trial_dir,
            packet_path=trial_dir / f"packets.{output_format}",
        )


def build_run_layout(
    *,
    root: Path | str,
    experiment_id: str,
    dataset_id: str,
    run_id: str,
) -> RunLayout:
    """Resolve the single maintained run layout below a caller-owned root."""

    for field, value in (
        ("experiment_id", experiment_id),
        ("dataset_id", dataset_id),
        ("run_id", run_id),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a non-empty string")

    root_path = Path(root)
    run_dir = root_path / experiment_id / dataset_id / "runs" / f"run_{run_id}"
    return RunLayout(
        root=root_path,
        experiment_id=experiment_id,
        dataset_id=dataset_id,
        run_id=run_id,
        run_dir=run_dir,
        manifest_path=run_dir / "manifest.json",
    )


def feature_artifact_dir(
    *,
    root: Path | str,
    experiment_id: str,
    dataset_id: str,
    feature_set_id: str,
) -> Path:
    return Path(root) / experiment_id / dataset_id / "features" / feature_set_id


def model_artifact_dir(
    *,
    root: Path | str,
    experiment_id: str,
    dataset_id: str,
    model_id: str,
) -> Path:
    return Path(root) / experiment_id / dataset_id / "models" / model_id


def evaluation_artifact_dir(
    *,
    root: Path | str,
    experiment_id: str,
    dataset_id: str,
    report_id: str,
) -> Path:
    return Path(root) / experiment_id / dataset_id / "reports" / report_id
