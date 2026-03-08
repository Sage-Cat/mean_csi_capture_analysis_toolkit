#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DEVICE=""
TARGET_PROFILE="esp32s3_csi_v1"
EXP_ROOT="experiments"
EXP_ID="exp_interference_$(date -u +%Y%m%d_%H%M%S)"
SCENARIO_SET="core"
RUNS_PER_SCENARIO=3
MAX_RECORDS=1500
FORMAT="jsonl"
BAUD="921600"
DRY_RUN_PACKETS=5
DRY_RUN_TIMEOUT="10s"
SKIP_DRY_RUN=0
INTER_RUN_PAUSE_S="3"
CHANNEL="11"
BANDWIDTH_MHZ="20"
PACKET_RATE_HZ="250"
TX_POWER_DBM="default"
NOTES=""
LIST_SCENARIOS=0

usage() {
  cat <<'USAGE'
Capture interference-oriented RSSI/CSI runs across a fixed scenario matrix.

Usage:
  scripts/run_interference_protocol.sh [options]

Options:
  --device <path>             Serial device (default: auto-detect / env / /dev/esp32_csi)
  --target-profile <id>       Target environment profile (default: esp32s3_csi_v1)
  --exp-root <path>           Output root (default: experiments)
  --exp-id <id>               Experiment id (default: exp_interference_<UTC timestamp>)
  --scenario-set <core|full>  Scenario preset (default: core)
  --runs <n>                  Runs per scenario (default: 3)
  --max-records <n>           Parsed CSI packets per run (default: 1500)
  --format <jsonl|csv>        Capture format (default: jsonl)
  --baud <num>                Serial baud (default: 921600)
  --inter-run-pause-s <sec>   Pause between runs in same scenario (default: 3)
  --notes <text>              Free-form experiment notes
  --channel <num>             Meta note for Wi-Fi channel (default: 11)
  --bandwidth-mhz <num>       Meta note for bandwidth (default: 20)
  --packet-rate-hz <num>      Meta note for TX packet rate (default: 250)
  --tx-power-dbm <value>      Meta note for TX power (default: default)
  --dry-run-packets <n>       Serial probe packet count (default: 5)
  --dry-run-timeout <time>    Serial probe timeout (default: 10s)
  --skip-dry-run              Skip the initial serial probe
  --list-scenarios            Print the selected scenario matrix and exit
  -h, --help                  Show this help
USAGE
}

scenario_rows_core() {
  cat <<'EOF'
s01_ref_los_empty|block_a_reference|room_same|0|open|none|static|2.0|reference,los,empty_room,static|Reference: same room, clear LoS, move extra furniture away and keep people out of the path.
s02_los_tables_chairs|block_a_reference|room_same|0|open|furniture|static|2.0|los,furniture,tables,chairs,multipath_light|Same room with normal tables and chairs, but keep direct LoS between TX and RX.
s03_human_block_static|block_b_human|room_same|0|open|human_block|static|2.0|human_block,static,body_shadowing|One person stands still in the middle of the TX-RX path.
s04_human_motion_side|block_b_human|room_same|0|open|human_near_link|dynamic|2.0|human_motion,dynamic,near_link|One person walks near the link or beside RX without fully blocking LoS.
s05_adjacent_door_open|block_c_interroom|room_adjacent_open|0|open|doorway|static|2.5|door_open,interroom,partial_los|Adjacent rooms or doorway geometry with the door open.
s06_adjacent_door_closed|block_c_interroom|room_adjacent_open|0|closed|doorway|static|2.5|door_closed,interroom,attenuation|Use the same geometry as s05 but close the door.
s07_one_wall|block_c_interroom|room_one_wall|1|n_a|wall|static|3.0|one_wall,nlos,interroom|TX and RX in neighboring rooms with one wall between them.
s08_two_walls_corridor|block_c_interroom|room_two_walls_corridor|2|n_a|walls_corridor|static|4.0|two_walls,corridor,nlos,strong_attenuation|TX and RX separated by two walls with a corridor section between rooms.
s09_boxes_wood_partition|block_d_clutter|room_boxes|0|open|boxes_wood_partition|static|2.0|boxes,wood_partition,clutter,multipath_heavy|Office desk or room area with many boxes, wooden partitions, or shelves around the link.
EOF
}

scenario_rows_full() {
  scenario_rows_core
  cat <<'EOF'
s10_chair_cluster_rx|block_d_clutter|room_same|0|open|chair_cluster|static|2.0|chairs,rx_clutter,multipath_local|Place a dense cluster of chairs or tables near RX while keeping TX fixed.
s11_door_frame_offset|block_c_interroom|room_adjacent_offset|0|open|door_frame_offset|static|2.5|door_frame,offset_path,multipath|Use an offset doorway path, not centered in the door opening.
s12_corridor_people_motion|block_c_interroom|room_two_walls_corridor|2|n_a|walls_corridor|dynamic|4.0|two_walls,corridor,human_motion,dynamic|Use the corridor scenario and let one person walk in the corridor during capture.
s13_boxes_and_human|block_d_clutter|room_boxes|0|open|boxes_plus_human|static|2.0|boxes,human_near_rx,clutter|Keep the boxes and wooden partitions, plus one person standing near RX.
EOF
}

scenario_rows() {
  case "$1" in
    core) scenario_rows_core ;;
    full) scenario_rows_full ;;
    *)
      echo "Unsupported scenario set: $1" >&2
      return 2
      ;;
  esac
}

print_scenarios() {
  printf "%-24s %-20s %-22s %-5s %-8s %-20s %-8s %-6s %s\n" \
    "scenario_id" "block_id" "room_id" "wall" "door" "obstruction" "motion" "dist" "tags"
  while IFS='|' read -r scenario_id block_id room_id wall_count door_state obstruction_class motion_class estimated_distance_m scenario_tags prompt; do
    printf "%-24s %-20s %-22s %-5s %-8s %-20s %-8s %-6s %s\n" \
      "$scenario_id" "$block_id" "$room_id" "$wall_count" "$door_state" "$obstruction_class" "$motion_class" "$estimated_distance_m" "$scenario_tags"
    printf "  setup: %s\n" "$prompt"
  done < <(scenario_rows "$SCENARIO_SET")
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --device) DEVICE="$2"; shift 2 ;;
    --target-profile) TARGET_PROFILE="$2"; shift 2 ;;
    --exp-root) EXP_ROOT="$2"; shift 2 ;;
    --exp-id) EXP_ID="$2"; shift 2 ;;
    --scenario-set) SCENARIO_SET="$2"; shift 2 ;;
    --runs) RUNS_PER_SCENARIO="$2"; shift 2 ;;
    --max-records) MAX_RECORDS="$2"; shift 2 ;;
    --format) FORMAT="$2"; shift 2 ;;
    --baud) BAUD="$2"; shift 2 ;;
    --inter-run-pause-s) INTER_RUN_PAUSE_S="$2"; shift 2 ;;
    --notes) NOTES="$2"; shift 2 ;;
    --channel) CHANNEL="$2"; shift 2 ;;
    --bandwidth-mhz) BANDWIDTH_MHZ="$2"; shift 2 ;;
    --packet-rate-hz) PACKET_RATE_HZ="$2"; shift 2 ;;
    --tx-power-dbm) TX_POWER_DBM="$2"; shift 2 ;;
    --dry-run-packets) DRY_RUN_PACKETS="$2"; shift 2 ;;
    --dry-run-timeout) DRY_RUN_TIMEOUT="$2"; shift 2 ;;
    --skip-dry-run) SKIP_DRY_RUN=1; shift ;;
    --list-scenarios) LIST_SCENARIOS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ "$SCENARIO_SET" != "core" && "$SCENARIO_SET" != "full" ]]; then
  echo "Error: --scenario-set must be core or full" >&2
  exit 2
fi

if [[ "$FORMAT" != "jsonl" && "$FORMAT" != "csv" ]]; then
  echo "Error: --format must be jsonl or csv" >&2
  exit 2
fi

if ! [[ "$RUNS_PER_SCENARIO" =~ ^[0-9]+$ ]] || [[ "$RUNS_PER_SCENARIO" -le 0 ]]; then
  echo "Error: --runs must be a positive integer" >&2
  exit 2
fi

if ! [[ "$MAX_RECORDS" =~ ^[0-9]+$ ]] || [[ "$MAX_RECORDS" -le 0 ]]; then
  echo "Error: --max-records must be a positive integer" >&2
  exit 2
fi

if ! [[ "$DRY_RUN_PACKETS" =~ ^[0-9]+$ ]] || [[ "$DRY_RUN_PACKETS" -le 0 ]]; then
  echo "Error: --dry-run-packets must be a positive integer" >&2
  exit 2
fi

if [[ "$LIST_SCENARIOS" -eq 1 ]]; then
  print_scenarios
  exit 0
fi

cd "$REPO_ROOT"

mapfile -t device_info < <(python3 - "$DEVICE" <<'PY'
from csi_capture.core.device import resolve_serial_device
import sys

cli_device = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else None
resolved = resolve_serial_device(cli_device)
print(resolved.path)
print(resolved.realpath)
print(resolved.source)
PY
)

RESOLVED_DEVICE="${device_info[0]}"
RESOLVED_DEVICE_REALPATH="${device_info[1]}"
DEVICE_SOURCE="${device_info[2]}"

SCENARIO_COUNT="$(scenario_rows "$SCENARIO_SET" | wc -l | tr -d ' ')"
EXPECTED_TOTAL_RECORDS="$((SCENARIO_COUNT * RUNS_PER_SCENARIO * MAX_RECORDS))"
EXP_DIR="$EXP_ROOT/$EXP_ID"
META_FILE="$EXP_DIR/meta.json"
SCENARIO_ROWS_PAYLOAD="$(scenario_rows "$SCENARIO_SET")"

if [[ -e "$EXP_DIR" ]]; then
  echo "Error: experiment directory already exists: $EXP_DIR" >&2
  exit 2
fi

echo "Experiment directory: $EXP_DIR"
echo "Target profile: $TARGET_PROFILE"
echo "Serial device: $RESOLVED_DEVICE"
echo "Resolved path: $RESOLVED_DEVICE_REALPATH"
echo "Selection source: $DEVICE_SOURCE"
echo "Scenario set: $SCENARIO_SET"
echo "Scenario count: $SCENARIO_COUNT"
echo "Runs per scenario: $RUNS_PER_SCENARIO"
echo "Packets per run: $MAX_RECORDS"
echo "Expected total records: $EXPECTED_TOTAL_RECORDS"
echo
print_scenarios
echo

device_args=()
if [[ -n "$DEVICE" ]]; then
  device_args=(--device "$DEVICE")
fi

if [[ "$SKIP_DRY_RUN" -eq 0 ]]; then
  echo "Running initial serial dry-run probe..."
  ./tools/exp capture \
    --experiment static_sign_v1 \
    --target-profile "$TARGET_PROFILE" \
    --dry-run-packets "$DRY_RUN_PACKETS" \
    --dry-run-timeout "$DRY_RUN_TIMEOUT" \
    "${device_args[@]}"
  echo
fi

mkdir -p "$EXP_DIR"

SCENARIO_ROWS_PAYLOAD="$SCENARIO_ROWS_PAYLOAD" python3 - \
  "$META_FILE" \
  "$EXP_ID" \
  "$TARGET_PROFILE" \
  "$SCENARIO_SET" \
  "$RUNS_PER_SCENARIO" \
  "$MAX_RECORDS" \
  "$FORMAT" \
  "$BAUD" \
  "$RESOLVED_DEVICE" \
  "$RESOLVED_DEVICE_REALPATH" \
  "$DEVICE_SOURCE" \
  "$CHANNEL" \
  "$BANDWIDTH_MHZ" \
  "$PACKET_RATE_HZ" \
  "$TX_POWER_DBM" \
  "$NOTES" \
  <<'PY'
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    meta_path,
    exp_id,
    target_profile,
    scenario_set,
    runs_per_scenario,
    max_records,
    output_format,
    baud,
    device_path,
    device_realpath,
    device_source,
    channel,
    bandwidth_mhz,
    packet_rate_hz,
    tx_power_dbm,
    notes,
) = sys.argv[1:]

scenarios = []
for raw in os.environ.get("SCENARIO_ROWS_PAYLOAD", "").splitlines():
    parts = raw.rstrip("\n").split("|")
    if len(parts) != 10:
        continue
    (
        scenario_id,
        block_id,
        room_id,
        wall_count,
        door_state,
        obstruction_class,
        motion_class,
        estimated_distance_m,
        scenario_tags,
        prompt,
    ) = parts
    scenarios.append(
        {
            "scenario_id": scenario_id,
            "block_id": block_id,
            "room_id": room_id,
            "wall_count": int(wall_count),
            "door_state": door_state,
            "obstruction_class": obstruction_class,
            "motion_class": motion_class,
            "estimated_distance_m": float(estimated_distance_m),
            "scenario_tags": [tag for tag in scenario_tags.split(",") if tag],
            "setup_prompt": prompt,
        }
    )

payload = {
    "exp_id": exp_id,
    "experiment_type": "interference_v1",
    "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "target_profile": target_profile,
    "scenario_set": scenario_set,
    "runs_per_scenario": int(runs_per_scenario),
    "max_records_per_run": int(max_records),
    "output_format": output_format,
    "baud": int(baud),
    "device": {
        "path": device_path,
        "realpath": device_realpath,
        "source": device_source,
    },
    "channel": int(channel),
    "bandwidth_mhz": int(bandwidth_mhz),
    "packet_rate_hz": int(packet_rate_hz),
    "tx_power_dbm": tx_power_dbm,
    "notes": notes,
    "scenarios": scenarios,
}

Path(meta_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

read -r -p "Confirm TX is running and RX is streaming CSI_DATA. Press Enter to continue..."

GIT_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
if [[ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]]; then
  GIT_DIRTY=1
else
  GIT_DIRTY=0
fi

while IFS='|' read -r scenario_id block_id room_id wall_count door_state obstruction_class motion_class estimated_distance_m scenario_tags prompt; do
  echo
  echo "================================================================"
  echo "Scenario: $scenario_id"
  echo "Block: $block_id"
  echo "Room: $room_id"
  echo "Walls: $wall_count | Door: $door_state | Obstruction: $obstruction_class | Motion: $motion_class"
  echo "Estimated TX-RX distance (m): $estimated_distance_m"
  echo "Tags: $scenario_tags"
  echo "Setup: $prompt"
  read -r -p "Arrange the environment for $scenario_id and press Enter to start..."

  for run_idx in $(seq 1 "$RUNS_PER_SCENARIO"); do
    run_dir="$EXP_DIR/$scenario_id/run_${run_idx}"
    output_file="$run_dir/capture.$FORMAT"
    manifest_file="$run_dir/manifest.json"
    mkdir -p "$run_dir"

    echo
    echo "Run $run_idx/$RUNS_PER_SCENARIO for $scenario_id"
    read -r -p "Press Enter to capture this run..."

    python3 -m csi_capture.capture \
      -p "$RESOLVED_DEVICE" \
      -b "$BAUD" \
      -o "$output_file" \
      --format "$FORMAT" \
      --max-records "$MAX_RECORDS" \
      --exp-id "$EXP_ID" \
      --experiment-type interference_v1 \
      --scenario "$scenario_id" \
      --run-id "$run_idx" \
      --trial-id "capture" \
      --device-path "$RESOLVED_DEVICE"

    if [[ "$FORMAT" == "jsonl" ]]; then
      records_captured="$(wc -l < "$output_file" | tr -d ' ')"
    else
      records_captured="$(( $(wc -l < "$output_file") - 1 ))"
    fi

    if [[ "$records_captured" -le 0 ]]; then
      echo "Error: no records captured for $scenario_id run $run_idx" >&2
      exit 1
    fi

    python3 - \
      "$manifest_file" \
      "$EXP_ID" \
      "$scenario_id" \
      "$block_id" \
      "$room_id" \
      "$wall_count" \
      "$door_state" \
      "$obstruction_class" \
      "$motion_class" \
      "$estimated_distance_m" \
      "$scenario_tags" \
      "$run_idx" \
      "$records_captured" \
      "$TARGET_PROFILE" \
      "$RESOLVED_DEVICE" \
      "$RESOLVED_DEVICE_REALPATH" \
      "$GIT_COMMIT" \
      "$GIT_DIRTY" \
      "$output_file" \
      "$CHANNEL" \
      "$BANDWIDTH_MHZ" \
      "$PACKET_RATE_HZ" \
      "$TX_POWER_DBM" \
      "$NOTES" \
      <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    manifest_path,
    exp_id,
    scenario_id,
    block_id,
    room_id,
    wall_count,
    door_state,
    obstruction_class,
    motion_class,
    estimated_distance_m,
    scenario_tags,
    run_id,
    records_captured,
    target_profile,
    device_path,
    device_realpath,
    git_commit,
    git_dirty,
    output_file,
    channel,
    bandwidth_mhz,
    packet_rate_hz,
    tx_power_dbm,
    notes,
) = sys.argv[1:]

payload = {
    "exp_id": exp_id,
    "experiment_type": "interference_v1",
    "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "scenario": {
        "scenario_id": scenario_id,
        "block_id": block_id,
        "room_id": room_id,
        "wall_count": int(wall_count),
        "door_state": door_state,
        "obstruction_class": obstruction_class,
        "motion_class": motion_class,
        "estimated_distance_m": float(estimated_distance_m),
        "scenario_tags": [tag for tag in scenario_tags.split(",") if tag],
    },
    "run_id": int(run_id),
    "records_captured": int(records_captured),
    "target_profile": target_profile,
    "device_path": device_path,
    "device_realpath": device_realpath,
    "git_commit": git_commit,
    "git_dirty": bool(int(git_dirty)),
    "output_file": output_file,
    "config_snapshot": {
        "channel": int(channel),
        "bandwidth_mhz": int(bandwidth_mhz),
        "packet_rate_hz": int(packet_rate_hz),
        "tx_power_dbm": tx_power_dbm,
    },
    "notes": notes,
}

Path(manifest_path).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

    echo "Captured $records_captured records"
    echo "Output: $output_file"
    echo "Manifest: $manifest_file"

    if [[ "$run_idx" -lt "$RUNS_PER_SCENARIO" ]]; then
      sleep "$INTER_RUN_PAUSE_S"
    fi
  done
done < <(scenario_rows "$SCENARIO_SET")

echo
echo "Interference protocol complete."
echo "Data root: $EXP_DIR"
echo "Meta: $META_FILE"
