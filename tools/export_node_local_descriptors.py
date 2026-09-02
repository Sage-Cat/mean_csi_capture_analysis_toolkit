#!/usr/bin/env python3
"""Export deterministic compact descriptors from a local collector archive.

The tool is intentionally standard-library-only so the same exact file can run
on a Raspberry Pi and on the coordinating workstation.  Every output path must
be supplied by the experiment operator; this mean owns no experiment data.
"""

from __future__ import annotations

import argparse
import ast
import csv
import gzip
import hashlib
import json
import math
import platform
import resource
import socket
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Iterable


SCHEMA = "cws-node-local-descriptor-export/1"
ALGORITHM_VERSION = "wall-clock-2s-statistical-descriptor-v1"
FEATURE_ORDER = [
    "sample_count",
    "rssi_mean",
    "rssi_std",
    "rssi_p95_p05",
    "interval_mean_ms",
    "interval_std_ms",
    "interval_p95_p05_ms",
    "amplitude_mean",
    "amplitude_std",
    "amplitude_p95_p05",
    "preview_coverage_ratio",
    "local_confidence",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def percentile(values: list[float], probability: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    fraction = position - lower
    return float(ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction)


def spread(values: list[float]) -> float:
    return percentile(values, 0.95) - percentile(values, 0.05)


def population_std(values: list[float]) -> float:
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def parse_csi(raw: str) -> tuple[int, int, list[float]] | None:
    if not raw.startswith("CSI_DATA,"):
        return None
    try:
        fields = next(csv.reader([raw]))
        sequence = int(fields[1])
        rssi = int(fields[3])
        iq = ast.literal_eval(fields[-1])
    except (csv.Error, SyntaxError, ValueError, IndexError):
        return None
    if not isinstance(iq, list) or len(iq) < 2:
        return None
    try:
        numeric = [float(value) for value in iq]
    except (TypeError, ValueError):
        return None
    if len(numeric) % 2:
        numeric = numeric[:-1]
    if not numeric:
        return None
    amplitudes = [
        math.hypot(numeric[index], numeric[index + 1])
        for index in range(0, len(numeric), 2)
    ]
    return sequence, rssi, amplitudes


def iter_records(archive: Path) -> Iterable[dict[str, Any]]:
    chunks = sorted(archive.glob("sources/**/*.ndjson.gz"))
    if not chunks:
        raise ValueError(f"no collector source chunks under {archive}")
    for chunk in chunks:
        with gzip.open(chunk, "rt", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON in {chunk}:{line_number}") from exc
                if isinstance(value, dict):
                    yield value


def descriptor(window: list[dict[str, Any]], *, expected_rate_hz: float, window_ms: int) -> dict[str, Any]:
    timestamps_ns = [int(item["ingest_wall_time_ns"]) for item in window]
    rssi = [float(item["rssi"]) for item in window]
    amplitudes = [value for item in window for value in item["amplitudes"]]
    intervals_ms = [
        (timestamps_ns[index] - timestamps_ns[index - 1]) / 1_000_000.0
        for index in range(1, len(timestamps_ns))
    ]
    complex_count = len(amplitudes)
    nonzero_count = sum(value > 0.0 for value in amplitudes)
    coverage = nonzero_count / complex_count if complex_count else 0.0
    expected_samples = expected_rate_hz * (window_ms / 1000.0)
    completeness = min(1.0, len(window) / expected_samples) if expected_samples > 0 else 0.0
    features = {
        "sample_count": len(window),
        "rssi_mean": statistics.fmean(rssi),
        "rssi_std": population_std(rssi),
        "rssi_p95_p05": spread(rssi),
        "interval_mean_ms": statistics.fmean(intervals_ms) if intervals_ms else 0.0,
        "interval_std_ms": population_std(intervals_ms),
        "interval_p95_p05_ms": spread(intervals_ms),
        "amplitude_mean": statistics.fmean(amplitudes),
        "amplitude_std": population_std(amplitudes),
        "amplitude_p95_p05": spread(amplitudes),
        "preview_coverage_ratio": coverage,
        "local_confidence": completeness * coverage,
    }
    return {
        "window_start_wall_time_ns": timestamps_ns[0],
        "window_end_wall_time_ns": timestamps_ns[-1],
        "first_device_sequence": window[0]["device_sequence"],
        "last_device_sequence": window[-1]["device_sequence"],
        "feature_order": FEATURE_ORDER,
        "features": [features[name] for name in FEATURE_ORDER],
    }


def build_descriptors(
    archive: Path,
    *,
    source_id: str,
    run_id: str,
    node_id: str,
    profile: str,
    window_ms: int,
    expected_rate_hz: float,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    windows: dict[tuple[str, int, int], list[dict[str, Any]]] = {}
    raw_records = 0
    malformed_csi = 0
    selected_csi = 0
    window_ns = window_ms * 1_000_000
    for record in iter_records(archive):
        raw_records += 1
        if record.get("source_id") != source_id:
            continue
        raw = record.get("raw")
        if not isinstance(raw, str) or not raw.startswith("CSI_DATA,"):
            continue
        parsed = parse_csi(raw)
        timestamp_ns = record.get("ingest_wall_time_ns")
        if parsed is None or not isinstance(timestamp_ns, int):
            malformed_csi += 1
            continue
        sequence, rssi, amplitudes = parsed
        session_id = str(record.get("session_id", ""))
        connection_epoch = int(record.get("connection_epoch", 0))
        bucket = timestamp_ns // window_ns
        windows.setdefault((session_id, connection_epoch, bucket), []).append({
            "ingest_wall_time_ns": timestamp_ns,
            "device_sequence": sequence,
            "rssi": rssi,
            "amplitudes": amplitudes,
        })
        selected_csi += 1
    output: list[dict[str, Any]] = []
    for index, key in enumerate(sorted(windows)):
        rows = sorted(windows[key], key=lambda item: (item["ingest_wall_time_ns"], item["device_sequence"]))
        value = descriptor(rows, expected_rate_hz=expected_rate_hz, window_ms=window_ms)
        value.update({
            "schema_version": SCHEMA,
            "algorithm_version": ALGORITHM_VERSION,
            "run_id": run_id,
            "node_id": node_id,
            "source_id": source_id,
            "profile": profile,
            "session_id": key[0],
            "connection_epoch": key[1],
            "window_index": index,
            "window_nominal_start_ns": key[2] * window_ns,
            "window_nominal_end_ns": (key[2] + 1) * window_ns,
        })
        output.append(value)
    if not output:
        raise ValueError("no descriptors were produced from selected CSI records")
    return output, {
        "raw_archive_records": raw_records,
        "selected_csi_records": selected_csi,
        "malformed_selected_csi_records": malformed_csi,
        "descriptor_windows": len(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--profile", default="L1")
    parser.add_argument("--window-ms", type=int, default=2000)
    parser.add_argument("--expected-rate-hz", type=float, default=40.0)
    parser.add_argument(
        "--execution-boundary",
        choices=("physical-node-local", "central-replay"),
        required=True,
    )
    args = parser.parse_args()
    if args.window_ms <= 0 or args.expected_rate_hz <= 0:
        parser.error("window and expected rate must be positive")
    if not args.archive.is_dir():
        parser.error("archive must be an existing collector archive directory")
    if args.output.exists():
        parser.error("output path already exists; outputs are immutable")
    if args.output.resolve().is_relative_to(args.archive.resolve()):
        parser.error("output must not be inside the immutable input archive")

    started_wall_ns = time.time_ns()
    started_cpu_ns = time.process_time_ns()
    descriptors, counts = build_descriptors(
        args.archive,
        source_id=args.source_id,
        run_id=args.run_id,
        node_id=args.node_id,
        profile=args.profile,
        window_ms=args.window_ms,
        expected_rate_hz=args.expected_rate_hz,
    )
    args.output.mkdir(parents=True, exist_ok=False)
    descriptor_path = args.output / "descriptors.ndjson"
    with descriptor_path.open("x", encoding="utf-8") as handle:
        for value in descriptors:
            handle.write(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
    ended_cpu_ns = time.process_time_ns()
    ended_wall_ns = time.time_ns()
    input_files = sorted(path for path in args.archive.rglob("*") if path.is_file())
    manifest = {
        "schema_version": SCHEMA,
        "algorithm_version": ALGORITHM_VERSION,
        "claim_eligible": False,
        "execution_boundary": args.execution_boundary,
        "executed_hostname": socket.gethostname(),
        "machine": platform.machine(),
        "python_version": platform.python_version(),
        "source_id": args.source_id,
        "node_id": args.node_id,
        "run_id": args.run_id,
        "profile": args.profile,
        "timestamp_basis": "collector ingest_wall_time_ns",
        "window_ms": args.window_ms,
        "window_alignment": "UTC epoch multiples; session and connection boundaries are separate",
        "expected_rate_hz": args.expected_rate_hz,
        "missing_value_policy": "malformed selected CSI records are counted and excluded; no imputation",
        "feature_order": FEATURE_ORDER,
        "input_archive_path": str(args.archive.resolve()),
        "input_file_count": len(input_files),
        "input_bytes": sum(path.stat().st_size for path in input_files),
        "input_manifest_sha256": sha256(args.archive / "manifest.json") if (args.archive / "manifest.json").is_file() else None,
        "input_checksums_sha256": sha256(args.archive / "SHA256SUMS") if (args.archive / "SHA256SUMS").is_file() else None,
        "extractor_sha256": sha256(Path(__file__).resolve()),
        "descriptor_sha256": sha256(descriptor_path),
        "descriptor_bytes": descriptor_path.stat().st_size,
        "counts": counts,
        "resource_accounting": {
            "wall_time_ns": ended_wall_ns - started_wall_ns,
            "cpu_time_ns": ended_cpu_ns - started_cpu_ns,
            "maximum_resident_set_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        },
    }
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "ok": True,
        "execution_boundary": manifest["execution_boundary"],
        "descriptor_windows": counts["descriptor_windows"],
        "descriptor_sha256": manifest["descriptor_sha256"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
