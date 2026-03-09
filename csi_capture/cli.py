from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from csi_capture.core.device import (
    DeviceAccessError,
    list_serial_candidates,
)
from csi_capture.core.environment import (
    DEFAULT_ENVIRONMENT_PROFILE_ID,
    EnvironmentProfileError,
    list_environment_profiles,
)
from csi_capture.experiment import ExperimentConfigError
from csi_capture.experiments import (
    experiment_choices,
    get_experiment,
    iter_experiments,
)


def _print_list_devices() -> None:
    candidates = list_serial_candidates()
    if not candidates:
        print(
            "No candidate serial devices found under "
            "/dev/esp32_csi, /dev/ttyACM*, /dev/ttyUSB*, "
            "/dev/tty.usbmodem*, /dev/cu.usbmodem*, /dev/tty.usbserial*, /dev/cu.usbserial*, or COM ports."
        )
        return

    print("Serial device candidates:")
    for path in candidates:
        realpath = str(Path(path).resolve(strict=False))
        marker = ""
        if path != realpath:
            marker = f" -> {realpath}"
        print(f"- {path}{marker}")


def _print_list_target_profiles() -> None:
    profiles = list_environment_profiles()
    if not profiles:
        print("No target profiles are registered.")
        return
    print("Target environment profiles:")
    for profile in profiles:
        print(f"- {profile.profile_id}: {profile.display_name} ({profile.board}, {profile.chip})")


def _print_list_experiments() -> None:
    plugins = iter_experiments()
    if not plugins:
        print("No experiments are registered.")
        return

    print("Registered experiments:")
    for plugin in plugins:
        definition = plugin.definition
        capabilities = [
            name
            for name in ("capture", "train", "eval", "report", "inspect", "validate-config")
            if plugin.supports(name)
        ]
        caps_text = ", ".join(capabilities) if capabilities else "metadata-only"
        modalities = "/".join(definition.modalities)
        print(
            f"- {definition.experiment_id}: {definition.display_name} "
            f"[task={definition.task_type}; modalities={modalities}; actions={caps_text}]"
        )


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("config must be a JSON object")
    return payload


def _dispatch_experiment_action(args: argparse.Namespace, action: str) -> int:
    try:
        plugin = get_experiment(args.experiment)
    except KeyError as err:
        print(f"Error: {err}")
        return 2

    try:
        if action == "capture":
            handler = plugin.capture_handler
        elif action == "train":
            handler = plugin.train_handler
        elif action == "eval":
            handler = plugin.eval_handler
        else:
            raise ValueError(f"Unsupported action dispatch: {action}")

        if handler is None:
            print(f"Error: experiment '{args.experiment}' does not support '{action}'")
            return 2
        return int(handler(args))
    except (
        ValueError,
        DeviceAccessError,
        EnvironmentProfileError,
        RuntimeError,
        ExperimentConfigError,
    ) as err:
        print(f"Error: {err}")
        return 2

def cmd_capture(args: argparse.Namespace) -> int:
    return _dispatch_experiment_action(args, "capture")


def cmd_train(args: argparse.Namespace) -> int:
    return _dispatch_experiment_action(args, "train")


def cmd_eval(args: argparse.Namespace) -> int:
    return _dispatch_experiment_action(args, "eval")


def cmd_validate_config(args: argparse.Namespace) -> int:
    try:
        plugin = get_experiment(args.experiment)
    except KeyError as err:
        print(f"Error: {err}")
        return 2
    if plugin.validate_handler is None:
        print(f"Error: experiment '{args.experiment}' does not support validate-config")
        return 2

    config_path = Path(args.config)
    if not config_path.exists():
        print(f"Error: config path does not exist: {config_path}")
        return 2

    try:
        payload = _load_json(config_path)
        plugin.validate_handler(args.mode, payload)
    except (ValueError, json.JSONDecodeError, ExperimentConfigError) as err:
        print(f"Error: {err}")
        return 2

    print(f"Config validation passed: mode={args.mode} file={config_path}")
    return 0


def cmd_distance(args: argparse.Namespace) -> int:
    args.experiment = "distance"
    return cmd_capture(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Unified CSI experiment CLI (capture/train/eval).",
    )
    parser.add_argument(
        "--list-devices",
        action="store_true",
        help=(
            "List serial candidates: /dev/esp32_csi, /dev/ttyACM*, /dev/ttyUSB*, "
            "/dev/tty.usbmodem*, /dev/cu.usbmodem*, /dev/tty.usbserial*, /dev/cu.usbserial*, COMx"
        ),
    )
    parser.add_argument(
        "--list-target-profiles",
        action="store_true",
        help="List available target environment profiles.",
    )
    parser.add_argument(
        "--list-experiments",
        action="store_true",
        help="List registered experiment families and supported actions.",
    )

    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list-devices", help="List serial candidates.")
    sub.add_parser("list-target-profiles", help="List available target environment profiles.")
    sub.add_parser("list-experiments", help="List registered experiment families.")

    capture = sub.add_parser("capture", help="Capture labeled dataset runs.")
    capture.add_argument(
        "--experiment",
        required=True,
        choices=experiment_choices(),
        help="Experiment name.",
    )
    capture.add_argument("--config", default=None, help="Experiment config JSON when applicable.")
    capture.add_argument("--label", default=None, help="Label (baseline or hands_up)")
    capture.add_argument("--runs", type=int, default=1, help="Number of runs to capture")
    capture.add_argument(
        "--duration",
        default="20s",
        help="Duration per run (e.g. 20s, 1m). Ignored when --packets-per-run is used.",
    )
    capture.add_argument("--packets-per-run", type=int, default=None, help="Capture N packets per run")
    capture.add_argument("--dataset-root", default="data/experiments", help="Dataset root directory")
    capture.add_argument("--dataset-id", default=None, help="Dataset id (default: UTC date YYYYMMDD)")
    capture.add_argument("--device", default=None, help="Serial device path override")
    capture.add_argument(
        "--target-profile",
        default=DEFAULT_ENVIRONMENT_PROFILE_ID,
        help=f"Target environment profile id (default: {DEFAULT_ENVIRONMENT_PROFILE_ID}).",
    )
    capture.add_argument("--baud", type=int, default=921600, help="Serial baud")
    capture.add_argument("--timeout-s", type=float, default=1.0, help="Serial read timeout seconds")
    capture.add_argument("--subject-id", default=None, help="Optional subject identifier")
    capture.add_argument("--environment-id", default=None, help="Optional environment identifier")
    capture.add_argument("--notes", default=None, help="Optional notes")
    capture.add_argument(
        "--dry-run-packets",
        type=int,
        default=0,
        help="Open serial and parse N packets, then exit without writing dataset",
    )
    capture.add_argument(
        "--dry-run-timeout",
        default="10s",
        help="Maximum wait duration for dry-run packet sampling",
    )
    capture.add_argument(
        "--exp-id",
        default=None,
        help="Angle CLI mode experiment id when --config is omitted.",
    )
    capture.add_argument("--run-id", default=None, help="Optional single run id for angle CLI mode.")
    capture.add_argument(
        "--run-ids",
        nargs="+",
        default=None,
        help="Optional explicit run ids for angle CLI mode.",
    )
    capture.add_argument("--angles", nargs="+", default=None, help="Angle list for angle CLI mode.")
    capture.add_argument(
        "--repeats-per-angle",
        type=int,
        default=1,
        help="Repeats per angle for angle CLI mode.",
    )
    capture.add_argument(
        "--packets-per-repeat",
        type=int,
        default=None,
        help="Packets per angle trial for angle CLI mode.",
    )
    capture.add_argument(
        "--duration-s",
        type=float,
        default=None,
        help="Duration per angle trial in seconds for angle CLI mode.",
    )
    capture.add_argument(
        "--output-format",
        choices=("jsonl", "csv"),
        default="jsonl",
        help="Capture output format for angle CLI mode.",
    )
    capture.add_argument(
        "--inter-trial-pause-s",
        type=float,
        default=0.0,
        help="Pause between angle trials in seconds.",
    )
    capture.add_argument(
        "--wait-enter",
        action="store_true",
        help="Pause between angle trials and wait for Enter.",
    )
    capture.add_argument(
        "--scenario-tags",
        nargs="+",
        default=None,
        help="Scenario tags for angle CLI mode.",
    )
    capture.add_argument("--room-id", default="", help="Room identifier for angle CLI mode.")
    capture.add_argument("--num-antennas", type=int, default=1, help="Angle array antenna count.")
    capture.add_argument(
        "--antenna-spacing-m",
        type=float,
        default=None,
        help="Angle array antenna spacing in meters.",
    )
    capture.add_argument(
        "--orientation-reference",
        default="0 deg points from receiver front toward AP",
        help="Geometry metadata for angle CLI mode.",
    )
    capture.add_argument(
        "--measurement-positions",
        default="AP fixed in center, receiver moved between angle marks",
        help="Measurement geometry notes for angle CLI mode.",
    )
    capture.add_argument(
        "--output-root",
        default="experiments",
        help="Output root for config-driven or angle CLI capture.",
    )

    train = sub.add_parser("train", help="Train model for an experiment dataset.")
    train.add_argument(
        "--experiment",
        required=True,
        choices=experiment_choices(),
        help="Experiment name.",
    )
    train.add_argument("--dataset", required=True, help="Dataset directory path")
    train.add_argument("--model", default="svm_linear", help="Model name: svm_linear or logreg")
    train.add_argument("--window", default="1s", help="Feature window duration")
    train.add_argument("--overlap", type=float, default=0.5, help="Window overlap [0,1)")
    train.add_argument("--test-size", type=float, default=0.3, help="Group split test fraction")
    train.add_argument("--seed", type=int, default=42, help="Random seed")
    train.add_argument("--artifact", default=None, help="Output model artifact path")

    eval_parser = sub.add_parser("eval", help="Evaluate a trained model.")
    eval_parser.add_argument(
        "--experiment",
        required=True,
        choices=experiment_choices(),
        help="Experiment name.",
    )
    eval_parser.add_argument("--dataset", required=True, help="Dataset directory path")
    eval_parser.add_argument("--model", required=True, help="Model artifact path")
    eval_parser.add_argument("--report", required=True, help="Output report JSON path")
    eval_parser.add_argument("--window", default=None, help="Optional window override")
    eval_parser.add_argument("--overlap", type=float, default=None, help="Optional overlap override")

    validate = sub.add_parser("validate-config", help="Validate experiment config JSON")
    validate.add_argument(
        "--experiment",
        required=True,
        choices=experiment_choices(),
        help="Experiment name.",
    )
    validate.add_argument("--mode", required=True, choices=("capture", "train", "eval", "report"))
    validate.add_argument("--config", required=True, help="Config JSON path")

    distance = sub.add_parser("distance", help="Compatibility adapter to distance capture config")
    distance.add_argument("--config", required=True, help="Distance config path")
    distance.add_argument("--device", default=None, help="Serial device path override")
    distance.add_argument(
        "--target-profile",
        default=None,
        help="Target environment profile id override.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.list_devices:
        _print_list_devices()
        return 0
    if args.list_target_profiles:
        _print_list_target_profiles()
        return 0
    if args.list_experiments:
        _print_list_experiments()
        return 0

    if args.command is None:
        parser.print_help()
        return 2

    if args.command == "list-devices":
        _print_list_devices()
        return 0
    if args.command == "list-target-profiles":
        _print_list_target_profiles()
        return 0
    if args.command == "list-experiments":
        _print_list_experiments()
        return 0
    if args.command == "capture":
        return cmd_capture(args)
    if args.command == "train":
        return cmd_train(args)
    if args.command == "eval":
        return cmd_eval(args)
    if args.command == "validate-config":
        return cmd_validate_config(args)
    if args.command == "distance":
        return cmd_distance(args)

    print(f"Error: unknown command '{args.command}'")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
