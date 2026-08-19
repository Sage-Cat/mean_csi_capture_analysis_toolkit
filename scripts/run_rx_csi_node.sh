#!/usr/bin/env bash
set -euo pipefail

PORT=""
BAUD="921600"
TARGET="esp32s3"
BUILD=1
FLASH=1
MONITOR=0

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
IDF_PATH_DEFAULT="$REPO_ROOT/../../my-inventory/toolchains/esp-idf-v5.5.2"
ESP_CSI_PATH_DEFAULT="$REPO_ROOT/../../my-inventory/helpers/firmware/esp-csi"
IDF_PATH="${IDF_PATH:-$IDF_PATH_DEFAULT}"
ESP_CSI_PATH="${ESP_CSI_PATH:-$ESP_CSI_PATH_DEFAULT}"

usage() {
  cat <<'USAGE'
RX node runner (ESP32 csi_recv firmware only).

Usage:
  scripts/run_rx_csi_node.sh [options]

Options:
  --port <path>          Serial port (default: auto-detect, prefers /dev/esp32_csi)
  --baud <num>           Flash/monitor baud (default: 921600)
  --target <chip>        IDF target (default: esp32s3)
  --idf-path <path>      ESP-IDF path (default: $HOME/esp/esp-idf)
  --esp-csi-path <path>  esp-csi path (default: $HOME/esp/esp-csi)
  --skip-build           Do not run idf.py build
  --skip-flash           Do not run idf.py flash
  --monitor              Start idf.py monitor after flashing
  -h, --help             Show this help
USAGE
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
    --port) PORT="$2"; shift 2 ;;
    --baud) BAUD="$2"; shift 2 ;;
    --target) TARGET="$2"; shift 2 ;;
    --idf-path) IDF_PATH="$2"; shift 2 ;;
    --esp-csi-path) ESP_CSI_PATH="$2"; shift 2 ;;
    --skip-build) BUILD=0; shift ;;
    --skip-flash) FLASH=0; shift ;;
    --monitor) MONITOR=1; shift ;;
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

if [[ ! -f "$IDF_PATH/export.sh" ]]; then
  echo "ESP-IDF export script not found: $IDF_PATH/export.sh" >&2
  exit 2
fi

RECV_DIR="$ESP_CSI_PATH/examples/get-started/csi_recv"
if [[ ! -d "$RECV_DIR" ]]; then
  echo "csi_recv directory not found: $RECV_DIR" >&2
  exit 2
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

echo "RX node ready on $PORT (target=$TARGET). csi_recv should be streaming CSI_DATA now."

if [[ "$MONITOR" -eq 1 ]]; then
  idf.py -p "$PORT" -b "$BAUD" monitor
fi
