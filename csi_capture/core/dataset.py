from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

from csi_capture.core.domain import CANONICAL_SCHEMA_NAME, CANONICAL_SCHEMA_VERSION


@dataclass(frozen=True)
class RunCapture:
    run_dir: Path
    metadata: dict[str, Any]
    frames: list[dict[str, Any]]


@dataclass(frozen=True)
class NormalizedTrialCapture:
    trial_id: str
    packet_path: Path
    metadata: dict[str, Any]
    records: list[dict[str, Any]]


@dataclass(frozen=True)
class NormalizedRun:
    run_dir: Path
    manifest: dict[str, Any]
    packet_files: list[Path]
    trials: list[NormalizedTrialCapture]


class DatasetValidationError(ValueError):
    """Raised when a dataset violates an explicitly selected schema contract."""


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise DatasetValidationError(f"Expected JSON object: {path}")
    return payload


def _iter_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _iter_csv(path: Path) -> Iterator[dict[str, Any]]:
    with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
        yield from csv.DictReader(handle)


def iter_packet_rows(path: Path) -> Iterator[dict[str, Any]]:
    """Read packet records from one explicitly supplied supported file."""

    suffix = path.suffix.lower()
    if suffix in {".jsonl", ".txt"}:
        yield from _iter_jsonl(path)
        return
    if suffix == ".csv":
        yield from _iter_csv(path)
        return
    if suffix == ".json":
        payload = _read_json(path)
        records = payload.get("records")
        if isinstance(records, list):
            for row in records:
                if isinstance(row, dict):
                    yield row
            return
        yield payload
        return
    raise DatasetValidationError(f"Unsupported packet file extension: {path}")


def validate_run_metadata(
    metadata: dict[str, Any],
    *,
    expected_experiment: str | None = None,
    allowed_labels: Sequence[str] | None = None,
    expected_schema_version: int | str | None = None,
) -> None:
    """Validate a labeled-run metadata object against caller-owned parameters."""

    required_str_fields = (
        "experiment_name",
        "label",
        "run_id",
        "device",
        "serial_dev",
        "start_time",
        "end_time",
    )
    for field in required_str_fields:
        value = metadata.get(field)
        if not isinstance(value, str) or not value.strip():
            raise DatasetValidationError(f"metadata.{field} must be a non-empty string")

    experiment_name = str(metadata["experiment_name"]).strip()
    if expected_experiment is not None and experiment_name != expected_experiment:
        raise DatasetValidationError(
            f"metadata.experiment_name must be '{expected_experiment}'"
        )

    label = str(metadata["label"]).strip()
    if allowed_labels is not None:
        accepted = tuple(str(item).strip() for item in allowed_labels)
        if not accepted or any(not item for item in accepted):
            raise ValueError("allowed_labels must contain non-empty strings")
        if label not in accepted:
            raise DatasetValidationError(
                f"metadata.label must be one of {sorted(accepted)}"
            )

    schema_version = metadata.get("schema_version")
    if schema_version is None:
        raise DatasetValidationError("metadata.schema_version is required")
    if expected_schema_version is not None and schema_version != expected_schema_version:
        raise DatasetValidationError(
            f"metadata.schema_version must be {expected_schema_version!r}"
        )

    if not isinstance(metadata.get("sampling_params"), dict):
        raise DatasetValidationError("metadata.sampling_params must be an object")

    for optional in ("subject_id", "environment_id", "notes", "target_profile", "chip"):
        value = metadata.get(optional)
        if value is not None and not isinstance(value, str):
            raise DatasetValidationError(f"metadata.{optional} must be string or null")

    environment_profile = metadata.get("environment_profile")
    if environment_profile is not None and not isinstance(environment_profile, dict):
        raise DatasetValidationError("metadata.environment_profile must be object or null")


def validate_canonical_manifest(manifest: dict[str, Any]) -> None:
    if not isinstance(manifest, dict):
        raise DatasetValidationError("manifest must be an object")
    if manifest.get("schema_name") != CANONICAL_SCHEMA_NAME:
        raise DatasetValidationError(
            f"manifest.schema_name must be '{CANONICAL_SCHEMA_NAME}'"
        )
    if manifest.get("schema_version") != CANONICAL_SCHEMA_VERSION:
        raise DatasetValidationError(
            f"manifest.schema_version must be '{CANONICAL_SCHEMA_VERSION}'"
        )
    experiment = manifest.get("experiment")
    if not isinstance(experiment, dict):
        raise DatasetValidationError("manifest.experiment must be an object")
    experiment_id = experiment.get("experiment_id")
    if not isinstance(experiment_id, str) or not experiment_id.strip():
        raise DatasetValidationError("manifest.experiment.experiment_id must be a non-empty string")
    for field in ("dataset_id", "run_id", "status", "created_at_utc"):
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip():
            raise DatasetValidationError(f"manifest.{field} must be a non-empty string")


def _packet_paths_from_manifest(run_dir: Path, manifest: dict[str, Any]) -> list[Path]:
    packet_files: list[Path] = []
    for trial in manifest.get("trials", []):
        if not isinstance(trial, dict):
            continue
        found = False
        for field in ("output_file", "packet_path"):
            raw_path = trial.get(field)
            if not isinstance(raw_path, str) or not raw_path.strip():
                continue
            packet_path = Path(raw_path)
            candidates = [packet_path] if packet_path.is_absolute() else [run_dir / packet_path]
            for candidate in candidates:
                if candidate.exists() and candidate not in packet_files:
                    packet_files.append(candidate)
                    found = True
                    break
            if found:
                break
    if packet_files:
        return packet_files

    for filename in ("frames.jsonl", "capture.jsonl", "capture.csv"):
        candidate = run_dir / filename
        if candidate.exists():
            packet_files.append(candidate)
    if packet_files:
        return packet_files

    for pattern in ("capture.jsonl", "capture.csv", "frames.jsonl"):
        for path in sorted(run_dir.rglob(pattern)):
            if path not in packet_files:
                packet_files.append(path)
    return packet_files


def _load_legacy_labeled_manifest(
    meta_path: Path,
    *,
    expected_experiment: str | None,
    allowed_labels: Sequence[str] | None,
    expected_schema_version: int | str | None,
) -> dict[str, Any]:
    metadata = _read_json(meta_path)
    validate_run_metadata(
        metadata,
        expected_experiment=expected_experiment,
        allowed_labels=allowed_labels,
        expected_schema_version=expected_schema_version,
    )
    run_dir = meta_path.parent
    packet_path = next(
        (run_dir / name for name in ("frames.jsonl", "capture.jsonl", "capture.csv") if (run_dir / name).exists()),
        None,
    )
    if packet_path is None:
        raise DatasetValidationError(f"Missing packet file for run: {run_dir}")

    experiment_name = str(metadata["experiment_name"]).strip()
    label = str(metadata["label"]).strip()
    dataset_id = str(metadata.get("dataset_id") or run_dir.parent.parent.name)
    return {
        "schema_name": CANONICAL_SCHEMA_NAME,
        "schema_version": CANONICAL_SCHEMA_VERSION,
        "layout_style": "legacy_labeled_v1",
        "dataset_id": dataset_id,
        "run_id": str(metadata["run_id"]),
        "status": "completed",
        "created_at_utc": str(metadata["start_time"]),
        "experiment": {"experiment_id": experiment_name},
        "scenario": {
            "scenario_id": label,
            "tags": [label],
            "room_id": metadata.get("environment_id"),
            "notes": metadata.get("notes") or "",
        },
        "subject": {
            "subject_id": metadata.get("subject_id"),
            "cohort_id": None,
            "attributes": {},
        },
        "capture": metadata.get("sampling_params", {}),
        "provenance": {
            "target_profile_id": metadata.get("target_profile"),
            "device_path": metadata.get("serial_dev"),
            "device_realpath": metadata.get("serial_realpath"),
            "notes": metadata.get("notes") or "",
            "tags": [label],
        },
        "trials": [
            {
                "trial_id": "capture",
                "repeat_index": 1,
                "ground_truth": {"label": label},
                "packet_path": packet_path.name,
            }
        ],
        "extra": {"legacy_metadata": metadata},
    }


def load_normalized_runs(
    root: Path,
    *,
    experiment_name: str | None = None,
    legacy_allowed_labels: Sequence[str] | None = None,
    legacy_schema_version: int | str | None = None,
) -> list[NormalizedRun]:
    """Load canonical runs and parameterized legacy labeled-run adapters."""

    if not root.exists():
        raise DatasetValidationError(f"Dataset root does not exist: {root}")

    candidate_dirs: dict[Path, Path] = {}
    for manifest_path in sorted(root.rglob("manifest.json")):
        candidate_dirs[manifest_path.parent] = manifest_path
    for meta_path in sorted(root.rglob("metadata.json")):
        candidate_dirs.setdefault(meta_path.parent, meta_path)
    if not candidate_dirs:
        raise DatasetValidationError(f"No manifest.json or metadata.json files found under: {root}")

    runs: list[NormalizedRun] = []
    for run_dir, marker_path in sorted(candidate_dirs.items()):
        if marker_path.name == "manifest.json":
            manifest = _read_json(marker_path)
            validate_canonical_manifest(manifest)
        else:
            manifest = _load_legacy_labeled_manifest(
                marker_path,
                expected_experiment=experiment_name,
                allowed_labels=legacy_allowed_labels,
                expected_schema_version=legacy_schema_version,
            )

        experiment = manifest.get("experiment", {})
        experiment_id = experiment.get("experiment_id")
        if experiment_name and experiment_id != experiment_name:
            continue

        packet_files = _packet_paths_from_manifest(run_dir, manifest)
        if not packet_files:
            raise DatasetValidationError(f"No packet files found for run: {run_dir}")

        trial_entries = manifest.get("trials", [])
        trials: list[NormalizedTrialCapture] = []
        for index, packet_path in enumerate(packet_files):
            trial_meta = (
                trial_entries[index]
                if index < len(trial_entries) and isinstance(trial_entries[index], dict)
                else {"trial_id": f"trial_{index + 1:03d}"}
            )
            records = list(iter_packet_rows(packet_path))
            if not records:
                raise DatasetValidationError(f"No packet records found in {packet_path}")
            trials.append(
                NormalizedTrialCapture(
                    trial_id=str(trial_meta.get("trial_id", f"trial_{index + 1:03d}")),
                    packet_path=packet_path,
                    metadata=dict(trial_meta),
                    records=records,
                )
            )

        runs.append(
            NormalizedRun(
                run_dir=run_dir,
                manifest=manifest,
                packet_files=packet_files,
                trials=trials,
            )
        )

    if experiment_name and not runs:
        raise DatasetValidationError(
            f"No runs found for experiment '{experiment_name}' under: {root}"
        )
    return runs


def load_labeled_runs(
    dataset_root: Path,
    *,
    experiment_name: str | None = None,
    labels: Sequence[str] | None = None,
    schema_version: int | str | None = None,
) -> list[RunCapture]:
    """Load labeled CSI runs without deriving semantics from path names."""

    normalized_runs = load_normalized_runs(
        dataset_root,
        experiment_name=experiment_name,
        legacy_allowed_labels=labels,
        legacy_schema_version=schema_version,
    )
    runs: list[RunCapture] = []
    for normalized in normalized_runs:
        manifest = normalized.manifest
        legacy_metadata = manifest.get("extra", {}).get("legacy_metadata")
        if isinstance(legacy_metadata, dict):
            metadata = dict(legacy_metadata)
        else:
            scenario = manifest.get("scenario", {})
            subject = manifest.get("subject", {})
            provenance = manifest.get("provenance", {})
            extra = manifest.get("extra", {})
            environment_profile = extra.get("environment_profile")
            source_experiment_name = experiment_name or str(
                manifest.get("experiment", {}).get("experiment_id", "")
            )
            metadata = {
                "schema_version": schema_version if schema_version is not None else manifest["schema_version"],
                "experiment_name": source_experiment_name,
                "label": scenario.get("scenario_id"),
                "run_id": manifest.get("run_id"),
                "subject_id": subject.get("subject_id"),
                "environment_id": scenario.get("room_id"),
                "target_profile": provenance.get("target_profile_id"),
                "environment_profile": environment_profile,
                "device": extra.get("device") or (environment_profile or {}).get("board") or "unknown",
                "chip": extra.get("chip") or (environment_profile or {}).get("chip"),
                "serial_dev": provenance.get("device_path") or "unknown",
                "serial_realpath": provenance.get("device_realpath"),
                "start_time": manifest.get("created_at_utc"),
                "end_time": extra.get("ended_at_utc", manifest.get("created_at_utc")),
                "sampling_params": manifest.get("capture", {}),
                "notes": provenance.get("notes"),
                "records_captured": extra.get("records_captured"),
            }
        validate_run_metadata(
            metadata,
            expected_experiment=experiment_name,
            allowed_labels=labels,
            expected_schema_version=schema_version,
        )
        runs.append(
            RunCapture(
                run_dir=normalized.run_dir,
                metadata=metadata,
                frames=[record for trial in normalized.trials for record in trial.records],
            )
        )
    return runs


def load_labeled_run_dirs(
    run_dirs: Sequence[Path],
    *,
    experiment_name: str | None = None,
    labels: Sequence[str] | None = None,
    schema_version: int | str | None = None,
) -> list[RunCapture]:
    """Load an explicit caller-selected list of typed experiment run directories."""

    if not run_dirs:
        raise ValueError("run_dirs must not be empty")
    captures: list[RunCapture] = []
    seen: set[Path] = set()
    for supplied_dir in run_dirs:
        run_dir = Path(supplied_dir)
        if not run_dir.is_dir():
            raise DatasetValidationError(f"Run directory does not exist: {run_dir}")
        selected = load_labeled_runs(
            run_dir,
            experiment_name=experiment_name,
            labels=labels,
            schema_version=schema_version,
        )
        if len(selected) != 1:
            raise DatasetValidationError(
                f"Selected typed run must contain exactly one labeled capture: {run_dir} "
                f"(found {len(selected)})"
            )
        capture = selected[0]
        identity = run_dir.resolve()
        if identity in seen:
            raise DatasetValidationError(f"Run directory selected more than once: {identity}")
        seen.add(identity)
        metadata = dict(capture.metadata)
        metadata["selected_run_id"] = run_dir.name
        captures.append(
            RunCapture(
                run_dir=capture.run_dir,
                metadata=metadata,
                frames=capture.frames,
            )
        )
    return captures
