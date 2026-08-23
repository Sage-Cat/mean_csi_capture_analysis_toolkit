from __future__ import annotations

import argparse
import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from csi_capture.capture import (
    capture_stream,
    serial_lines,
)
from csi_capture.core.device import (
    DeviceAccessError,
    ResolvedDevice,
    format_device_banner,
    resolve_serial_device,
    validate_serial_device_access,
)
from csi_capture.core.environment import (
    DEFAULT_ENVIRONMENT_PROFILE_ID,
    EnvironmentProfile,
    EnvironmentProfileError,
    format_environment_banner,
    resolve_environment_profile,
)
from csi_capture.core.domain import (
    AcquisitionBlock,
    ExperimentDefinition,
    Geometry,
    GroundTruth,
    RunManifest,
    RunProvenance,
    ScenarioRef,
    TrialDefinition,
)
from csi_capture.core.layout import LAYOUT_LEGACY_DISTANCE_ANGLE_V1, build_run_layout


SUPPORTED_EXPERIMENT_TYPES = {"distance", "angle"}
SUPPORTED_OUTPUT_FORMATS = {"jsonl", "csv"}


class ExperimentConfigError(ValueError):
    """Raised when an experiment config is invalid."""


@dataclass(frozen=True)
class DeviceConfig:
    path: str
    baud: int
    timeout_s: float
    reconnect_on_error: bool
    reconnect_delay_s: float


@dataclass(frozen=True)
class CaptureConfig:
    output_format: str
    packets_per_repeat: Optional[int]
    duration_s: Optional[float]
    inter_trial_pause_s: float
    wait_for_enter_between_trials: bool


@dataclass(frozen=True)
class TrialSpec:
    trial_id: str
    repeat_index: int
    ground_truth: dict[str, Any]


@dataclass(frozen=True)
class ExperimentConfig:
    experiment_type: str
    exp_id: str
    run_ids: list[str]
    output_root: Path
    target_profile: EnvironmentProfile
    scenario_tags: list[str]
    environment: dict[str, str]
    device: DeviceConfig
    capture: CaptureConfig
    trials: list[TrialSpec]
    angle_array_config: Optional[dict[str, Any]]
    angle_geometry: Optional[dict[str, Any]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def _normalize_string_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        out: list[str] = []
        for idx, item in enumerate(value):
            if not isinstance(item, str):
                raise ExperimentConfigError(f"{field_name}[{idx}] must be a string.")
            text = item.strip()
            if not text:
                continue
            out.append(text)
        return out
    raise ExperimentConfigError(f"{field_name} must be a string or list of strings.")


def _require_dict(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ExperimentConfigError(f"{field_name} must be an object.")
    return value


def _require_positive_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool):
        raise ExperimentConfigError(f"{field_name} must be a positive integer.")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ExperimentConfigError(f"{field_name} must be a positive integer.") from exc
    if parsed <= 0:
        raise ExperimentConfigError(f"{field_name} must be > 0.")
    return parsed


def _require_positive_float(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ExperimentConfigError(f"{field_name} must be a positive number.") from exc
    if parsed <= 0.0:
        raise ExperimentConfigError(f"{field_name} must be > 0.")
    return parsed


def _require_non_negative_float(value: Any, field_name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ExperimentConfigError(f"{field_name} must be a non-negative number.") from exc
    if parsed < 0.0:
        raise ExperimentConfigError(f"{field_name} must be >= 0.")
    return parsed


def _require_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ExperimentConfigError(f"{field_name} must be numeric.") from exc


def _sanitize_token(value: float) -> str:
    text = f"{value:g}"
    text = text.replace("-", "neg").replace(".", "p")
    return text


def _normalize_run_ids(run_id: str, run_ids_raw: Any) -> list[str]:
    if run_ids_raw is None:
        return [run_id]
    if not isinstance(run_ids_raw, list):
        raise ExperimentConfigError("run_ids must be a non-empty list of strings.")

    normalized: list[str] = []
    seen: set[str] = set()
    for idx, item in enumerate(run_ids_raw):
        if not isinstance(item, str):
            raise ExperimentConfigError(f"run_ids[{idx}] must be a string.")
        token = item.strip()
        if not token:
            raise ExperimentConfigError(f"run_ids[{idx}] cannot be empty.")
        if token in seen:
            continue
        seen.add(token)
        normalized.append(token)

    if not normalized:
        raise ExperimentConfigError("run_ids must contain at least one non-empty id.")
    return normalized


def _split_cli_values(values: Optional[list[str]]) -> list[str]:
    if not values:
        return []

    tokens: list[str] = []
    for value in values:
        if value is None:
            continue
        for chunk in str(value).split(","):
            token = chunk.strip()
            if token:
                tokens.append(token)
    return tokens


def build_angle_cli_config(args: argparse.Namespace) -> dict[str, Any]:
    target_profile = resolve_environment_profile(getattr(args, "target_profile", None))
    exp_id = str(args.exp_id or "").strip()
    if not exp_id:
        raise ExperimentConfigError(
            "angle CLI mode requires --exp-id when --config is not provided."
        )

    angle_tokens = _split_cli_values(args.angles)
    if not angle_tokens:
        raise ExperimentConfigError(
            "angle CLI mode requires --angles when --config is not provided."
        )
    angles = [
        _require_float(token, f"--angles[{idx}]") for idx, token in enumerate(angle_tokens, start=1)
    ]

    run_ids_tokens = _split_cli_values(args.run_ids)
    run_id = str(args.run_id or "").strip()
    if args.runs is not None and (run_ids_tokens or run_id):
        raise ExperimentConfigError("Use only one of --runs, --run-id, or --run-ids.")
    if run_ids_tokens and run_id:
        raise ExperimentConfigError("Use either --run-id or --run-ids, not both.")

    if args.runs is not None:
        run_count = _require_positive_int(args.runs, "--runs")
        run_ids = [f"{idx:03d}" for idx in range(1, run_count + 1)]
    elif run_ids_tokens:
        run_ids = _normalize_run_ids(run_ids_tokens[0], run_ids_tokens)
    elif run_id:
        run_ids = [run_id]
    else:
        run_ids = ["001"]

    packets_per_repeat = args.packets_per_repeat
    duration_s = args.duration_s
    if packets_per_repeat is None and duration_s is None:
        raise ExperimentConfigError(
            "angle CLI mode requires --packets-per-repeat or --duration-s."
        )

    device_path = (
        str(args.device).strip() if args.device is not None else target_profile.default_serial_device
    )
    if not device_path:
        device_path = target_profile.default_serial_device

    return {
        "experiment_type": "angle",
        "target_profile": target_profile.profile_id,
        "exp_id": exp_id,
        "run_id": run_ids[0],
        "run_ids": run_ids,
        "output_root": str(args.output_root).strip(),
        "scenario_tags": _split_cli_values(args.scenario_tags),
        "environment": {
            "room_id": str(args.room_id or "").strip(),
            "notes": str(args.notes or "").strip(),
        },
        "device": {
            "path": device_path,
            "baud": args.baud if args.baud is not None else target_profile.default_baud,
            "timeout_s": args.timeout_s,
            "reconnect_on_error": bool(args.reconnect_on_error),
            "reconnect_delay_s": args.reconnect_delay_s,
        },
        "capture": {
            "output_format": args.output_format,
            "packets_per_repeat": packets_per_repeat,
            "duration_s": duration_s,
            "inter_trial_pause_s": args.inter_trial_pause_s,
            "wait_for_enter_between_trials": bool(args.wait_enter),
        },
        "angle": {
            "angles": angles,
            "repeats_per_angle": args.repeats_per_angle,
            "array_config": {
                "num_antennas": args.num_antennas,
                "antenna_spacing_m": args.antenna_spacing_m,
            },
            "geometry": {
                "orientation_reference": str(args.orientation_reference or "").strip(),
                "measurement_positions": str(args.measurement_positions or "").strip(),
            },
        },
    }


def _build_distance_trials(distance_cfg: dict[str, Any]) -> list[TrialSpec]:
    distances = distance_cfg.get("distances_m")
    if not isinstance(distances, list) or not distances:
        raise ExperimentConfigError("distance.distances_m must be a non-empty list.")
    repeats = _require_positive_int(
        distance_cfg.get("repeats_per_distance", 1), "distance.repeats_per_distance"
    )

    trials: list[TrialSpec] = []
    for distance in distances:
        distance_m = _require_positive_float(distance, "distance.distances_m[]")
        for rep in range(1, repeats + 1):
            trial_id = f"distance_{_sanitize_token(distance_m)}m_rep_{rep:03d}"
            trials.append(
                TrialSpec(
                    trial_id=trial_id,
                    repeat_index=rep,
                    ground_truth={"distance_m": distance_m},
                )
            )
    return trials


def _build_angle_trials(angle_cfg: dict[str, Any]) -> tuple[list[TrialSpec], dict[str, Any], dict[str, Any]]:
    angles = angle_cfg.get("angles")
    if not isinstance(angles, list) or not angles:
        raise ExperimentConfigError("angle.angles must be a non-empty list.")
    repeats = _require_positive_int(angle_cfg.get("repeats_per_angle"), "angle.repeats_per_angle")

    array_cfg = _require_dict(angle_cfg.get("array_config"), "angle.array_config")
    num_antennas = _require_positive_int(array_cfg.get("num_antennas"), "angle.array_config.num_antennas")
    antenna_spacing = array_cfg.get("antenna_spacing_m")
    if antenna_spacing is not None:
        antenna_spacing = _require_positive_float(
            antenna_spacing, "angle.array_config.antenna_spacing_m"
        )
    normalized_array_cfg = {
        "num_antennas": num_antennas,
        "antenna_spacing_m": antenna_spacing,
    }

    geometry_cfg = _require_dict(angle_cfg.get("geometry"), "angle.geometry")
    orientation_reference = geometry_cfg.get("orientation_reference")
    if not isinstance(orientation_reference, str) or not orientation_reference.strip():
        raise ExperimentConfigError("angle.geometry.orientation_reference must be a non-empty string.")
    measurement_positions = geometry_cfg.get("measurement_positions")
    if not isinstance(measurement_positions, str) or not measurement_positions.strip():
        raise ExperimentConfigError("angle.geometry.measurement_positions must be a non-empty string.")
    normalized_geometry_cfg = {
        "orientation_reference": orientation_reference.strip(),
        "measurement_positions": measurement_positions.strip(),
    }

    trials: list[TrialSpec] = []
    for angle in angles:
        angle_deg = _require_float(angle, "angle.angles[]")
        for rep in range(1, repeats + 1):
            trial_id = f"angle_{_sanitize_token(angle_deg)}deg_rep_{rep:03d}"
            trials.append(
                TrialSpec(
                    trial_id=trial_id,
                    repeat_index=rep,
                    ground_truth={"angle_deg": angle_deg},
                )
            )

    return trials, normalized_array_cfg, normalized_geometry_cfg


def _normalize_config(raw: dict[str, Any]) -> ExperimentConfig:
    experiment_type = str(raw.get("experiment_type", "")).strip().lower()
    if experiment_type not in SUPPORTED_EXPERIMENT_TYPES:
        raise ExperimentConfigError(
            f"experiment_type must be one of {sorted(SUPPORTED_EXPERIMENT_TYPES)}."
        )

    target_profile_id = str(raw.get("target_profile", DEFAULT_ENVIRONMENT_PROFILE_ID)).strip()
    if not target_profile_id:
        target_profile_id = DEFAULT_ENVIRONMENT_PROFILE_ID
    try:
        target_profile = resolve_environment_profile(target_profile_id)
    except EnvironmentProfileError as exc:
        raise ExperimentConfigError(str(exc)) from exc

    exp_id = str(raw.get("exp_id", "")).strip()
    if not exp_id:
        raise ExperimentConfigError("exp_id is required.")
    run_id = str(raw.get("run_id", _default_run_id())).strip()
    if not run_id:
        raise ExperimentConfigError("run_id cannot be empty.")
    run_ids = _normalize_run_ids(run_id=run_id, run_ids_raw=raw.get("run_ids"))

    output_root_raw = str(raw.get("output_root", "")).strip()
    if not output_root_raw:
        raise ExperimentConfigError("output_root must be an explicit session-owned path")
    output_root = Path(output_root_raw)

    scenario_tags = _normalize_string_list(raw.get("scenario_tags"), "scenario_tags")
    environment_raw = raw.get("environment", {})
    environment_cfg = _require_dict(environment_raw, "environment")
    room_id = str(environment_cfg.get("room_id", "")).strip()
    notes = str(environment_cfg.get("notes", "")).strip()
    environment = {"room_id": room_id, "notes": notes}

    device_raw = raw.get("device", {})
    device_cfg = _require_dict(device_raw, "device")
    device_path = (
        str(device_cfg.get("path", target_profile.default_serial_device)).strip()
        or target_profile.default_serial_device
    )
    baud = _require_positive_int(device_cfg.get("baud", target_profile.default_baud), "device.baud")
    timeout_s = _require_positive_float(device_cfg.get("timeout_s", 1.0), "device.timeout_s")
    reconnect_on_error = bool(device_cfg.get("reconnect_on_error", False))
    reconnect_delay_s = _require_positive_float(
        device_cfg.get("reconnect_delay_s", 1.0), "device.reconnect_delay_s"
    )

    capture_raw = raw.get("capture", {})
    capture_cfg = _require_dict(capture_raw, "capture")
    output_format = str(capture_cfg.get("output_format", "jsonl")).strip().lower()
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ExperimentConfigError(
            f"capture.output_format must be one of {sorted(SUPPORTED_OUTPUT_FORMATS)}."
        )
    packets_per_repeat = capture_cfg.get("packets_per_repeat")
    duration_s = capture_cfg.get("duration_s")
    if packets_per_repeat is None and duration_s is None:
        raise ExperimentConfigError(
            "capture requires packets_per_repeat or duration_s (one is required)."
        )
    if packets_per_repeat is not None:
        packets_per_repeat = _require_positive_int(
            packets_per_repeat, "capture.packets_per_repeat"
        )
    if duration_s is not None:
        duration_s = _require_positive_float(duration_s, "capture.duration_s")
    if packets_per_repeat is not None and duration_s is not None:
        raise ExperimentConfigError(
            "capture.packets_per_repeat and capture.duration_s are mutually exclusive."
        )
    inter_trial_pause_s = _require_non_negative_float(
        capture_cfg.get("inter_trial_pause_s", 0.0), "capture.inter_trial_pause_s"
    )
    wait_for_enter_between_trials = capture_cfg.get("wait_for_enter_between_trials", False)
    if not isinstance(wait_for_enter_between_trials, bool):
        raise ExperimentConfigError("capture.wait_for_enter_between_trials must be boolean.")

    angle_array_config: Optional[dict[str, Any]] = None
    angle_geometry: Optional[dict[str, Any]] = None
    if experiment_type == "distance":
        distance_raw = raw.get("distance")
        distance_cfg = _require_dict(distance_raw, "distance")
        trials = _build_distance_trials(distance_cfg)
    else:
        angle_raw = raw.get("angle")
        angle_cfg = _require_dict(angle_raw, "angle")
        trials, angle_array_config, angle_geometry = _build_angle_trials(angle_cfg)

    return ExperimentConfig(
        experiment_type=experiment_type,
        exp_id=exp_id,
        run_ids=run_ids,
        output_root=output_root,
        target_profile=target_profile,
        scenario_tags=scenario_tags,
        environment=environment,
        device=DeviceConfig(
            path=device_path,
            baud=baud,
            timeout_s=timeout_s,
            reconnect_on_error=reconnect_on_error,
            reconnect_delay_s=reconnect_delay_s,
        ),
        capture=CaptureConfig(
            output_format=output_format,
            packets_per_repeat=packets_per_repeat,
            duration_s=duration_s,
            inter_trial_pause_s=inter_trial_pause_s,
            wait_for_enter_between_trials=wait_for_enter_between_trials,
        ),
        trials=trials,
        angle_array_config=angle_array_config,
        angle_geometry=angle_geometry,
    )


def load_experiment_config(config_path: Path) -> tuple[dict[str, Any], ExperimentConfig]:
    try:
        with config_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ExperimentConfigError(f"Config JSON parse error: {exc}") from exc
    if not isinstance(raw, dict):
        raise ExperimentConfigError("Top-level config must be a JSON object.")
    normalized = _normalize_config(raw)
    return raw, normalized


def _git_info(repo_root: Path) -> dict[str, Any]:
    def run_git(args: list[str]) -> tuple[int, str]:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_root,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        return proc.returncode, proc.stdout.strip()

    commit_code, commit_out = run_git(["rev-parse", "HEAD"])
    dirty_code, dirty_out = run_git(["status", "--porcelain"])
    commit = commit_out if commit_code == 0 and commit_out else "unknown"
    dirty = bool(dirty_out) if dirty_code == 0 else None
    return {"git_commit": commit, "git_dirty": dirty}


def _duration_limited_lines(lines: Iterable[str], duration_s: float) -> Iterator[str]:
    deadline = time.monotonic() + duration_s
    for line in lines:
        if time.monotonic() >= deadline:
            break
        yield line


def _resolve_runtime_device(config_device_path: str, device_override: Optional[str]) -> ResolvedDevice:
    requested = device_override if device_override is not None else config_device_path
    if requested.strip().lower() == "auto":
        return resolve_serial_device(cli_device=None)
    return resolve_serial_device(cli_device=requested)


def _config_to_dict(config: ExperimentConfig) -> dict[str, Any]:
    output: dict[str, Any] = {
        "experiment_type": config.experiment_type,
        "target_profile": config.target_profile.profile_id,
        "exp_id": config.exp_id,
        "run_ids": config.run_ids,
        "output_root": str(config.output_root),
        "scenario_tags": config.scenario_tags,
        "environment": config.environment,
        "device": {
            "path": config.device.path,
            "baud": config.device.baud,
            "timeout_s": config.device.timeout_s,
            "reconnect_on_error": config.device.reconnect_on_error,
            "reconnect_delay_s": config.device.reconnect_delay_s,
        },
        "capture": {
            "output_format": config.capture.output_format,
            "packets_per_repeat": config.capture.packets_per_repeat,
            "duration_s": config.capture.duration_s,
            "inter_trial_pause_s": config.capture.inter_trial_pause_s,
            "wait_for_enter_between_trials": config.capture.wait_for_enter_between_trials,
        },
    }
    if len(config.run_ids) == 1:
        output["run_id"] = config.run_ids[0]
    if config.experiment_type == "distance":
        distances = [trial.ground_truth["distance_m"] for trial in config.trials]
        output["distance"] = {
            "distances_m": sorted(set(distances), key=lambda v: (float(v), str(v))),
            "repeats_per_distance": max(
                trial.repeat_index for trial in config.trials if "distance_m" in trial.ground_truth
            ),
        }
    else:
        angles = [trial.ground_truth["angle_deg"] for trial in config.trials]
        output["angle"] = {
            "angles": sorted(set(angles), key=lambda v: (float(v), str(v))),
            "repeats_per_angle": max(
                trial.repeat_index for trial in config.trials if "angle_deg" in trial.ground_truth
            ),
            "array_config": config.angle_array_config,
            "geometry": config.angle_geometry,
        }
    return output


def _experiment_definition(config: ExperimentConfig) -> ExperimentDefinition:
    task_type = "regression" if config.experiment_type == "distance" else "localization"
    summary = (
        "Config-driven CSI/RSSI capture for distance estimation."
        if config.experiment_type == "distance"
        else "Config-driven CSI/RSSI capture for angular localization."
    )
    return ExperimentDefinition(
        experiment_id=config.experiment_type,
        display_name=config.experiment_type.replace("_", " ").title(),
        summary=summary,
        task_type=task_type,
        modalities=("csi", "rssi", "fusion"),
        layout_style=LAYOUT_LEGACY_DISTANCE_ANGLE_V1,
        target_profile_id=config.target_profile.profile_id,
        supports_capture=True,
        supports_preprocess=False,
        supports_train=False,
        supports_evaluate=False,
        supports_report=False,
        supports_inspect=False,
        config_modes=("capture",),
    )


def _scenario_ref(config: ExperimentConfig) -> ScenarioRef:
    scenario_id = config.scenario_tags[0] if config.scenario_tags else "unlabeled"
    return ScenarioRef(
        scenario_id=scenario_id,
        tags=tuple(config.scenario_tags),
        room_id=config.environment.get("room_id") or None,
        notes=config.environment.get("notes", ""),
    )


def _geometry(config: ExperimentConfig) -> Geometry | None:
    if config.experiment_type != "angle":
        return None
    angle_geometry = config.angle_geometry or {}
    angle_array = config.angle_array_config or {}
    return Geometry(
        coordinate_frame="angle_deg",
        antenna_array=dict(angle_array),
        measurement_positions=str(angle_geometry.get("measurement_positions") or ""),
        orientation_reference=str(angle_geometry.get("orientation_reference") or ""),
    )


def _trial_metadata(
    config: ExperimentConfig, trial: TrialSpec, run_id: str, device_path: str
) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "exp_id": config.exp_id,
        "dataset_id": config.exp_id,
        "experiment_id": config.experiment_type,
        "experiment_type": config.experiment_type,
        "target_profile": config.target_profile.profile_id,
        "run_id": run_id,
        "trial_id": trial.trial_id,
        "repeat_index": trial.repeat_index,
        "scenario_tags": config.scenario_tags,
        "device_path": device_path,
        "room_id": config.environment.get("room_id", ""),
        "environment_notes": config.environment.get("notes", ""),
    }
    if config.scenario_tags:
        metadata["scenario"] = config.scenario_tags[0]
    metadata.update(trial.ground_truth)
    if config.experiment_type == "angle" and config.angle_array_config is not None:
        metadata["array_config"] = config.angle_array_config
    return metadata


def _manifest_template(
    config: ExperimentConfig,
    run_id: str,
    runtime_device: ResolvedDevice,
    config_snapshot: dict[str, Any],
    repo_root: Path,
) -> dict[str, Any]:
    git_meta = _git_info(repo_root)
    trial_defs = tuple(
        TrialDefinition(
            trial_id=trial.trial_id,
            repeat_index=trial.repeat_index,
            ground_truth=GroundTruth(dict(trial.ground_truth)),
            scenario=_scenario_ref(config),
            acquisition=AcquisitionBlock(
                block_id=trial.trial_id,
                modality="csi_rssi",
                packet_budget=config.capture.packets_per_repeat,
                duration_s=config.capture.duration_s,
                output_format=config.capture.output_format,
                notes="Legacy distance/angle capture trial.",
            ),
        )
        for trial in config.trials
    )
    manifest = RunManifest(
        experiment=_experiment_definition(config),
        dataset_id=config.exp_id,
        run_id=run_id,
        status="running",
        created_at_utc=_utc_now_iso(),
        layout_style=LAYOUT_LEGACY_DISTANCE_ANGLE_V1,
        scenario=_scenario_ref(config),
        geometry=_geometry(config),
        trials=trial_defs,
        provenance=RunProvenance(
            git_commit=git_meta["git_commit"],
            git_dirty=git_meta["git_dirty"],
            target_profile_id=config.target_profile.profile_id,
            device_path=runtime_device.path,
            device_realpath=runtime_device.realpath,
            notes=config.environment.get("notes", ""),
            tags=tuple(config.scenario_tags),
        ),
        capture={
            "output_format": config.capture.output_format,
            "packets_per_repeat": config.capture.packets_per_repeat,
            "duration_s": config.capture.duration_s,
            "inter_trial_pause_s": config.capture.inter_trial_pause_s,
            "wait_for_enter_between_trials": config.capture.wait_for_enter_between_trials,
        },
        config_snapshot=config_snapshot,
        extra={
            "environment_profile": config.target_profile.to_dict(),
            "analysis_status": "not_run",
            "analysis_notes": "",
            "resolved_config": _config_to_dict(config),
        },
    ).to_dict()
    manifest.update(
        {
            "exp_id": config.exp_id,
            "experiment_type": config.experiment_type,
            "target_profile": config.target_profile.profile_id,
            "environment_profile": config.target_profile.to_dict(),
            "device": {
                "path": runtime_device.path,
                "realpath": runtime_device.realpath,
                "selection_source": runtime_device.source,
                "baud": config.device.baud,
                "timeout_s": config.device.timeout_s,
                "reconnect_on_error": config.device.reconnect_on_error,
                "reconnect_delay_s": config.device.reconnect_delay_s,
            },
            "scenario_tags": config.scenario_tags,
            "environment": config.environment,
            "angle": {
                "array_config": config.angle_array_config,
                "geometry": config.angle_geometry,
            }
            if config.experiment_type == "angle"
            else None,
            "analysis_status": "not_run",
            "analysis_notes": "",
            **git_meta,
            "resolved_config": _config_to_dict(config),
        }
    )
    return manifest


def _write_manifest(manifest_path: Path, manifest: dict[str, Any]) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")


def run_raw_config(
    raw_config: dict[str, Any],
    expected_type: Optional[str] = None,
    device_override: Optional[str] = None,
    target_profile_override: Optional[str] = None,
) -> int:
    if not isinstance(raw_config, dict):
        raise ExperimentConfigError("Top-level config must be a JSON object.")
    raw_config_runtime = dict(raw_config)
    if target_profile_override and target_profile_override.strip():
        raw_config_runtime["target_profile"] = target_profile_override.strip()
    config = _normalize_config(raw_config_runtime)
    if expected_type is not None and config.experiment_type != expected_type:
        raise ExperimentConfigError(
            f"Config experiment_type is '{config.experiment_type}', expected '{expected_type}'."
        )

    runtime_device = _resolve_runtime_device(config.device.path, device_override=device_override)
    validate_serial_device_access(runtime_device.path)
    print(format_environment_banner(config.target_profile))
    print(format_device_banner(runtime_device))

    repo_root = Path(__file__).resolve().parent.parent

    stream = serial_lines(
        port=runtime_device.path,
        baud=config.device.baud,
        timeout=config.device.timeout_s,
        reconnect_on_error=config.device.reconnect_on_error,
        reconnect_delay_s=config.device.reconnect_delay_s,
        yield_on_timeout=config.capture.duration_s is not None,
    )
    total_records = 0
    run_summaries: list[dict[str, Any]] = []
    active_manifest: Optional[dict[str, Any]] = None
    active_manifest_path: Optional[Path] = None

    try:
        run_count = len(config.run_ids)
        for run_index, run_id in enumerate(config.run_ids, start=1):
            run_layout = build_run_layout(
                root=config.output_root,
                experiment_id=config.experiment_type,
                dataset_id=config.exp_id,
                run_id=run_id,
                layout_style=LAYOUT_LEGACY_DISTANCE_ANGLE_V1,
            )
            run_dir = run_layout.run_dir
            run_dir.mkdir(parents=True, exist_ok=True)
            manifest_path = run_layout.manifest_path
            manifest = _manifest_template(
                config=config,
                run_id=run_id,
                runtime_device=runtime_device,
                config_snapshot=raw_config_runtime,
                repo_root=repo_root,
            )
            manifest["run_index"] = run_index
            manifest["run_count"] = run_count

            for idx, trial in enumerate(config.trials):
                paths = run_layout.trial_paths(trial.trial_id, output_format=config.capture.output_format)
                entry = manifest["trials"][idx]
                entry.update(
                    {
                        "output_file": str(paths.packet_path),
                        "status": "pending",
                        "records_captured": 0,
                    }
                )
            _write_manifest(manifest_path, manifest)
            active_manifest = manifest
            active_manifest_path = manifest_path

            print(
                f"Starting {config.experiment_type} experiment: exp_id={config.exp_id}, "
                f"run_id={run_id} ({run_index}/{run_count}), trials={len(config.trials)}, "
                f"device={runtime_device.path}"
            )

            run_total_records = 0
            for idx, trial in enumerate(config.trials):
                paths = run_layout.trial_paths(trial.trial_id, output_format=config.capture.output_format)
                trial_dir = paths.trial_dir
                trial_dir.mkdir(parents=True, exist_ok=True)
                out_file = paths.packet_path
                trial_entry = manifest["trials"][idx]
                trial_entry["status"] = "running"
                trial_entry["started_at_utc"] = _utc_now_iso()
                _write_manifest(manifest_path, manifest)

                metadata = _trial_metadata(
                    config=config, trial=trial, run_id=run_id, device_path=runtime_device.path
                )
                with out_file.open("w", encoding="utf-8", newline="") as out_handle:
                    if config.capture.packets_per_repeat is not None:
                        written = capture_stream(
                            lines=stream,
                            out=out_handle,
                            output_format=config.capture.output_format,
                            max_records=config.capture.packets_per_repeat,
                            metadata=metadata,
                        )
                    else:
                        assert config.capture.duration_s is not None
                        written = capture_stream(
                            lines=_duration_limited_lines(stream, config.capture.duration_s),
                            out=out_handle,
                            output_format=config.capture.output_format,
                            max_records=None,
                            metadata=metadata,
                        )

                trial_entry["status"] = "completed"
                trial_entry["records_captured"] = written
                trial_entry["ended_at_utc"] = _utc_now_iso()
                run_total_records += written
                total_records += written
                _write_manifest(manifest_path, manifest)
                print(
                    f"Completed run {run_id} trial {idx + 1}/{len(config.trials)}: "
                    f"{trial.trial_id} -> {written} records"
                )
                if idx < len(config.trials) - 1:
                    if (
                        config.experiment_type == "angle"
                        and config.capture.wait_for_enter_between_trials
                    ):
                        print(
                            "Paused after trial capture. Move receiver to the next angle mark, "
                            "then press Enter to continue."
                        )
                        try:
                            input()
                        except EOFError as exc:
                            raise RuntimeError(
                                "capture.wait_for_enter_between_trials=true requires interactive stdin."
                            ) from exc
                    elif config.capture.inter_trial_pause_s > 0.0:
                        pause_s = config.capture.inter_trial_pause_s
                        print(
                            f"Pause {pause_s:.1f}s before next trial. "
                            "Move receiver to the next angle mark now."
                        )
                        time.sleep(pause_s)

            manifest["status"] = "completed"
            manifest["ended_at_utc"] = _utc_now_iso()
            manifest["total_records"] = run_total_records
            _write_manifest(manifest_path, manifest)
            print(f"Run complete: run_id={run_id}, records={run_total_records}")
            print(f"Manifest: {manifest_path}")

            run_summaries.append(
                {"run_id": run_id, "records": run_total_records, "manifest_path": str(manifest_path)}
            )
            active_manifest = None
            active_manifest_path = None
    except KeyboardInterrupt:
        if active_manifest is not None and active_manifest_path is not None:
            active_manifest["status"] = "interrupted"
            active_manifest["ended_at_utc"] = _utc_now_iso()
            _write_manifest(active_manifest_path, active_manifest)
        raise

    print(
        f"Experiment complete. exp_id={config.exp_id}, runs={len(config.run_ids)}, "
        f"total_records={total_records}"
    )
    for summary in run_summaries:
        print(
            f"  run_id={summary['run_id']} records={summary['records']} "
            f"manifest={summary['manifest_path']}"
        )
    return 0


def run_config(
    config_path: Path,
    expected_type: Optional[str] = None,
    device_override: Optional[str] = None,
    target_profile_override: Optional[str] = None,
) -> int:
    raw_config, _ = load_experiment_config(config_path)
    return run_raw_config(
        raw_config,
        expected_type=expected_type,
        device_override=device_override,
        target_profile_override=target_profile_override,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run config-driven CSI experiments (distance or angle)."
    )
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Run an experiment from a JSON config.")
    run_parser.add_argument("--config", required=True, help="Path to experiment config JSON.")
    run_parser.add_argument(
        "--device",
        default=None,
        help="Serial device override. Use 'auto' to auto-detect (macOS/Linux).",
    )
    run_parser.add_argument(
        "--target-profile",
        default=None,
        help=f"Target environment profile id (default from config or {DEFAULT_ENVIRONMENT_PROFILE_ID}).",
    )

    distance_parser = sub.add_parser("distance", help="Run distance experiment config.")
    distance_parser.add_argument("--config", required=True, help="Path to experiment config JSON.")
    distance_parser.add_argument(
        "--device",
        default=None,
        help="Serial device override. Use 'auto' to auto-detect (macOS/Linux).",
    )
    distance_parser.add_argument(
        "--target-profile",
        default=None,
        help=f"Target environment profile id (default from config or {DEFAULT_ENVIRONMENT_PROFILE_ID}).",
    )

    angle_parser = sub.add_parser(
        "angle",
        help="Run angle experiment from JSON config or fully from CLI flags.",
    )
    angle_parser.add_argument(
        "--config",
        default=None,
        help="Path to experiment config JSON (optional when using CLI flags).",
    )
    angle_parser.add_argument(
        "--device",
        default=None,
        help="Serial device path (CLI mode) or config override. Use 'auto' to auto-detect.",
    )
    angle_parser.add_argument(
        "--target-profile",
        default=DEFAULT_ENVIRONMENT_PROFILE_ID,
        help=f"Target environment profile id (default: {DEFAULT_ENVIRONMENT_PROFILE_ID}).",
    )
    angle_parser.add_argument("--exp-id", default=None, help="Experiment id (required in CLI mode).")
    angle_parser.add_argument(
        "--run-id",
        default=None,
        help="Single run id for CLI mode (default: 001 if run flags are omitted).",
    )
    angle_parser.add_argument(
        "--run-ids",
        nargs="+",
        default=None,
        help="Multiple run ids in CLI mode, space/comma separated (e.g. 001 002).",
    )
    angle_parser.add_argument(
        "--runs",
        type=int,
        default=None,
        help="Generate run ids 001..N in CLI mode (e.g. --runs 2).",
    )
    angle_parser.add_argument(
        "--angles",
        nargs="+",
        default=None,
        help="Angle degrees for CLI mode, space/comma separated (e.g. 0 45 90).",
    )
    angle_parser.add_argument(
        "--repeats-per-angle",
        type=int,
        default=1,
        help="Repeats per angle in CLI mode (default: 1).",
    )
    capture_budget_group = angle_parser.add_mutually_exclusive_group(required=False)
    capture_budget_group.add_argument(
        "--packets-per-repeat",
        type=int,
        default=None,
        help="Packets to capture per trial in CLI mode.",
    )
    capture_budget_group.add_argument(
        "--duration-s",
        type=float,
        default=None,
        help="Capture duration per trial in seconds (CLI mode).",
    )
    angle_parser.add_argument(
        "--output-format",
        choices=sorted(SUPPORTED_OUTPUT_FORMATS),
        default="jsonl",
        help="Output format in CLI mode (default: jsonl).",
    )
    angle_parser.add_argument(
        "--inter-trial-pause-s",
        type=float,
        default=0.0,
        help="Pause between trials in seconds for CLI mode.",
    )
    angle_parser.add_argument(
        "--wait-enter",
        action="store_true",
        help="Pause between angle trials and wait for Enter to continue.",
    )
    angle_parser.add_argument(
        "--scenario-tags",
        nargs="+",
        default=None,
        help="Scenario tags in CLI mode, space/comma separated.",
    )
    angle_parser.add_argument(
        "--room-id",
        default="",
        help="Environment room id in CLI mode.",
    )
    angle_parser.add_argument(
        "--notes",
        default="",
        help="Environment notes in CLI mode.",
    )
    angle_parser.add_argument(
        "--num-antennas",
        type=int,
        default=1,
        help="Array metadata: number of antennas (CLI mode).",
    )
    angle_parser.add_argument(
        "--antenna-spacing-m",
        type=float,
        default=None,
        help="Array metadata: antenna spacing in meters (optional, CLI mode).",
    )
    angle_parser.add_argument(
        "--orientation-reference",
        default="0 deg points from receiver front toward AP",
        help="Geometry metadata for CLI mode.",
    )
    angle_parser.add_argument(
        "--measurement-positions",
        default="AP fixed in center, receiver moved between angle marks",
        help="Geometry metadata for CLI mode.",
    )
    angle_parser.add_argument(
        "--output-root",
        required=True,
        help="Session-owned output root directory in CLI mode.",
    )
    angle_parser.add_argument(
        "--baud",
        type=int,
        default=921600,
        help="Serial baud in CLI mode (default: 921600).",
    )
    angle_parser.add_argument(
        "--timeout-s",
        type=float,
        default=1.0,
        help="Serial timeout in CLI mode (default: 1.0).",
    )
    angle_parser.add_argument(
        "--reconnect-on-error",
        action="store_true",
        help="Enable serial reconnect on read errors in CLI mode.",
    )
    angle_parser.add_argument(
        "--reconnect-delay-s",
        type=float,
        default=1.0,
        help="Reconnect delay in seconds for CLI mode (default: 1.0).",
    )

    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        return 2

    try:
        if args.command == "run":
            config_path = Path(args.config)
            if not config_path.exists():
                print(f"Error: config file does not exist: {config_path}")
                return 2
            return run_config(
                config_path,
                device_override=args.device,
                target_profile_override=args.target_profile,
            )
        if args.command == "distance":
            config_path = Path(args.config)
            if not config_path.exists():
                print(f"Error: config file does not exist: {config_path}")
                return 2
            return run_config(
                config_path,
                expected_type="distance",
                device_override=args.device,
                target_profile_override=args.target_profile,
            )
        if args.command == "angle":
            if args.config:
                config_path = Path(args.config)
                if not config_path.exists():
                    print(f"Error: config file does not exist: {config_path}")
                    return 2
                return run_config(
                    config_path,
                    expected_type="angle",
                    device_override=args.device,
                    target_profile_override=args.target_profile,
                )
            raw_config = build_angle_cli_config(args)
            return run_raw_config(
                raw_config,
                expected_type="angle",
                device_override=None,
                target_profile_override=args.target_profile,
            )
        print(f"Error: unknown command {args.command}")
        return 2
    except ExperimentConfigError as err:
        print(f"Error: invalid config: {err}")
        return 2
    except EnvironmentProfileError as err:
        print(f"Error: invalid target profile: {err}")
        return 2
    except DeviceAccessError as err:
        print(f"Error: {err}")
        return 2
    except RuntimeError as err:
        print(f"Error: {err}")
        return 2
    except KeyboardInterrupt:
        print("Interrupted by user.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
