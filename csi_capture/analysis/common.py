from __future__ import annotations

import base64
import binascii
import json
import re
import tomllib
from pathlib import Path
from typing import Any, Iterator

import numpy as np
import pandas as pd

DATA_SUFFIXES = {".csv", ".jsonl", ".json", ".txt"}
SCENARIO_CANONICAL = {
    "los": "LoS",
    "nlos": "NLoS",
    "nlos_furniture": "NLoS_furniture",
    "nlos_human": "NLoS_human",
    "nlos_wall": "NLoS_wall",
}
ANGLE_TOKEN_RE = re.compile(r"(?:^|[_-])ang(?:le)?[_-]?([mp]?\d+(?:p\d+)?)\b", flags=re.IGNORECASE)
ANGLE_TAG_RE = re.compile(r"(?:^|[_-])ang(?:le)?[_-]?[mp]?\d+(?:p\d+)?\b", flags=re.IGNORECASE)


def normalize_scenario(raw: Any) -> str:
    if raw is None:
        return "unknown"
    text = str(raw).strip()
    if not text:
        return "unknown"
    key = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")
    if "nlos" in key:
        if "furniture" in key:
            return SCENARIO_CANONICAL["nlos_furniture"]
        if "human" in key or "person" in key:
            return SCENARIO_CANONICAL["nlos_human"]
        if "wall" in key:
            return SCENARIO_CANONICAL["nlos_wall"]
        return SCENARIO_CANONICAL["nlos"]
    if "los" in key:
        return SCENARIO_CANONICAL["los"]
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")


def discover_files(data_dir: Path, suffixes: set[str] | None = None) -> list[Path]:
    allowed = suffixes or DATA_SUFFIXES
    if not data_dir.exists():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")
    files = sorted(p for p in data_dir.rglob("*") if p.is_file() and p.suffix.lower() in allowed)
    if not files:
        raise FileNotFoundError(
            f"No supported data files ({sorted(allowed)}) found under: {data_dir}"
        )
    return files


def discover_test_case_files(
    runs_dir: Path,
    test_case_id: str,
    suffixes: set[str] | None = None,
) -> list[Path]:
    """Discover data only in direct runs assigned to one experiment test case."""

    if not runs_dir.is_dir():
        raise FileNotFoundError(f"Runs directory not found: {runs_dir}")
    selected_runs: list[Path] = []
    for run_dir in sorted(path for path in runs_dir.iterdir() if path.is_dir()):
        manifest_path = run_dir / "run.toml"
        try:
            with manifest_path.open("rb") as stream:
                manifest = tomllib.load(stream)
        except (OSError, tomllib.TOMLDecodeError) as error:
            raise ValueError(f"Invalid run manifest: {manifest_path}") from error
        if manifest.get("test_case_id") == test_case_id:
            selected_runs.append(run_dir)
    if not selected_runs:
        raise FileNotFoundError(
            f"Unknown test case {test_case_id!r} under: {runs_dir}"
        )
    allowed = suffixes or DATA_SUFFIXES
    selected = sorted(
        path
        for run_dir in selected_runs
        for path in run_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in allowed
    )
    if not selected:
        raise FileNotFoundError(
            f"No supported data files ({sorted(allowed)}) found for test case "
            f"{test_case_id!r} under: {runs_dir}"
        )
    return selected


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        frame = pd.read_csv(path)
        for row in frame.to_dict(orient="records"):
            yield row
        return

    if suffix == ".json":
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        if isinstance(data, list):
            for row in data:
                if isinstance(row, dict):
                    yield row
        elif isinstance(data, dict):
            records = data.get("records")
            if isinstance(records, list):
                for row in records:
                    if isinstance(row, dict):
                        yield row
            else:
                yield data
        return

    non_empty_lines: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if line:
                non_empty_lines.append(line)
    if not non_empty_lines:
        return

    if non_empty_lines[0].startswith("{"):
        for line in non_empty_lines:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row
        return

    frame = pd.read_csv(path)
    for row in frame.to_dict(orient="records"):
        yield row


def infer_distance_from_path(path: Path) -> float | None:
    match = re.search(r"distance[_-](\d+(?:p\d+)?)m", path.name.lower())
    if not match:
        return None
    token = match.group(1).replace("p", ".")
    try:
        return float(token)
    except ValueError:
        return None


def infer_run_id_from_path(path: Path) -> str | None:
    for part in path.parts:
        match = re.fullmatch(r"run[_-]?([A-Za-z0-9]+)", part, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def infer_scenario_from_path(path: Path) -> str | None:
    for part in path.parts:
        normalized = normalize_scenario(part)
        if normalized != "unknown" and (normalized == "LoS" or normalized.startswith("NLoS")):
            return normalized
    return None


def parse_numeric_array(value: Any) -> np.ndarray | None:
    if isinstance(value, np.ndarray):
        if value.ndim != 1:
            return None
        return value.astype(np.float32, copy=False)
    if isinstance(value, (list, tuple)) and value:
        try:
            return np.asarray(value, dtype=np.float32)
        except (TypeError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.startswith("[") and text.endswith("]"):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return None
            return parse_numeric_array(parsed)
    return None


def decode_payload_bytes(
    payload: bytes, csi_len_hint: int | None = None, bit_hint: int | None = None
) -> np.ndarray:
    if not payload:
        raise ValueError("CSI payload is empty")
    if bit_hint in (8, 16):
        widths = [bit_hint // 8]
    else:
        widths = [1, 2]
    candidates: list[tuple[int, np.ndarray, int]] = []
    for width in widths:
        pair_bytes = width * 2
        if len(payload) % pair_bytes != 0:
            continue
        dtype = np.int8 if width == 1 else np.dtype("<i2")
        values = np.frombuffer(payload, dtype=dtype).astype(np.float32)
        score = 0
        if csi_len_hint is not None:
            if csi_len_hint == len(payload):
                score += 3
            if csi_len_hint == len(values):
                score += 2
            if csi_len_hint == len(values) // 2:
                score += 2
        candidates.append((score, values, width))
    if not candidates:
        raise ValueError(f"Cannot infer CSI payload width for {len(payload)} bytes.")
    candidates.sort(key=lambda item: (item[0], item[2]), reverse=True)
    return candidates[0][1]


def parse_csi_interleaved(record: dict[str, Any]) -> np.ndarray:
    for key in ("csi", "csi_iq", "csi_values"):
        if key in record:
            arr = parse_numeric_array(record[key])
            if arr is not None and arr.size > 0:
                return arr

    bit_hint: int | None = None
    for key in ("csi_bits", "iq_bits", "sample_bits"):
        if key in record:
            try:
                bit_hint = int(record[key])
                break
            except (TypeError, ValueError):
                pass

    csi_len_hint: int | None = None
    if "csi_len" in record:
        try:
            csi_len_hint = int(record["csi_len"])
        except (TypeError, ValueError):
            csi_len_hint = None

    if "csi_iq_hex" in record and record["csi_iq_hex"] not in (None, ""):
        raw_hex = str(record["csi_iq_hex"]).strip().replace(" ", "")
        payload = bytes.fromhex(raw_hex)
        return decode_payload_bytes(payload, csi_len_hint=csi_len_hint, bit_hint=bit_hint)

    if "csi_iq_base64" in record and record["csi_iq_base64"] not in (None, ""):
        raw_b64 = str(record["csi_iq_base64"]).strip()
        try:
            payload = base64.b64decode(raw_b64, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"Invalid csi_iq_base64 payload: {exc}") from exc
        return decode_payload_bytes(payload, csi_len_hint=csi_len_hint, bit_hint=bit_hint)

    raise ValueError(
        "Missing CSI payload. Expected one of csi/csi_iq/csi_values/csi_iq_hex/csi_iq_base64."
    )


def strip_angle_tag(text: str) -> str:
    stripped = ANGLE_TAG_RE.sub("", text)
    stripped = re.sub(r"[_-]{2,}", "_", stripped)
    return stripped.strip("_-")


def scenario_base_from_text(raw: Any) -> str:
    if raw is None:
        return "unknown"
    text = str(raw).strip()
    if not text:
        return "unknown"
    base_candidate = strip_angle_tag(text)
    if not base_candidate:
        base_candidate = text
    return normalize_scenario(base_candidate)


def parse_angle_token(token: str) -> float | None:
    token = token.strip().lower()
    if not token:
        return None
    sign = 1.0
    if token.startswith("m"):
        sign = -1.0
        token = token[1:]
    elif token.startswith("p"):
        token = token[1:]

    if not token:
        return None
    numeric = token.replace("p", ".")
    try:
        return sign * float(numeric)
    except ValueError:
        return None


def extract_angle_from_text(raw: Any) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    match = ANGLE_TOKEN_RE.search(text)
    if not match:
        return None
    return parse_angle_token(match.group(1))


def infer_angle_from_path(path: Path) -> float | None:
    candidates = list(path.parts) + [path.stem, path.name]
    for part in candidates:
        angle = extract_angle_from_text(part)
        if angle is not None:
            return angle

    match = re.search(r"angle[_-]([mp]?\d+(?:p\d+)?)", path.name.lower())
    if match:
        return parse_angle_token(match.group(1))
    return None
