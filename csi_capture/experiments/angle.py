from __future__ import annotations

from pathlib import Path
from typing import Any

from csi_capture.core.domain import ExperimentDefinition
from csi_capture.core.environment import DEFAULT_ENVIRONMENT_PROFILE_ID
from csi_capture.core.layout import LAYOUT_LEGACY_DISTANCE_ANGLE_V1
from csi_capture.experiment import (
    ExperimentConfigError,
    build_angle_cli_config,
    load_experiment_config,
    run_config,
    run_raw_config,
)
from csi_capture.experiments.registry import ExperimentPlugin

ANGLE_EXPERIMENT = "angle"


def validate_angle_config(mode: str, config: dict[str, Any]) -> None:
    if mode.strip().lower() != "capture":
        raise ExperimentConfigError("angle currently supports only capture config validation")
    if not isinstance(config, dict):
        raise ExperimentConfigError("config must be a JSON object")
    _, normalized = load_experiment_config_from_raw(config)
    if normalized.experiment_type != ANGLE_EXPERIMENT:
        raise ExperimentConfigError(
            f"config experiment_type must be '{ANGLE_EXPERIMENT}', got '{normalized.experiment_type}'"
        )


def load_experiment_config_from_raw(config: dict[str, Any]):
    return config, load_experiment_config_object(config)


def load_experiment_config_object(config: dict[str, Any]):
    if not isinstance(config, dict):
        raise ExperimentConfigError("config must be a JSON object")
    # Reuse the existing normalization path by writing no files.
    from csi_capture.experiment import _normalize_config  # local import to avoid a wider API break

    return _normalize_config(config)


def handle_capture(args: Any) -> int:
    if getattr(args, "config", None):
        return run_config(
            Path(args.config),
            expected_type=ANGLE_EXPERIMENT,
            device_override=getattr(args, "device", None),
            target_profile_override=getattr(args, "target_profile", None),
        )
    raw_config = build_angle_cli_config(args)
    return run_raw_config(
        raw_config,
        expected_type=ANGLE_EXPERIMENT,
        device_override=None,
        target_profile_override=getattr(args, "target_profile", None),
    )


ANGLE_PLUGIN = ExperimentPlugin(
    definition=ExperimentDefinition(
        experiment_id=ANGLE_EXPERIMENT,
        display_name="Angular Localization Capture",
        summary="Config-driven angle/AoA dataset capture for later localization experiments.",
        task_type="localization",
        modalities=("csi", "rssi", "fusion"),
        layout_style=LAYOUT_LEGACY_DISTANCE_ANGLE_V1,
        target_profile_id=DEFAULT_ENVIRONMENT_PROFILE_ID,
        supports_capture=True,
        supports_preprocess=False,
        supports_train=False,
        supports_evaluate=False,
        supports_report=False,
        supports_inspect=False,
        config_modes=("capture",),
    ),
    capture_handler=handle_capture,
    validate_handler=validate_angle_config,
    examples=(
        "tools/exp capture --experiment angle --config ../studies/csi_capture_characterization/configs/angle_radial_45deg_2runs.sample.json",
    ),
)
