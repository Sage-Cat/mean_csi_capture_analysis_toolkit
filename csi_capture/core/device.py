from __future__ import annotations

import glob
import os
import platform
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

DEFAULT_SERIAL_DEVICE = "/dev/esp32_csi"
DEVICE_ENV_VARS: Sequence[str] = ("CSI_CAPTURE_DEVICE", "ESP32_CSI_DEVICE")
SERIAL_GLOB_PATTERNS: Sequence[str] = (
    "/dev/ttyACM*",
    "/dev/ttyUSB*",
    "/dev/tty.usbmodem*",
    "/dev/cu.usbmodem*",
    "/dev/tty.usbserial*",
    "/dev/cu.usbserial*",
)
WINDOWS_COM_PORT_RE = re.compile(r"^(?:\\\\\.\\)?(COM\d+)$", re.IGNORECASE)


class DeviceAccessError(RuntimeError):
    """Raised when serial device discovery/access checks fail."""


@dataclass(frozen=True)
class ResolvedDevice:
    path: str
    realpath: str
    source: str


def _safe_realpath(path: str) -> str:
    if _is_windows_com_port(path):
        return _normalize_windows_port(path)
    try:
        return str(Path(path).resolve(strict=False))
    except OSError:
        return os.path.realpath(path)


def _normalize_windows_port(path: str) -> str:
    match = WINDOWS_COM_PORT_RE.match(path.strip())
    if match is None:
        return path.strip()
    return match.group(1).upper()


def _is_windows_com_port(path: str) -> bool:
    return WINDOWS_COM_PORT_RE.match(path.strip()) is not None


def _list_pyserial_candidates() -> list[str]:
    if platform.system() != "Windows":
        return []

    try:
        from serial.tools import list_ports
    except ModuleNotFoundError:
        return []

    candidates: list[str] = []
    for port in list_ports.comports():
        device = str(getattr(port, "device", "") or "").strip()
        if not device:
            continue
        device = _normalize_windows_port(device)
        if device not in candidates:
            candidates.append(device)
    return candidates


def resolve_serial_device(
    cli_device: str | None,
    env: Mapping[str, str] | None = None,
    default: str = DEFAULT_SERIAL_DEVICE,
) -> ResolvedDevice:
    env_map = env if env is not None else os.environ
    cli_value = cli_device.strip() if cli_device is not None else ""
    if cli_value and cli_value.lower() != "auto":
        selected = cli_value
        source = "cli"
    else:
        selected = ""
        source = "default"
        for key in DEVICE_ENV_VARS:
            value = env_map.get(key, "").strip()
            if value:
                selected = value
                source = f"env:{key}"
                break
        if not selected:
            if os.path.exists(default):
                selected = default
            else:
                candidates = list_serial_candidates()
                if candidates:
                    selected = candidates[0]
                    source = "auto"
                else:
                    selected = default

    return ResolvedDevice(path=selected, realpath=_safe_realpath(selected), source=source)


def list_serial_candidates() -> list[str]:
    candidates: list[str] = []
    if os.path.exists(DEFAULT_SERIAL_DEVICE):
        candidates.append(DEFAULT_SERIAL_DEVICE)

    for pattern in SERIAL_GLOB_PATTERNS:
        for path in sorted(glob.glob(pattern)):
            if path not in candidates:
                candidates.append(path)

    for path in _list_pyserial_candidates():
        if path not in candidates:
            candidates.append(path)

    return candidates


def validate_serial_device_access(path: str) -> None:
    if platform.system() == "Windows":
        normalized = _normalize_windows_port(path)
        known_ports = {_normalize_windows_port(candidate) for candidate in _list_pyserial_candidates()}
        if normalized in known_ports:
            return
        raise DeviceAccessError(
            f"serial device does not exist or is not enumerated: {path}\n"
            "Windows fix:\n"
            "  1) confirm Device Manager shows the board under Ports (COM & LPT)\n"
            "  2) run --list-devices to inspect detected COM ports\n"
            "  3) retry with --device COMx"
        )

    if not os.path.exists(path):
        raise DeviceAccessError(
            f"serial device does not exist: {path}\n"
            "Use --list-devices to inspect candidates."
        )
    if not os.access(path, os.R_OK | os.W_OK):
        if platform.system() == "Darwin":
            raise DeviceAccessError(
                f"serial device exists but is not read/write for current user: {path}\n"
                "macOS fix:\n"
                "  1) close serial monitors that may lock the port\n"
                "  2) check owner/group: ls -l <device>\n"
                "  3) re-plug the board and retry\n"
            )
        raise DeviceAccessError(
            f"serial device exists but is not read/write for current user: {path}\n"
            "Linux fix:\n"
            "  1) sudo usermod -a -G dialout $USER\n"
            "  2) log out and log in again\n"
            "  3) verify with: id -nG"
        )


def format_device_banner(device: ResolvedDevice) -> str:
    return (
        f"Serial device: {device.path}\n"
        f"Resolved path: {device.realpath}\n"
        f"Selection source: {device.source}"
    )
