#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

PORT=""
BAUD="921600"
TARGET="esp32s3"
TARGET_PROFILE="esp32s3_csi_v1"
FORMAT="jsonl"
MAX_RECORDS="2500"

BUILD=1
FLASH=1

EXP_ID="$(date +%Y%m%d_%H%M%S)"
SCENARIO="LoS"
RUN_ID="1"
DISTANCE_M="1.0"

CHANNEL="11"
BANDWIDTH_MHZ="20"
PACKET_RATE_HZ="250"
TX_POWER_DBM="default"

IDF_PATH_DEFAULT="$HOME/esp/esp-idf"
ESP_CSI_PATH_DEFAULT="$HOME/esp/esp-csi"
IDF_PATH="${IDF_PATH:-$IDF_PATH_DEFAULT}"
ESP_CSI_PATH="${ESP_CSI_PATH:-$ESP_CSI_PATH_DEFAULT}"

OUT_FILE=""
EXP_ROOT="$REPO_ROOT/experiments"

usage() {
  cat <<'EOF'
RX laptop runner (ESP32 csi_recv + structured CSI capture).

Usage:
  scripts/run_rx_laptop.sh [options]

Key experiment options:
  --exp-id <id>            Experiment id (default: current timestamp)
  --scenario <name>        LoS/NLoS_furniture/NLoS_human/NLoS_wall (default: LoS)
  --run-id <n>             Run index (default: 1)
  --distance-m <meters>    Ground-truth distance (default: 1.0)
  --max-records <n>        Number of CSI records to capture (default: 2500)

Device/build options:
  --port <path>            Serial port (default: auto-detect, prefers /dev/esp32_csi)
  --baud <num>             Serial baud (default: 921600)
  --target <chip>          IDF target (default: esp32s3)
  --target-profile <id>    Environment profile id (default: esp32s3_csi_v1)
  --idf-path <path>        ESP-IDF path (default: $HOME/esp/esp-idf)
  --esp-csi-path <path>    esp-csi path (default: $HOME/esp/esp-csi)
  --skip-build             Do not run idf.py build
  --skip-flash             Do not run idf.py flash

Output/options:
  --format <jsonl|csv>     Output format (default: jsonl)
  --out <path>             Output file override
  --exp-root <path>        Experiment root directory (default: <repo>/experiments)

Meta options (written to meta.json):
  --channel <num>          Wi-Fi channel (default: 11)
  --bandwidth-mhz <num>    Bandwidth MHz (default: 20)
  --packet-rate-hz <num>   Packet rate (default: 250)
  --tx-power-dbm <value>   TX power note (default: default)

  -h, --help               Show this help
EOF
}

detect_serial_port() {
  if [[ -e "/dev/esp32_csi" ]]; then
    echo "/dev/esp32_csi"
    return 0
  fi

  local candidates=()
  shopt -s nullglob
  candidates=(
    /dev/ttyACM*
    /dev/ttyUSB*
    /dev/cu.usbmodem*
    /dev/tty.usbmodem*
    /dev/cu.usbserial*
    /dev/tty.usbserial*
  )
  shopt -u nullglob

  if [[ ${#candidates[@]} -gt 0 ]]; then
    echo "${candidates[0]}"
    return 0
  fi
  return 1
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --exp-id) EXP_ID="$2"; shift 2 ;;
    --scenario) SCENARIO="$2"; shift 2 ;;
    --run-id) RUN_ID="$2"; shift 2 ;;
    --distance-m) DISTANCE_M="$2"; shift 2 ;;
    --max-records) MAX_RECORDS="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --baud) BAUD="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --target-profile) TARGET_PROFILE="$2"; shift 2 ;;
    --idf-path) IDF_PATH="$2"; shift 2 ;;
    --esp-csi-path) ESP_CSI_PATH="$2"; shift 2 ;;
    --skip-build) BUILD=0; shift ;;
    --skip-flash) FLASH=0; shift ;;
    --format) FORMAT="$2"; shift 2 ;;
    --out) OUT_FILE="$2"; shift 2 ;;
    --exp-root) EXP_ROOT="$2"; shift 2 ;;
    --channel) CHANNEL="$2"; shift 2 ;;
    --bandwidth-mhz) BANDWIDTH_MHZ="$2"; shift 2 ;;
    --packet-rate-hz) PACKET_RATE_HZ="$2"; shift 2 ;;
    --tx-power-dbm) TX_POWER_DBM="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 2 ;;
  esac
done

if [[ -z "$PORT" ]]; then
  if ! PORT="$(detect_serial_port)"; then
    echo "No serial port detected. Use --port to specify one." >&2
    exit 2
  fi
  echo "Auto-detected RX serial port: $PORT"
fi

if [[ "$FORMAT" != "jsonl" && "$FORMAT" != "csv" ]]; then
  echo "Unsupported format: $FORMAT (use jsonl or csv)" >&2
  exit 2
fi

if [[ ! -f "$IDF_PATH/export.sh" ]]; then
  echo "ESP-IDF export script not found: $IDF_PATH/export.sh" >&2
  exit 2
fi

RECV_DIR="$ESP_CSI_PATH/examples/get-started/csi_recv"
if [[ ! -d "$RECV_DIR" ]]; then
  echo "csi_recv directory not found: $RECV_DIR" >&2
  exit 2
fi

DISTANCE_TAG="${DISTANCE_M//./p}"
BASE_DIR="$EXP_ROOT/$EXP_ID/$SCENARIO/run_${RUN_ID}"
mkdir -p "$BASE_DIR"

if [[ -z "$OUT_FILE" ]]; then
  OUT_FILE="$BASE_DIR/distance_${DISTANCE_TAG}m.$FORMAT"
fi

META_FILE="$EXP_ROOT/$EXP_ID/meta.json"
MANIFEST_FILE="$BASE_DIR/manifest.json"
mkdir -p "$(dirname "$META_FILE")"
if [[ ! -f "$META_FILE" ]]; then
  python3 - \
    "$META_FILE" "$EXP_ID" "$TARGET_PROFILE" "$CHANNEL" \
    "$BANDWIDTH_MHZ" "$PACKET_RATE_HZ" "$TX_POWER_DBM" "$TARGET" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

meta_file, exp_id, target_profile, channel, bandwidth, rate, power, target = sys.argv[1:]
payload = {
    "exp_id": exp_id,
    "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "target_profile": target_profile,
    "channel": int(channel),
    "bandwidth_mhz": int(bandwidth),
    "packet_rate_hz": int(rate),
    "tx_power_dbm": power,
    "target": target,
    "notes": "ESP32-S3 CSI experiment (2.4 GHz only)",
}
Path(meta_file).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
fi

# shellcheck source=/dev/null
source "$IDF_PATH/export.sh" >/dev/null
cd "$RECV_DIR"

if [[ "$BUILD" -eq 1 ]]; then
  idf.py set-target "$TARGET"
  idf.py build
fi

if [[ "$FLASH" -eq 1 ]]; then
  idf.py -p "$PORT" -b "$BAUD" flash
fi

cd "$REPO_ROOT"
python3 -m csi_capture.capture \
  -p "$PORT" \
  -b "$BAUD" \
  -o "$OUT_FILE" \
  --format "$FORMAT" \
  --max-records "$MAX_RECORDS" \
  --exp-id "$EXP_ID" \
  --experiment-type distance \
  --scenario "$SCENARIO" \
  --run-id "$RUN_ID" \
  --trial-id "distance_${DISTANCE_TAG}m" \
  --device-path "$PORT" \
  --distance-m "$DISTANCE_M"

if [[ "$FORMAT" == "jsonl" ]]; then
  RECORDS_CAPTURED="$(wc -l < "$OUT_FILE" | tr -d ' ')"
else
  RECORDS_CAPTURED="$(( $(wc -l < "$OUT_FILE") - 1 ))"
fi

if [[ "$RECORDS_CAPTURED" -le 0 ]]; then
  echo "Error: no records captured in $OUT_FILE" >&2
  exit 1
fi

GIT_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
if [[ -n "$(git -C "$REPO_ROOT" status --porcelain 2>/dev/null)" ]]; then
  GIT_DIRTY=1
else
  GIT_DIRTY=0
fi

python3 - \
  "$MANIFEST_FILE" "$EXP_ID" "$TARGET_PROFILE" "$RUN_ID" \
  "distance_${DISTANCE_TAG}m" "$PORT" "$GIT_COMMIT" "$GIT_DIRTY" \
  "$OUT_FILE" "$RECORDS_CAPTURED" "$FORMAT" "$MAX_RECORDS" \
  "$SCENARIO" "$DISTANCE_M" "$CHANNEL" "$BANDWIDTH_MHZ" \
  "$PACKET_RATE_HZ" "$TX_POWER_DBM" "$TARGET" <<'PY'
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

(
    manifest_file, exp_id, target_profile, run_id, trial_id, port,
    git_commit, git_dirty, output_file, records_captured, output_format,
    max_records, scenario, distance_m, channel, bandwidth, rate, power, target,
) = sys.argv[1:]
manifest = {
    "exp_id": exp_id,
    "experiment_type": "distance",
    "target_profile": target_profile,
    "run_id": run_id,
    "trial_id": trial_id,
    "created_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    "device_path": port,
    "git_commit": git_commit,
    "git_dirty": bool(int(git_dirty)),
    "output_file": output_file,
    "records_captured": int(records_captured),
    "config_snapshot": {
        "format": output_format,
        "max_records": int(max_records),
        "scenario": scenario,
        "distance_m": float(distance_m),
        "channel": int(channel),
        "bandwidth_mhz": int(bandwidth),
        "packet_rate_hz": int(rate),
        "tx_power_dbm": power,
        "target": target,
        "target_profile": target_profile,
    },
}

Path(manifest_file).write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY

echo "RX capture complete: $RECORDS_CAPTURED records"
echo "Output: $OUT_FILE"
echo "Meta:   $META_FILE"
echo "Manifest: $MANIFEST_FILE"
