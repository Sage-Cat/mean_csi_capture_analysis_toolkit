from __future__ import annotations

from csi_capture.core.domain import ExperimentDefinition, LabelSet
from csi_capture.core.environment import DEFAULT_ENVIRONMENT_PROFILE_ID
from csi_capture.core.layout import LAYOUT_CANONICAL_V1
from csi_capture.experiments.registry import ExperimentPlugin

PRESENCE_EXPERIMENT = "presence_v1"


def validate_presence_config(mode: str, config: dict[str, object]) -> None:
    if not isinstance(config, dict):
        raise ValueError("config must be a JSON object")
    if config.get("experiment") not in (None, PRESENCE_EXPERIMENT):
        raise ValueError(f"config.experiment must be '{PRESENCE_EXPERIMENT}' when provided")
    mode_norm = mode.strip().lower()
    if mode_norm == "capture":
        scenario = config.get("scenario")
        if not isinstance(scenario, str) or not scenario.strip():
            raise ValueError("capture config requires non-empty scenario")
        packets = config.get("packets_per_trial")
        duration = config.get("duration_s")
        if packets is None and duration is None:
            raise ValueError("capture config requires packets_per_trial or duration_s")
    elif mode_norm in {"train", "eval", "report"}:
        dataset = config.get("dataset")
        if not isinstance(dataset, str) or not dataset.strip():
            raise ValueError(f"{mode_norm} config requires non-empty dataset")
    else:
        raise ValueError("mode must be one of: capture, train, eval, report")


PRESENCE_PLUGIN = ExperimentPlugin(
    definition=ExperimentDefinition(
        experiment_id=PRESENCE_EXPERIMENT,
        display_name="Presence Detection v1",
        summary="Future-ready binary detection plugin shape for occupied vs empty scenes.",
        task_type="detection",
        modalities=("csi", "rssi", "fusion"),
        layout_style=LAYOUT_CANONICAL_V1,
        target_profile_id=DEFAULT_ENVIRONMENT_PROFILE_ID,
        supports_capture=False,
        supports_train=False,
        supports_evaluate=False,
        supports_report=False,
        supports_inspect=False,
        config_modes=("capture", "train", "eval", "report"),
        label_set=LabelSet(
            label_set_id="presence_binary_v1",
            task_type="detection",
            labels=("empty", "occupied"),
            positive_label="occupied",
        ),
    ),
    validate_handler=validate_presence_config,
    examples=(
        "tools/exp validate-config --experiment presence_v1 --mode capture --config ../studies/csi_capture_characterization/configs/presence_v1.capture.sample.json",
    ),
)
