from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path
from typing import IO, Iterable, Iterator, Optional, TextIO

from csi_capture.core.device import DeviceAccessError, validate_serial_device_access
from csi_capture.parser import CSIRecord, parse_csi_line


class SerialPortAccessError(RuntimeError):
    """Raised when a serial port path is missing or inaccessible."""


def _record_to_dict(record: CSIRecord, metadata: Optional[dict] = None) -> dict:
    row = {
        "timestamp": record.timestamp,
        "rssi": record.rssi,
        "csi": record.csi,
        "esp_timestamp": record.esp_timestamp,
        "mac": record.mac,
    }
    if metadata:
        row.update(metadata)
    return row


def _write_jsonl(f: TextIO, record: CSIRecord, metadata: Optional[dict] = None) -> None:
    f.write(json.dumps(_record_to_dict(record, metadata=metadata), separators=(",", ":")) + "\n")


def _write_csv(writer: csv.DictWriter, record: CSIRecord, metadata: Optional[dict] = None) -> None:
    row = _record_to_dict(record, metadata=metadata)
    row["csi"] = json.dumps(record.csi, separators=(",", ":"))
    writer.writerow(row)


def capture_stream(
    lines: Iterable[str],
    out: IO[str],
    output_format: str = "jsonl",
    max_records: Optional[int] = None,
    metadata: Optional[dict] = None,
) -> int:
    if output_format not in {"jsonl", "csv"}:
        raise ValueError(f"Unsupported output_format: {output_format}")
    if max_records is not None and max_records < 0:
        raise ValueError("max_records must be >= 0")

    csv_writer = None
    if output_format == "csv":
        fieldnames = ["timestamp", "rssi", "csi", "esp_timestamp", "mac"]
        if metadata:
            for key in metadata.keys():
                if key not in fieldnames:
                    fieldnames.append(key)
        csv_writer = csv.DictWriter(
            out, fieldnames=fieldnames
        )
        csv_writer.writeheader()

    written = 0
    for line in lines:
        if max_records is not None and written >= max_records:
            break

        ts = int(time.time() * 1000)
        record = parse_csi_line(line, timestamp=ts)
        if record is None:
            continue

        if output_format == "jsonl":
            _write_jsonl(out, record, metadata=metadata)
        else:
            if csv_writer is None:
                raise RuntimeError("CSV writer was not initialized")
            _write_csv(csv_writer, record, metadata=metadata)

        written += 1

    return written


def ensure_serial_port_access(port: str) -> None:
    """Validate that a serial device is available for the current platform."""
    try:
        validate_serial_device_access(port)
    except DeviceAccessError as exc:
        raise SerialPortAccessError(str(exc)) from exc


def serial_lines(
    port: str,
    baud: int,
    timeout: float = 1.0,
    reconnect_on_error: bool = False,
    reconnect_delay_s: float = 1.0,
    yield_on_timeout: bool = False,
) -> Iterator[str]:
    """Yield decoded serial lines with timeout and optional reconnect handling."""
    # Import here so parser tests run without serial dependency.
    try:
        import serial
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency 'pyserial' for this Python interpreter. "
            "Install with: python3 -m pip install pyserial "
            "or install OS package (Linux apt: python3-serial, macOS brew: pyserial)"
        ) from exc

    while True:
        try:
            with serial.Serial(port=port, baudrate=baud, timeout=timeout) as ser:
                while True:
                    raw = ser.readline()
                    if not raw:
                        if yield_on_timeout:
                            yield ""
                        continue
                    yield raw.decode("utf-8", errors="replace")
        except serial.SerialException as exc:
            if not reconnect_on_error:
                raise RuntimeError(f"Serial connection error on {port}: {exc}") from exc
            time.sleep(reconnect_delay_s)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Capture ESP CSI_DATA into structured timestamp,rssi,csi records."
    )
    parser.add_argument("-p", "--port", required=True, help="Serial port, e.g. /dev/ttyACM1 or COM4")
    parser.add_argument("-b", "--baud", type=int, default=921600, help="Serial baud rate")
    parser.add_argument(
        "-o",
        "--output",
        default="csi_capture.jsonl",
        help="Output file path (jsonl or csv)",
    )
    parser.add_argument(
        "--format",
        choices=["jsonl", "csv"],
        default="jsonl",
        help="Output format",
    )
    parser.add_argument(
        "--max-records",
        type=int,
        default=None,
        help="Stop after N parsed CSI records",
    )
    parser.add_argument("--exp-id", default=None, help="Experiment identifier")
    parser.add_argument(
        "--experiment-type", default=None, help="Experiment type (e.g. distance, angle)"
    )
    parser.add_argument("--scenario", default=None, help="Scenario label (LoS/NLoS_*)")
    parser.add_argument("--run-id", type=int, default=None, help="Run index, e.g. 1..3")
    parser.add_argument("--trial-id", default=None, help="Trial identifier")
    parser.add_argument("--distance-m", type=float, default=None, help="Ground-truth distance in meters")
    parser.add_argument(
        "--device-path",
        default=None,
        help="Device identifier to store in output metadata (defaults to --port if omitted)",
    )
    args = parser.parse_args()

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        ensure_serial_port_access(args.port)
    except SerialPortAccessError as err:
        print(f"Error: {err}")
        return 2

    print(
        f"Capturing CSI from {args.port} @ {args.baud}. "
        f"Writing {args.format} to {output_path}. Press Ctrl+C to stop."
    )

    metadata = {
        "exp_id": args.exp_id,
        "experiment_type": args.experiment_type,
        "scenario": args.scenario,
        "run_id": args.run_id,
        "trial_id": args.trial_id,
        "distance_m": args.distance_m,
        "device_path": args.device_path,
    }
    metadata = {k: v for k, v in metadata.items() if v is not None}

    written = 0
    try:
        with output_path.open("w", encoding="utf-8", newline="") as out:
            written = capture_stream(
                serial_lines(args.port, args.baud),
                out=out,
                output_format=args.format,
                max_records=args.max_records,
                metadata=metadata,
            )
    except RuntimeError as err:
        print(f"Error: {err}")
        return 2
    except KeyboardInterrupt:
        pass

    print(f"Done. Records captured: {written}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
