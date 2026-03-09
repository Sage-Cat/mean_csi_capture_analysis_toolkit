from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, TextIO

from csi_capture.capture import capture_stream, serial_lines
from csi_capture.core.device import (
    DeviceAccessError,
    format_device_banner,
    resolve_serial_device,
    validate_serial_device_access,
)
from csi_capture.core.environment import (
    DEFAULT_ENVIRONMENT_PROFILE_ID,
    EnvironmentProfileError,
    format_environment_banner,
    resolve_environment_profile,
)


@dataclass(frozen=True)
class InterferenceScenario:
    scenario_id: str
    block_id: str
    room_id: str
    wall_count: int
    door_state: str
    obstruction_class: str
    motion_class: str
    estimated_distance_m: float
    scenario_tags: tuple[str, ...]
    setup_prompt: str


CORE_SCENARIOS: tuple[InterferenceScenario, ...] = (
    InterferenceScenario(
        "s01_ref_los_empty",
        "block_a_reference",
        "room_same",
        0,
        "open",
        "none",
        "static",
        2.0,
        ("reference", "los", "empty_room", "static"),
        "Reference: same room, clear LoS, move extra furniture away and keep people out of the path.",
    ),
    InterferenceScenario(
        "s02_los_tables_chairs",
        "block_a_reference",
        "room_same",
        0,
        "open",
        "furniture",
        "static",
        2.0,
        ("los", "furniture", "tables", "chairs", "multipath_light"),
        "Same room with normal tables and chairs, but keep direct LoS between TX and RX.",
    ),
    InterferenceScenario(
        "s03_human_block_static",
        "block_b_human",
        "room_same",
        0,
        "open",
        "human_block",
        "static",
        2.0,
        ("human_block", "static", "body_shadowing"),
        "One person stands still in the middle of the TX-RX path.",
    ),
    InterferenceScenario(
        "s04_human_motion_side",
        "block_b_human",
        "room_same",
        0,
        "open",
        "human_near_link",
        "dynamic",
        2.0,
        ("human_motion", "dynamic", "near_link"),
        "One person walks near the link or beside RX without fully blocking LoS.",
    ),
    InterferenceScenario(
        "s05_adjacent_door_open",
        "block_c_interroom",
        "room_adjacent_open",
        0,
        "open",
        "doorway",
        "static",
        2.5,
        ("door_open", "interroom", "partial_los"),
        "Adjacent rooms or doorway geometry with the door open.",
    ),
    InterferenceScenario(
        "s06_adjacent_door_closed",
        "block_c_interroom",
        "room_adjacent_open",
        0,
        "closed",
        "doorway",
        "static",
        2.5,
        ("door_closed", "interroom", "attenuation"),
        "Use the same geometry as s05 but close the door.",
    ),
    InterferenceScenario(
        "s07_one_wall",
        "block_c_interroom",
        "room_one_wall",
        1,
        "n_a",
        "wall",
        "static",
        3.0,
        ("one_wall", "nlos", "interroom"),
        "TX and RX in neighboring rooms with one wall between them.",
    ),
    InterferenceScenario(
        "s08_two_walls_corridor",
        "block_c_interroom",
        "room_two_walls_corridor",
        2,
        "n_a",
        "walls_corridor",
        "static",
        4.0,
        ("two_walls", "corridor", "nlos", "strong_attenuation"),
        "TX and RX separated by two walls with a corridor section between rooms.",
    ),
    InterferenceScenario(
        "s09_boxes_wood_partition",
        "block_d_clutter",
        "room_boxes",
        0,
        "open",
        "boxes_wood_partition",
        "static",
        2.0,
        ("boxes", "wood_partition", "clutter", "multipath_heavy"),
        "Office desk or room area with many boxes, wooden partitions, or shelves around the link.",
    ),
)

FULL_EXTRA_SCENARIOS: tuple[InterferenceScenario, ...] = (
    InterferenceScenario(
        "s10_chair_cluster_rx",
        "block_d_clutter",
        "room_same",
        0,
        "open",
        "chair_cluster",
        "static",
        2.0,
        ("chairs", "rx_clutter", "multipath_local"),
        "Place a dense cluster of chairs or tables near RX while keeping TX fixed.",
    ),
    InterferenceScenario(
        "s11_door_frame_offset",
        "block_c_interroom",
        "room_adjacent_offset",
        0,
        "open",
        "door_frame_offset",
        "static",
        2.5,
        ("door_frame", "offset_path", "multipath"),
        "Use an offset doorway path, not centered in the door opening.",
    ),
    InterferenceScenario(
        "s12_corridor_people_motion",
        "block_c_interroom",
        "room_two_walls_corridor",
        2,
        "n_a",
        "walls_corridor",
        "dynamic",
        4.0,
        ("two_walls", "corridor", "human_motion", "dynamic"),
        "Use the corridor scenario and let one person walk in the corridor during capture.",
    ),
    InterferenceScenario(
        "s13_boxes_and_human",
        "block_d_clutter",
        "room_boxes",
        0,
        "open",
        "boxes_plus_human",
        "static",
        2.0,
        ("boxes", "human_near_rx", "clutter"),
        "Keep the boxes and wooden partitions, plus one person standing near RX.",
    ),
)

SCENARIO_SETS: dict[str, tuple[InterferenceScenario, ...]] = {
    "core": CORE_SCENARIOS,
    "full": CORE_SCENARIOS + FULL_EXTRA_SCENARIOS,
}


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_exp_id() -> str:
    return f"exp_interference_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"


def _parse_duration_s(text: str) -> float:
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


def _duration_limited_lines(lines: Iterable[str], duration_s: float) -> Iterable[str]:
    deadline = time.monotonic() + duration_s
    for line in lines:
        if time.monotonic() >= deadline:
            break
        yield line


def _dry_run_capture(*, device_path: str, baud: int, packets: int, timeout_s: float, max_wait_s: float) -> int:
    lines = serial_lines(
        port=device_path,
        baud=baud,
        timeout=timeout_s,
        reconnect_on_error=False,
        reconnect_delay_s=1.0,
        yield_on_timeout=True,
    )
    sink = io.StringIO()
    return capture_stream(
        _duration_limited_lines(lines, max_wait_s),
        out=sink,
        output_format="jsonl",
        max_records=packets,
        metadata=None,
    )


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _relative_display(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _git_info(repo_root: Path) -> dict[str, object]:
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


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _scenario_to_payload(scenario: InterferenceScenario) -> dict[str, object]:
    return {
        "scenario_id": scenario.scenario_id,
        "block_id": scenario.block_id,
        "room_id": scenario.room_id,
        "wall_count": scenario.wall_count,
        "door_state": scenario.door_state,
        "obstruction_class": scenario.obstruction_class,
        "motion_class": scenario.motion_class,
        "estimated_distance_m": scenario.estimated_distance_m,
        "scenario_tags": list(scenario.scenario_tags),
        "setup_prompt": scenario.setup_prompt,
    }


def _print_scenarios(scenario_set: str, stream: TextIO = sys.stdout) -> None:
    scenarios = SCENARIO_SETS[scenario_set]
    stream.write(
        f"{'scenario_id':24} {'block_id':20} {'room_id':22} {'wall':5} {'door':8} "
        f"{'obstruction':20} {'motion':8} {'dist':6} tags\n"
    )
    for scenario in scenarios:
        stream.write(
            f"{scenario.scenario_id:24} {scenario.block_id:20} {scenario.room_id:22} "
            f"{scenario.wall_count:<5} {scenario.door_state:8} {scenario.obstruction_class:20} "
            f"{scenario.motion_class:8} {scenario.estimated_distance_m:<6} "
            f"{','.join(scenario.scenario_tags)}\n"
        )
        stream.write(f"  setup: {scenario.setup_prompt}\n")


def _prompt_enter(prompt: str, assume_yes: bool) -> None:
    if assume_yes:
        print(f"{prompt} [auto-confirmed]")
        return
    try:
        input(prompt)
    except EOFError as exc:
        raise RuntimeError("prompt input is unavailable; rerun with --yes or an interactive terminal") from exc


def _capture_run(
    *,
    device_path: str,
    baud: int,
    output_path: Path,
    output_format: str,
    max_records: int,
    metadata: dict[str, object],
) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        return capture_stream(
            serial_lines(device_path, baud),
            out=handle,
            output_format=output_format,
            max_records=max_records,
            metadata=metadata,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the interference_v1 RSSI/CSI capture protocol.",
    )
    parser.add_argument("--device", default=None, help="Serial device path, e.g. /dev/ttyACM1 or COM4")
    parser.add_argument(
        "--target-profile",
        default=DEFAULT_ENVIRONMENT_PROFILE_ID,
        help=f"Target environment profile id (default: {DEFAULT_ENVIRONMENT_PROFILE_ID})",
    )
    parser.add_argument("--exp-root", default="experiments", help="Output root (default: experiments)")
    parser.add_argument("--exp-id", default=None, help="Experiment id (default: exp_interference_<UTC timestamp>)")
    parser.add_argument("--scenario-set", choices=sorted(SCENARIO_SETS), default="core")
    parser.add_argument("--runs", type=int, default=3, help="Runs per scenario (default: 3)")
    parser.add_argument("--max-records", type=int, default=1500, help="Parsed CSI packets per run")
    parser.add_argument("--format", choices=("jsonl", "csv"), default="jsonl")
    parser.add_argument("--baud", type=int, default=921600)
    parser.add_argument("--inter-run-pause-s", type=float, default=3.0)
    parser.add_argument("--notes", default="", help="Free-form experiment notes")
    parser.add_argument("--channel", type=int, default=11)
    parser.add_argument("--bandwidth-mhz", type=int, default=20)
    parser.add_argument("--packet-rate-hz", type=int, default=250)
    parser.add_argument("--tx-power-dbm", default="default")
    parser.add_argument("--dry-run-packets", type=int, default=5)
    parser.add_argument("--dry-run-timeout", default="10s")
    parser.add_argument("--skip-dry-run", action="store_true")
    parser.add_argument("--list-scenarios", action="store_true")
    parser.add_argument("--yes", action="store_true", help="Auto-confirm all prompts")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.runs <= 0:
        parser.error("--runs must be > 0")
    if args.max_records <= 0:
        parser.error("--max-records must be > 0")
    if args.dry_run_packets <= 0:
        parser.error("--dry-run-packets must be > 0")
    if args.inter_run_pause_s < 0:
        parser.error("--inter-run-pause-s must be >= 0")

    if args.list_scenarios:
        _print_scenarios(args.scenario_set)
        return 0

    repo_root = _repo_root()
    exp_root = Path(args.exp_root)
    if not exp_root.is_absolute():
        exp_root = repo_root / exp_root
    exp_id = args.exp_id or _default_exp_id()
    exp_dir = exp_root / exp_id
    meta_file = exp_dir / "meta.json"
    scenarios = SCENARIO_SETS[args.scenario_set]

    try:
        target_profile = resolve_environment_profile(args.target_profile)
        print(format_environment_banner(target_profile))

        device_arg = None if args.device is None or args.device.strip().lower() == "auto" else args.device
        device = resolve_serial_device(device_arg)
        print(format_device_banner(device))
        validate_serial_device_access(device.path)

        if exp_dir.exists():
            raise RuntimeError(f"experiment directory already exists: {_relative_display(exp_dir, repo_root)}")

        expected_total_records = len(scenarios) * args.runs * args.max_records
        print(f"Experiment directory: {_relative_display(exp_dir, repo_root)}")
        print(f"Target profile: {target_profile.profile_id}")
        print(f"Scenario set: {args.scenario_set}")
        print(f"Scenario count: {len(scenarios)}")
        print(f"Runs per scenario: {args.runs}")
        print(f"Packets per run: {args.max_records}")
        print(f"Expected total records: {expected_total_records}")
        print()
        _print_scenarios(args.scenario_set)
        print()

        if not args.skip_dry_run:
            print("Running initial serial dry-run probe...")
            written = _dry_run_capture(
                device_path=device.path,
                baud=args.baud,
                packets=args.dry_run_packets,
                timeout_s=1.0,
                max_wait_s=_parse_duration_s(args.dry_run_timeout),
            )
            if written < args.dry_run_packets:
                raise RuntimeError(
                    f"dry-run timed out after {args.dry_run_timeout}; "
                    f"expected {args.dry_run_packets} packets, got {written}"
                )
            print(f"Dry-run success. Parsed packets: {written}")
            print()

        exp_dir.mkdir(parents=True, exist_ok=False)
        _write_json(
            meta_file,
            {
                "exp_id": exp_id,
                "experiment_type": "interference_v1",
                "created_at_utc": _utc_now_iso(),
                "target_profile": target_profile.profile_id,
                "scenario_set": args.scenario_set,
                "runs_per_scenario": args.runs,
                "max_records_per_run": args.max_records,
                "output_format": args.format,
                "baud": args.baud,
                "device": {
                    "path": device.path,
                    "realpath": device.realpath,
                    "source": device.source,
                },
                "channel": args.channel,
                "bandwidth_mhz": args.bandwidth_mhz,
                "packet_rate_hz": args.packet_rate_hz,
                "tx_power_dbm": args.tx_power_dbm,
                "notes": args.notes,
                "scenarios": [_scenario_to_payload(scenario) for scenario in scenarios],
            },
        )

        _prompt_enter("Confirm TX is running and RX is streaming CSI_DATA. Press Enter to continue...", args.yes)
        git_info = _git_info(repo_root)

        for scenario in scenarios:
            print()
            print("================================================================")
            print(f"Scenario: {scenario.scenario_id}")
            print(f"Block: {scenario.block_id}")
            print(f"Room: {scenario.room_id}")
            print(
                "Walls: "
                f"{scenario.wall_count} | Door: {scenario.door_state} | "
                f"Obstruction: {scenario.obstruction_class} | Motion: {scenario.motion_class}"
            )
            print(f"Estimated TX-RX distance (m): {scenario.estimated_distance_m}")
            print(f"Tags: {','.join(scenario.scenario_tags)}")
            print(f"Setup: {scenario.setup_prompt}")
            _prompt_enter(
                f"Arrange the environment for {scenario.scenario_id} and press Enter to start...",
                args.yes,
            )

            for run_idx in range(1, args.runs + 1):
                run_dir = exp_dir / scenario.scenario_id / f"run_{run_idx}"
                output_file = run_dir / f"capture.{args.format}"
                manifest_file = run_dir / "manifest.json"

                print()
                print(f"Run {run_idx}/{args.runs} for {scenario.scenario_id}")
                _prompt_enter("Press Enter to capture this run...", args.yes)

                records_captured = _capture_run(
                    device_path=device.path,
                    baud=args.baud,
                    output_path=output_file,
                    output_format=args.format,
                    max_records=args.max_records,
                    metadata={
                        "exp_id": exp_id,
                        "experiment_type": "interference_v1",
                        "scenario": scenario.scenario_id,
                        "run_id": run_idx,
                        "trial_id": "capture",
                        "device_path": device.path,
                    },
                )

                if records_captured <= 0:
                    raise RuntimeError(f"no records captured for {scenario.scenario_id} run {run_idx}")

                _write_json(
                    manifest_file,
                    {
                        "exp_id": exp_id,
                        "experiment_type": "interference_v1",
                        "created_at_utc": _utc_now_iso(),
                        "scenario": _scenario_to_payload(scenario),
                        "run_id": run_idx,
                        "records_captured": records_captured,
                        "target_profile": target_profile.profile_id,
                        "device_path": device.path,
                        "device_realpath": device.realpath,
                        "git_commit": git_info["git_commit"],
                        "git_dirty": git_info["git_dirty"],
                        "output_file": _relative_display(output_file, repo_root),
                        "config_snapshot": {
                            "channel": args.channel,
                            "bandwidth_mhz": args.bandwidth_mhz,
                            "packet_rate_hz": args.packet_rate_hz,
                            "tx_power_dbm": args.tx_power_dbm,
                        },
                        "notes": args.notes,
                    },
                )

                print(f"Captured {records_captured} records")
                print(f"Output: {_relative_display(output_file, repo_root)}")
                print(f"Manifest: {_relative_display(manifest_file, repo_root)}")

                if run_idx < args.runs and args.inter_run_pause_s > 0:
                    time.sleep(args.inter_run_pause_s)

        print()
        print("Interference protocol complete.")
        print(f"Data root: {_relative_display(exp_dir, repo_root)}")
        print(f"Meta: {_relative_display(meta_file, repo_root)}")
        return 0
    except (
        DeviceAccessError,
        EnvironmentProfileError,
        RuntimeError,
        ValueError,
    ) as err:
        print(f"Error: {err}")
        return 2
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
