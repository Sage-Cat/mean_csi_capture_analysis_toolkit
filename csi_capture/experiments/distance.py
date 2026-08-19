from __future__ import annotations

from pathlib import Path

from csi_capture.core.domain import ExperimentDefinition
from csi_capture.core.environment import DEFAULT_ENVIRONMENT_PROFILE_ID
from csi_capture.core.layout import LAYOUT_LEGACY_DISTANCE_ANGLE_V1
from csi_capture.experiment import run_config
from csi_capture.experiments.registry import ExperimentPlugin


DISTANCE_EXPERIMENT = "distance"


def run_distance_capture_config(
    config_path: Path,
    device_override: str | None = None,
    target_profile_override: str | None = None,
) -> int:
    """Compatibility adapter for existing distance config-driven capture."""
    return run_config(
        config_path,
        expected_type=DISTANCE_EXPERIMENT,
        device_override=device_override,
        target_profile_override=target_profile_override,
    )


def validate_distance_config(mode: str, config: dict[str, object]) -> None:
    if mode.strip().lower() != "capture":
        raise ValueError("distance currently supports only capture config validation")
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    from csi_capture.experiment import _normalize_config  # local import to avoid a wider API break

    normalized = _normalize_config(config)
    if normalized.experiment_type != DISTANCE_EXPERIMENT:
        raise ValueError(
            f"config experiment_type must be '{DISTANCE_EXPERIMENT}', got '{normalized.experiment_type}'"
        )


def handle_capture(args: object) -> int:
    config_path = Path(getattr(args, "config"))
    return run_distance_capture_config(
        config_path,
        device_override=getattr(args, "device", None),
        target_profile_override=getattr(args, "target_profile", None),
    )


DISTANCE_PLUGIN = ExperimentPlugin(
    definition=ExperimentDefinition(
        experiment_id=DISTANCE_EXPERIMENT,
        display_name="Distance Measurement Capture",
        summary="Config-driven CSI/RSSI capture for ranging experiments.",
        task_type="regression",
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
    validate_handler=validate_distance_config,
    examples=(
        "tools/exp capture --experiment distance --config ../studies/csi_capture_characterization/configs/distance_capture.sample.json",
    ),
)
