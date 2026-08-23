from __future__ import annotations

import argparse
import io
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from csi_capture.capture import capture_stream, serial_lines
from csi_capture.core.dataset import RunCapture, load_static_sign_runs
from csi_capture.core.device import (
    DeviceAccessError,
    format_device_banner,
    resolve_serial_device,
    validate_serial_device_access,
)
from csi_capture.core.domain import (
    AcquisitionBlock,
    ExperimentDefinition,
    GroundTruth,
    LabelSet,
    RunManifest,
    RunProvenance,
    ScenarioRef,
    SubjectRef,
    TrialDefinition,
)
from csi_capture.core.evaluation import classification_metrics, per_run_summary
from csi_capture.core.features import extract_window_features
from csi_capture.core.environment import (
    DEFAULT_ENVIRONMENT_PROFILE_ID,
    EnvironmentProfileError,
    format_environment_banner,
    resolve_environment_profile,
)
from csi_capture.core.layout import (
    DEFAULT_ARTIFACT_ROOT,
    LAYOUT_LEGACY_STATIC_SIGN_V1,
    build_run_layout,
)
from csi_capture.core.models import create_classifier, load_model_artifact, save_model_artifact
from csi_capture.experiments.registry import ExperimentPlugin

STATIC_SIGN_EXPERIMENT = "static_sign_v1"
STATIC_SIGN_LABELS = ("baseline", "hands_up")


class StaticSignError(RuntimeError):
    """Raised for static_sign_v1 pipeline failures."""


@dataclass(frozen=True)
class CaptureSummary:
    run_dir: Path
    run_id: str
    label: str
    records_captured: int


@dataclass(frozen=True)
class TrainSummary:
    model_path: Path
    metrics_path: Path
    metrics: dict[str, Any]


@dataclass(frozen=True)
class EvalSummary:
    report_path: Path
    report: dict[str, Any]


def parse_duration_s(text: str) -> float:
    value = text.strip().lower()
    if not value:
        raise ValueError("duration value is empty")
    if value.endswith("ms"):
        return float(value[:-2]) / 1000.0
    if value.endswith("s"):
        return float(value[:-1])
    if value.endswith("m"):
        return float(value[:-1]) * 60.0
    if value.endswith("h"):
        return float(value[:-1]) * 3600.0
    return float(value)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _duration_limited_lines(lines: Iterable[str], duration_s: float) -> Iterable[str]:
    deadline = time.monotonic() + duration_s
    for line in lines:
        if time.monotonic() >= deadline:
            break
        yield line


def _ensure_label(label: str) -> str:
    text = label.strip().lower()
    if text not in STATIC_SIGN_LABELS:
        raise StaticSignError(f"label must be one of {sorted(STATIC_SIGN_LABELS)}")
    return text


def _ensure_dataset_id(dataset_id: str | None) -> str:
    if dataset_id and dataset_id.strip():
        return dataset_id.strip()
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _experiment_definition() -> ExperimentDefinition:
    return ExperimentDefinition(
        experiment_id=STATIC_SIGN_EXPERIMENT,
        display_name="Static Gesture / Static Sign v1",
        summary="Binary hands-up vs baseline classifier built on ESP32 CSI/RSSI windows.",
        task_type="classification",
        modalities=("csi", "rssi", "fusion"),
        layout_style=LAYOUT_LEGACY_STATIC_SIGN_V1,
        target_profile_id=DEFAULT_ENVIRONMENT_PROFILE_ID,
        supports_capture=True,
        supports_preprocess=True,
        supports_train=True,
        supports_evaluate=True,
        supports_report=False,
        supports_inspect=False,
        config_modes=("capture", "train", "eval"),
        label_set=LabelSet(
            label_set_id="static_sign_binary_v1",
            task_type="classification",
            labels=STATIC_SIGN_LABELS,
            positive_label="hands_up",
        ),
    )


def _default_model_artifact_path(model_name: str) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return DEFAULT_ARTIFACT_ROOT / STATIC_SIGN_EXPERIMENT / stamp / f"{model_name}.pkl"


def dry_run_capture(
    *,
    device_path: str,
    baud: int,
    packets: int,
    timeout_s: float,
    max_wait_s: float,
) -> int:
    if packets <= 0:
        raise StaticSignError("dry-run packets must be > 0")
    if max_wait_s <= 0:
        raise StaticSignError("dry-run max_wait_s must be > 0")

    lines = serial_lines(
        port=device_path,
        baud=baud,
        timeout=timeout_s,
        reconnect_on_error=False,
        reconnect_delay_s=1.0,
        yield_on_timeout=True,
    )
    sink = io.StringIO()
    written = capture_stream(
        _duration_limited_lines(lines, max_wait_s),
        out=sink,
        output_format="jsonl",
        max_records=packets,
        metadata=None,
    )
    if written < packets:
        raise StaticSignError(
            f"dry-run timed out after {max_wait_s}s; expected {packets} packets, got {written}"
        )
    return written


def capture_static_sign_runs(
    *,
    dataset_root: Path,
    dataset_id: str | None,
    label: str,
    runs: int,
    duration_s: float | None,
    packets_per_run: int | None,
    device_path: str,
    device_realpath: str,
    baud: int,
    timeout_s: float,
    subject_id: str | None,
    environment_id: str | None,
    notes: str | None,
    target_profile_id: str | None = None,
) -> list[CaptureSummary]:
    label_norm = _ensure_label(label)
    dataset_id_norm = _ensure_dataset_id(dataset_id)
    resolved_profile_id = (target_profile_id or DEFAULT_ENVIRONMENT_PROFILE_ID).strip()
    if not resolved_profile_id:
        resolved_profile_id = DEFAULT_ENVIRONMENT_PROFILE_ID
    try:
        target_profile = resolve_environment_profile(resolved_profile_id)
    except EnvironmentProfileError as exc:
        raise StaticSignError(str(exc)) from exc

    if runs <= 0:
        raise StaticSignError("runs must be > 0")
    if duration_s is None and packets_per_run is None:
        raise StaticSignError("Provide duration_s or packets_per_run")
    if duration_s is not None and duration_s <= 0:
        raise StaticSignError("duration_s must be > 0")
    if packets_per_run is not None and packets_per_run <= 0:
        raise StaticSignError("packets_per_run must be > 0")

    summaries: list[CaptureSummary] = []
    definition = _experiment_definition()
    scenario = ScenarioRef(
        scenario_id=label_norm,
        tags=(label_norm,),
        room_id=environment_id,
        notes=notes or "",
    )
    subject = SubjectRef(subject_id=subject_id)

    for run_idx in range(1, runs + 1):
        run_id = f"{_new_run_id()}_{run_idx:03d}"
        run_layout = build_run_layout(
            root=dataset_root,
            experiment_id=STATIC_SIGN_EXPERIMENT,
            dataset_id=dataset_id_norm,
            run_id=run_id,
            layout_style=LAYOUT_LEGACY_STATIC_SIGN_V1,
            label=label_norm,
        )
        run_dir = run_layout.run_dir
        run_dir.mkdir(parents=True, exist_ok=False)

        start_time = _utc_now_iso()
        frames_path = run_layout.trial_paths("capture", output_format="jsonl").packet_path

        packet_metadata = {
            "experiment_name": STATIC_SIGN_EXPERIMENT,
            "experiment_type": STATIC_SIGN_EXPERIMENT,
            "dataset_id": dataset_id_norm,
            "label": label_norm,
            "run_id": run_id,
            "trial_id": "capture",
            "target_profile": target_profile.profile_id,
            "subject_id": subject_id,
            "environment_id": environment_id,
            "scenario_tags": [label_norm],
        }
        packet_metadata = {k: v for k, v in packet_metadata.items() if v is not None}

        lines = serial_lines(
            port=device_path,
            baud=baud,
            timeout=timeout_s,
            reconnect_on_error=False,
            reconnect_delay_s=1.0,
            yield_on_timeout=duration_s is not None,
        )

        with frames_path.open("w", encoding="utf-8") as handle:
            if duration_s is not None:
                written = capture_stream(
                    _duration_limited_lines(lines, duration_s),
                    out=handle,
                    output_format="jsonl",
                    max_records=None,
                    metadata=packet_metadata,
                )
            else:
                written = capture_stream(
                    lines,
                    out=handle,
                    output_format="jsonl",
                    max_records=packets_per_run,
                    metadata=packet_metadata,
                )

        end_time = _utc_now_iso()

        metadata = {
            "schema_version": 1,
            "experiment_name": STATIC_SIGN_EXPERIMENT,
            "label": label_norm,
            "run_id": run_id,
            "subject_id": subject_id,
            "environment_id": environment_id,
            "target_profile": target_profile.profile_id,
            "environment_profile": target_profile.to_dict(),
            "device": target_profile.board,
            "chip": target_profile.chip,
            "serial_dev": device_path,
            "serial_realpath": device_realpath,
            "start_time": start_time,
            "end_time": end_time,
            "sampling_params": {
                "baud": baud,
                "timeout_s": timeout_s,
                "duration_s": duration_s,
                "packets_per_run": packets_per_run,
            },
            "notes": notes,
            "records_captured": written,
        }
        (run_dir / "metadata.json").write_text(
            json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        trial = TrialDefinition(
            trial_id="capture",
            repeat_index=1,
            ground_truth=GroundTruth({"label": label_norm}),
            scenario=scenario,
            subject=subject,
            acquisition=AcquisitionBlock(
                block_id="capture",
                modality="csi_rssi",
                packet_budget=packets_per_run,
                duration_s=duration_s,
                output_format="jsonl",
                notes="Legacy static_sign_v1 single-trial capture block.",
            ),
            labels=definition.label_set,
            notes=notes or "",
        )
        manifest = RunManifest(
            experiment=definition,
            dataset_id=dataset_id_norm,
            run_id=run_id,
            status="completed",
            created_at_utc=start_time,
            layout_style=LAYOUT_LEGACY_STATIC_SIGN_V1,
            scenario=scenario,
            subject=subject,
            trials=(trial,),
            provenance=RunProvenance(
                target_profile_id=target_profile.profile_id,
                device_path=device_path,
                device_realpath=device_realpath,
                notes=notes or "",
                tags=(label_norm,),
            ),
            capture={
                "output_format": "jsonl",
                "duration_s": duration_s,
                "packets_per_run": packets_per_run,
                "baud": baud,
                "timeout_s": timeout_s,
            },
            config_snapshot={
                "dataset_root": str(dataset_root),
                "dataset_id": dataset_id_norm,
                "label": label_norm,
                "runs": runs,
            },
            extra={
                "records_captured": written,
                "ended_at_utc": end_time,
                "metadata_path": str(run_dir / "metadata.json"),
                "packet_path": str(frames_path),
                "environment_profile": target_profile.to_dict(),
                "device": target_profile.board,
                "chip": target_profile.chip,
            },
        )
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        summaries.append(
            CaptureSummary(
                run_dir=run_dir,
                run_id=run_id,
                label=label_norm,
                records_captured=written,
            )
        )

    return summaries


def build_feature_table(
    runs: list[RunCapture],
    *,
    window_s: float,
    overlap: float,
) -> pd.DataFrame:
    if window_s <= 0:
        raise StaticSignError("window_s must be > 0")

    rows: list[dict[str, Any]] = []
    window_ms = int(round(window_s * 1000.0))
    for run in runs:
        run_id = str(run.metadata["run_id"])
        label = str(run.metadata["label"])
        run_features = extract_window_features(
            run.frames,
            run_id=run_id,
            label=label,
            window_ms=window_ms,
            overlap=overlap,
        )
        for feat in run_features:
            rows.append(
                {
                    "run_id": feat.run_id,
                    "label": feat.label,
                    "window_index": feat.window_index,
                    "window_start_ms": feat.window_start_ms,
                    "window_end_ms": feat.window_end_ms,
                    "frame_count": feat.frame_count,
                    "mean_amp": feat.mean_amp,
                    "var_amp": feat.var_amp,
                    "rms_amp": feat.rms_amp,
                    "entropy_amp": feat.entropy_amp,
                }
            )

    if not rows:
        raise StaticSignError("No feature rows extracted from dataset")

    frame = pd.DataFrame(rows)
    frame = frame.sort_values(["run_id", "window_index"]).reset_index(drop=True)
    return frame


def _group_split(
    frame: pd.DataFrame,
    *,
    test_size: float,
    random_seed: int,
) -> tuple[np.ndarray, np.ndarray]:
    from sklearn.model_selection import GroupShuffleSplit

    groups = frame["run_id"].astype(str)
    unique_groups = groups.nunique()

    if unique_groups < 2:
        idx = np.arange(len(frame))
        return idx, idx

    splitter = GroupShuffleSplit(n_splits=1, test_size=test_size, random_state=random_seed)
    train_idx, test_idx = next(splitter.split(frame, groups=groups))
    return train_idx, test_idx


def _frame_to_xy(frame: pd.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    x = frame[["mean_amp", "var_amp", "rms_amp", "entropy_amp"]].to_numpy(dtype=np.float32)
    y = frame["label"].astype(str).to_numpy()
    run_ids = frame["run_id"].astype(str).to_numpy()
    return x, y, run_ids


def train_static_sign_model(
    *,
    dataset_path: Path,
    model_name: str,
    window_s: float,
    overlap: float,
    test_size: float,
    random_seed: int,
    model_path: Path,
) -> TrainSummary:
    runs = load_static_sign_runs(dataset_path)
    frame = build_feature_table(runs, window_s=window_s, overlap=overlap)
    x, y, run_ids = _frame_to_xy(frame)

    train_idx, test_idx = _group_split(frame, test_size=test_size, random_seed=random_seed)
    clf = create_classifier(model_name)
    clf.fit(x[train_idx], y[train_idx])

    pred_test = clf.predict(x[test_idx])
    metrics = classification_metrics(y[test_idx], pred_test, labels=STATIC_SIGN_LABELS)
    metrics["split"] = {
        "train_windows": int(train_idx.size),
        "test_windows": int(test_idx.size),
        "train_runs": sorted(set(run_ids[train_idx].tolist())),
        "test_runs": sorted(set(run_ids[test_idx].tolist())),
    }
    metrics["per_run_summary"] = per_run_summary(run_ids[test_idx], y[test_idx], pred_test)

    model_metadata = {
        "experiment_name": STATIC_SIGN_EXPERIMENT,
        "labels": list(STATIC_SIGN_LABELS),
        "model_name": model_name,
        "window_s": window_s,
        "overlap": overlap,
        "feature_columns": ["mean_amp", "var_amp", "rms_amp", "entropy_amp"],
    }
    save_model_artifact(model_path, clf, model_metadata)

    metrics_path = model_path.with_suffix(".metrics.json")
    metrics_path.write_text(json.dumps(metrics, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return TrainSummary(model_path=model_path, metrics_path=metrics_path, metrics=metrics)


def evaluate_static_sign_model(
    *,
    dataset_path: Path,
    model_path: Path,
    report_path: Path,
    window_s: float | None,
    overlap: float | None,
) -> EvalSummary:
    artifact = load_model_artifact(model_path)
    model = artifact["model"]
    metadata = artifact.get("metadata", {})

    window_s_eff = window_s if window_s is not None else float(metadata.get("window_s", 1.0))
    overlap_eff = overlap if overlap is not None else float(metadata.get("overlap", 0.5))

    runs = load_static_sign_runs(dataset_path)
    frame = build_feature_table(runs, window_s=window_s_eff, overlap=overlap_eff)
    x, y, run_ids = _frame_to_xy(frame)

    pred = model.predict(x)
    report = classification_metrics(y, pred, labels=STATIC_SIGN_LABELS)
    report["per_run_summary"] = per_run_summary(run_ids, y, pred)
    report["dataset_path"] = str(dataset_path)
    report["model_path"] = str(model_path)
    report["model_metadata"] = metadata
    report["window_s"] = window_s_eff
    report["overlap"] = overlap_eff

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return EvalSummary(report_path=report_path, report=report)


def validate_static_sign_config(mode: str, config: dict[str, Any]) -> None:
    if not isinstance(config, dict):
        raise StaticSignError("config must be a JSON object")
    if config.get("experiment") != STATIC_SIGN_EXPERIMENT:
        raise StaticSignError(f"config.experiment must be '{STATIC_SIGN_EXPERIMENT}'")
    target_profile = config.get("target_profile")
    if target_profile is not None:
        try:
            resolve_environment_profile(str(target_profile))
        except EnvironmentProfileError as exc:
            raise StaticSignError(str(exc)) from exc

    mode_norm = mode.strip().lower()
    if mode_norm == "capture":
        label = str(config.get("label", "")).strip().lower()
        if label not in STATIC_SIGN_LABELS:
            raise StaticSignError("capture config label must be baseline or hands_up")
        runs = config.get("runs")
        if not isinstance(runs, int) or runs <= 0:
            raise StaticSignError("capture config runs must be integer > 0")
        duration_s = config.get("duration_s")
        packets_per_run = config.get("packets_per_run")
        if duration_s is None and packets_per_run is None:
            raise StaticSignError("capture config requires duration_s or packets_per_run")
    elif mode_norm == "train":
        model = str(config.get("model", "")).strip()
        dataset = str(config.get("dataset", "")).strip()
        if not model:
            raise StaticSignError("train config model is required")
        if not dataset:
            raise StaticSignError("train config dataset is required")
    elif mode_norm == "eval":
        dataset = str(config.get("dataset", "")).strip()
        model = str(config.get("model_artifact", "")).strip()
        if not dataset:
            raise StaticSignError("eval config dataset is required")
        if not model:
            raise StaticSignError("eval config model_artifact is required")
    else:
        raise StaticSignError("mode must be one of: capture, train, eval")


def handle_capture(args: argparse.Namespace) -> int:
    try:
        target_profile = resolve_environment_profile(args.target_profile)
        print(format_environment_banner(target_profile))
        device = resolve_serial_device(cli_device=args.device, env=os.environ)
        print(format_device_banner(device))
        validate_serial_device_access(device.path)

        if args.dry_run_packets and args.dry_run_packets > 0:
            max_wait_s = parse_duration_s(args.dry_run_timeout)
            written = dry_run_capture(
                device_path=device.path,
                baud=args.baud,
                packets=args.dry_run_packets,
                timeout_s=args.timeout_s,
                max_wait_s=max_wait_s,
            )
            print(f"Dry-run success. Parsed packets: {written}")
            return 0

        if not args.label:
            print("Error: --label is required unless --dry-run-packets is used")
            return 2

        if args.packets_per_run is not None:
            duration_s = None
        else:
            duration_s = parse_duration_s(args.duration) if args.duration else None
        summaries = capture_static_sign_runs(
            dataset_root=Path(args.dataset_root),
            dataset_id=args.dataset_id,
            label=args.label,
            runs=args.runs,
            duration_s=duration_s,
            packets_per_run=args.packets_per_run,
            device_path=device.path,
            device_realpath=device.realpath,
            baud=args.baud,
            timeout_s=args.timeout_s,
            subject_id=args.subject_id,
            environment_id=args.environment_id,
            notes=args.notes,
            target_profile_id=target_profile.profile_id,
        )
    except (
        ValueError,
        DeviceAccessError,
        EnvironmentProfileError,
        RuntimeError,
        StaticSignError,
    ) as err:
        print(f"Error: {err}")
        return 2

    total = sum(item.records_captured for item in summaries)
    print(f"Capture complete. runs={len(summaries)} total_records={total}")
    for item in summaries:
        print(f"- run_id={item.run_id} label={item.label} records={item.records_captured} dir={item.run_dir}")
    return 0


def handle_train(args: argparse.Namespace) -> int:
    artifact = Path(args.artifact)
    try:
        window_s = parse_duration_s(args.window)
        summary = train_static_sign_model(
            dataset_path=Path(args.dataset),
            model_name=args.model,
            window_s=window_s,
            overlap=args.overlap,
            test_size=args.test_size,
            random_seed=args.seed,
            model_path=artifact,
        )
    except (ValueError, RuntimeError, StaticSignError) as err:
        print(f"Error: {err}")
        return 2

    print(f"Model artifact: {summary.model_path}")
    print(f"Metrics file: {summary.metrics_path}")
    print(
        "Train split metrics: "
        f"accuracy={summary.metrics['accuracy']:.4f} "
        f"precision={summary.metrics['precision']:.4f} "
        f"recall={summary.metrics['recall']:.4f} "
        f"f1={summary.metrics['f1']:.4f}"
    )
    return 0


def handle_eval(args: argparse.Namespace) -> int:
    try:
        window_s = parse_duration_s(args.window) if args.window else None
        summary = evaluate_static_sign_model(
            dataset_path=Path(args.dataset),
            model_path=Path(args.model),
            report_path=Path(args.report),
            window_s=window_s,
            overlap=args.overlap,
        )
    except (ValueError, RuntimeError, StaticSignError) as err:
        print(f"Error: {err}")
        return 2

    report = summary.report
    print(f"Eval report: {summary.report_path}")
    print(
        "Metrics: "
        f"accuracy={report['accuracy']:.4f} "
        f"precision={report['precision']:.4f} "
        f"recall={report['recall']:.4f} "
        f"f1={report['f1']:.4f}"
    )
    print(f"Confusion matrix ({report['labels']}): {report['confusion_matrix']}")
    return 0


STATIC_SIGN_PLUGIN = ExperimentPlugin(
    definition=_experiment_definition(),
    capture_handler=handle_capture,
    train_handler=handle_train,
    eval_handler=handle_eval,
    validate_handler=validate_static_sign_config,
    examples=(
        "tools/exp capture --experiment static_sign_v1 --dataset-root <session-owned-root> --output-root <session-owned-root> --label baseline --runs 5 --duration 20s",
        "tools/exp train --experiment static_sign_v1 --dataset <session-owned-dataset> --artifact <session-owned-model> --model svm_linear",
    ),
)
