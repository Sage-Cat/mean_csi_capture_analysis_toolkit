from __future__ import annotations

import csv
import json
import math
import platform
import re
import shutil
import socket
import subprocess
import time
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence


CHANNELS_24_GHZ: tuple[int, ...] = tuple(range(1, 14))
NON_OVERLAPPING_24_GHZ: tuple[int, ...] = (1, 6, 11)


class RadioSurveyError(RuntimeError):
    """Raised when Wi-Fi radio-state capture fails."""


@dataclass(frozen=True)
class APObservation:
    sample_index: int
    captured_at_utc: str
    scanner: str
    ssid: str
    bssid: str
    channel: int
    frequency_mhz: int | None
    signal_dbm: float
    signal_dbm_estimated: bool
    signal_quality_pct: int | None
    security: str
    radio_type: str | None
    rate_mbps: float | None
    in_use: bool
    raw_signal: str | None = None

    @property
    def hidden_ssid(self) -> bool:
        return not self.ssid


@dataclass(frozen=True)
class ScanSample:
    sample_index: int
    captured_at_utc: str
    scanner: str
    command: tuple[str, ...]
    interface: str | None
    observations: tuple[APObservation, ...]
    raw_output: str


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def default_report_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"radio_state_24ghz_{stamp}"


def signal_percent_to_dbm(signal_pct: int) -> float:
    signal_pct = max(0, min(100, int(signal_pct)))
    return -100.0 + (signal_pct / 2.0)


def channel_to_frequency_mhz(channel: int) -> int | None:
    if 1 <= channel <= 13:
        return 2407 + (channel * 5)
    if channel == 14:
        return 2484
    return None


def frequency_to_channel(freq_mhz: int) -> int | None:
    if freq_mhz == 2484:
        return 14
    if 2412 <= freq_mhz <= 2472 and (freq_mhz - 2407) % 5 == 0:
        return (freq_mhz - 2407) // 5
    return None


def channel_overlap_weight(channel_a: int, channel_b: int) -> float:
    delta = abs(channel_a - channel_b)
    if delta > 4:
        return 0.0
    return 1.0 - (delta / 5.0)


def dbm_to_mw(signal_dbm: float) -> float:
    return 10.0 ** (signal_dbm / 10.0)


def split_nmcli_escaped_fields(line: str, separator: str = ":") -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    escape = False

    for char in line:
        if escape:
            current.append(char)
            escape = False
            continue
        if char == "\\":
            escape = True
            continue
        if char == separator:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)

    parts.append("".join(current))
    return parts


def parse_nmcli_output(text: str, sample_index: int, captured_at_utc: str) -> list[APObservation]:
    observations: list[APObservation] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        fields = split_nmcli_escaped_fields(line)
        if len(fields) != 8:
            raise RadioSurveyError(
                f"Unexpected nmcli row shape: expected 8 fields, got {len(fields)} in {raw_line!r}"
            )

        in_use_raw, ssid, bssid, chan_raw, freq_raw, rate_raw, signal_raw, security = fields
        try:
            channel = int(chan_raw)
            signal_pct = int(signal_raw)
        except ValueError as exc:
            raise RadioSurveyError(f"Failed to parse nmcli row: {raw_line!r}") from exc

        if not (1 <= channel <= 14):
            continue

        freq_match = re.search(r"(\d+)", freq_raw)
        rate_match = re.search(r"(\d+(?:\.\d+)?)", rate_raw)
        frequency_mhz = int(freq_match.group(1)) if freq_match else channel_to_frequency_mhz(channel)
        rate_mbps = float(rate_match.group(1)) if rate_match else None
        observations.append(
            APObservation(
                sample_index=sample_index,
                captured_at_utc=captured_at_utc,
                scanner="nmcli",
                ssid=ssid,
                bssid=bssid.upper(),
                channel=channel,
                frequency_mhz=frequency_mhz,
                signal_dbm=signal_percent_to_dbm(signal_pct),
                signal_dbm_estimated=True,
                signal_quality_pct=signal_pct,
                security=security or "OPEN",
                radio_type=None,
                rate_mbps=rate_mbps,
                in_use=(in_use_raw == "*"),
                raw_signal=signal_raw,
            )
        )
    return observations


def parse_netsh_output(text: str, sample_index: int, captured_at_utc: str) -> list[APObservation]:
    observations: list[APObservation] = []
    current_ssid = ""
    current_auth = ""
    current_encryption = ""
    current_bssid: str | None = None
    current_signal_pct: int | None = None
    current_radio_type: str | None = None
    current_channel: int | None = None

    def finalize_current_bssid() -> None:
        nonlocal current_bssid
        nonlocal current_signal_pct
        nonlocal current_radio_type
        nonlocal current_channel

        if current_bssid is None or current_signal_pct is None or current_channel is None:
            return
        if not (1 <= current_channel <= 14):
            current_bssid = None
            current_signal_pct = None
            current_radio_type = None
            current_channel = None
            return
        security_parts = [part for part in (current_auth, current_encryption) if part]
        observations.append(
            APObservation(
                sample_index=sample_index,
                captured_at_utc=captured_at_utc,
                scanner="netsh",
                ssid=current_ssid,
                bssid=current_bssid.upper(),
                channel=current_channel,
                frequency_mhz=channel_to_frequency_mhz(current_channel),
                signal_dbm=signal_percent_to_dbm(current_signal_pct),
                signal_dbm_estimated=True,
                signal_quality_pct=current_signal_pct,
                security=" / ".join(security_parts) if security_parts else "UNKNOWN",
                radio_type=current_radio_type,
                rate_mbps=None,
                in_use=False,
                raw_signal=f"{current_signal_pct}%",
            )
        )
        current_bssid = None
        current_signal_pct = None
        current_radio_type = None
        current_channel = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        ssid_match = re.match(r"SSID\s+\d+\s*:\s*(.*)$", line)
        if ssid_match:
            finalize_current_bssid()
            current_ssid = ssid_match.group(1).strip()
            current_auth = ""
            current_encryption = ""
            continue

        auth_match = re.match(r"Authentication\s*:\s*(.*)$", line)
        if auth_match:
            current_auth = auth_match.group(1).strip()
            continue

        encryption_match = re.match(r"Encryption\s*:\s*(.*)$", line)
        if encryption_match:
            current_encryption = encryption_match.group(1).strip()
            continue

        bssid_match = re.match(r"BSSID\s+\d+\s*:\s*(.*)$", line)
        if bssid_match:
            finalize_current_bssid()
            current_bssid = bssid_match.group(1).strip()
            continue

        signal_match = re.match(r"Signal\s*:\s*(\d+)%$", line)
        if signal_match:
            current_signal_pct = int(signal_match.group(1))
            continue

        radio_match = re.match(r"Radio type\s*:\s*(.*)$", line)
        if radio_match:
            current_radio_type = radio_match.group(1).strip()
            continue

        channel_match = re.match(r"Channel\s*:\s*(\d+)$", line)
        if channel_match:
            current_channel = int(channel_match.group(1))
            continue

    finalize_current_bssid()
    return observations


def detect_scanner(preferred: str = "auto") -> str:
    if preferred != "auto":
        return preferred

    system = platform.system()
    if system == "Windows":
        if shutil.which("netsh"):
            return "netsh"
    elif system == "Linux":
        if shutil.which("nmcli"):
            return "nmcli"
    raise RadioSurveyError(
        f"Could not auto-detect a supported scanner on {system}. "
        "Use --scanner with a supported backend."
    )


def run_scan(scanner: str, interface: str | None = None, rescan: str = "yes") -> tuple[list[str], str]:
    if scanner == "nmcli":
        cmd = [
            "nmcli",
            "-t",
            "--escape",
            "yes",
            "-f",
            "IN-USE,SSID,BSSID,CHAN,FREQ,RATE,SIGNAL,SECURITY",
            "dev",
            "wifi",
            "list",
        ]
        if interface:
            cmd.extend(["ifname", interface])
        cmd.extend(["--rescan", rescan])
    elif scanner == "netsh":
        if interface:
            raise RadioSurveyError("--interface is not supported with the netsh backend.")
        cmd = ["netsh", "wlan", "show", "networks", "mode=bssid"]
    else:
        raise RadioSurveyError(f"Unsupported scanner backend: {scanner}")

    proc = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise RadioSurveyError(
            f"Scan command failed with exit code {proc.returncode}: {' '.join(cmd)}\n"
            f"stderr: {proc.stderr.strip()}"
        )
    return cmd, proc.stdout


def parse_scan_output(scanner: str, text: str, sample_index: int, captured_at_utc: str) -> list[APObservation]:
    if scanner == "nmcli":
        return parse_nmcli_output(text, sample_index=sample_index, captured_at_utc=captured_at_utc)
    if scanner == "netsh":
        return parse_netsh_output(text, sample_index=sample_index, captured_at_utc=captured_at_utc)
    raise RadioSurveyError(f"Unsupported scanner backend: {scanner}")


def capture_scan_samples(
    *,
    scanner: str,
    interface: str | None,
    rescan: str,
    samples: int,
    interval_s: float,
) -> list[ScanSample]:
    if samples <= 0:
        raise RadioSurveyError("samples must be > 0")
    if interval_s < 0.0:
        raise RadioSurveyError("interval_s must be >= 0")

    output: list[ScanSample] = []
    for sample_index in range(1, samples + 1):
        captured_at_utc = utc_now_iso()
        cmd, raw_output = run_scan(scanner=scanner, interface=interface, rescan=rescan)
        observations = parse_scan_output(
            scanner=scanner,
            text=raw_output,
            sample_index=sample_index,
            captured_at_utc=captured_at_utc,
        )
        output.append(
            ScanSample(
                sample_index=sample_index,
                captured_at_utc=captured_at_utc,
                scanner=scanner,
                command=tuple(cmd),
                interface=interface,
                observations=tuple(observations),
                raw_output=raw_output,
            )
        )
        if sample_index < samples and interval_s > 0.0:
            time.sleep(interval_s)
    return output


def observation_matches_experiment_ap(
    observation: APObservation,
    experiment_ssid: str | None,
    experiment_bssid: str | None,
) -> bool:
    ssid_match = bool(experiment_ssid) and observation.ssid == experiment_ssid
    bssid_match = bool(experiment_bssid) and observation.bssid.upper() == experiment_bssid.upper()
    return ssid_match or bssid_match


def summarize_radio_state(
    samples: Sequence[ScanSample],
    *,
    focus_channel: int,
    experiment_ssid: str | None = None,
    experiment_bssid: str | None = None,
    top_n: int = 10,
) -> dict:
    if not samples:
        raise RadioSurveyError("No scan samples were captured.")
    if focus_channel not in CHANNELS_24_GHZ:
        raise RadioSurveyError(f"focus_channel must be one of {CHANNELS_24_GHZ}.")

    all_observations = [obs for sample in samples for obs in sample.observations if 1 <= obs.channel <= 14]
    external_observations = [
        obs
        for obs in all_observations
        if not observation_matches_experiment_ap(obs, experiment_ssid, experiment_bssid)
    ]

    sample_count = len(samples)
    bssid_groups_all: dict[str, list[APObservation]] = defaultdict(list)
    bssid_groups_external: dict[str, list[APObservation]] = defaultdict(list)
    for obs in all_observations:
        bssid_groups_all[obs.bssid].append(obs)
    for obs in external_observations:
        bssid_groups_external[obs.bssid].append(obs)

    bssid_summaries = []
    for bssid, grouped in bssid_groups_external.items():
        grouped_sorted = sorted(grouped, key=lambda item: (item.sample_index, item.signal_dbm), reverse=True)
        ssid_counter = Counter(item.ssid for item in grouped_sorted)
        channel_counter = Counter(item.channel for item in grouped_sorted)
        security_counter = Counter(item.security for item in grouped_sorted if item.security)
        radio_counter = Counter(item.radio_type for item in grouped_sorted if item.radio_type)
        seen_samples = sorted({item.sample_index for item in grouped_sorted})
        signal_values = [item.signal_dbm for item in grouped_sorted]
        channel_mode = channel_counter.most_common(1)[0][0]
        overlap_score_focus = (
            dbm_to_mw(max(signal_values)) * channel_overlap_weight(channel_mode, focus_channel)
        )
        bssid_summaries.append(
            {
                "bssid": bssid,
                "ssid": ssid_counter.most_common(1)[0][0],
                "hidden_ssid": ssid_counter.most_common(1)[0][0] == "",
                "primary_channel": channel_mode,
                "channels_seen": sorted(channel_counter),
                "samples_seen": len(seen_samples),
                "presence_ratio": len(seen_samples) / sample_count,
                "signal_dbm_mean": sum(signal_values) / len(signal_values),
                "signal_dbm_max": max(signal_values),
                "signal_dbm_min": min(signal_values),
                "signal_dbm_std": statistics_std(signal_values),
                "signal_dbm_estimated": all(item.signal_dbm_estimated for item in grouped_sorted),
                "signal_quality_pct_mean": mean_or_none(
                    [item.signal_quality_pct for item in grouped_sorted if item.signal_quality_pct is not None]
                ),
                "security": security_counter.most_common(1)[0][0] if security_counter else "UNKNOWN",
                "radio_type": radio_counter.most_common(1)[0][0] if radio_counter else None,
                "rate_mbps_max": max((item.rate_mbps for item in grouped_sorted if item.rate_mbps is not None), default=None),
                "overlap_weight_to_focus_channel": channel_overlap_weight(channel_mode, focus_channel),
                "interference_score_focus": overlap_score_focus,
            }
        )

    bssid_summaries.sort(
        key=lambda item: (
            -float(item["interference_score_focus"]),
            -float(item["presence_ratio"]),
            -float(item["signal_dbm_max"]),
            item["bssid"],
        )
    )

    per_sample_channel_scores: dict[int, list[float]] = {channel: [] for channel in CHANNELS_24_GHZ}
    per_sample_exact_counts: dict[int, list[int]] = {channel: [] for channel in CHANNELS_24_GHZ}
    per_sample_exact_strongest: dict[int, list[float | None]] = {channel: [] for channel in CHANNELS_24_GHZ}
    sample_rows: list[dict] = []
    for sample in samples:
        sample_external = [
            obs
            for obs in sample.observations
            if 1 <= obs.channel <= 14
            and not observation_matches_experiment_ap(obs, experiment_ssid, experiment_bssid)
        ]
        sample_rows.append(
            {
                "sample_index": sample.sample_index,
                "captured_at_utc": sample.captured_at_utc,
                "ap_count_24ghz_all": len([obs for obs in sample.observations if 1 <= obs.channel <= 14]),
                "ap_count_24ghz_external": len(sample_external),
            }
        )
        for channel in CHANNELS_24_GHZ:
            exact = [obs for obs in sample_external if obs.channel == channel]
            overlap_score = sum(
                dbm_to_mw(obs.signal_dbm) * channel_overlap_weight(channel, obs.channel)
                for obs in sample_external
            )
            per_sample_channel_scores[channel].append(overlap_score)
            per_sample_exact_counts[channel].append(len(exact))
            per_sample_exact_strongest[channel].append(
                max((obs.signal_dbm for obs in exact), default=None)
            )

    channel_rows = []
    for channel in CHANNELS_24_GHZ:
        exact_bssids = [item for item in bssid_summaries if item["primary_channel"] == channel]
        adjacent_strongest = [
            item["signal_dbm_max"]
            for item in bssid_summaries
            if item["primary_channel"] != channel and channel_overlap_weight(channel, int(item["primary_channel"])) > 0.0
        ]
        channel_rows.append(
            {
                "channel": channel,
                "mean_exact_bssid_count": mean_or_zero(per_sample_exact_counts[channel]),
                "max_exact_bssid_count": max(per_sample_exact_counts[channel], default=0),
                "mean_overlap_score_mw": mean_or_zero(per_sample_channel_scores[channel]),
                "max_overlap_score_mw": max(per_sample_channel_scores[channel], default=0.0),
                "strongest_exact_signal_dbm": max(
                    (value for value in per_sample_exact_strongest[channel] if value is not None),
                    default=None,
                ),
                "strongest_adjacent_signal_dbm": max(adjacent_strongest, default=None),
                "persistent_exact_bssids": sum(
                    1 for item in exact_bssids if float(item["presence_ratio"]) >= 0.8
                ),
            }
        )

    sorted_by_overlap = sorted(channel_rows, key=lambda item: (item["mean_overlap_score_mw"], item["channel"]))
    recommended_lowest_overlap = sorted_by_overlap[0]["channel"]
    recommended_non_overlapping = min(
        (item for item in channel_rows if item["channel"] in NON_OVERLAPPING_24_GHZ),
        key=lambda item: (item["mean_overlap_score_mw"], item["channel"]),
    )["channel"]
    focus_row = next(item for item in channel_rows if item["channel"] == focus_channel)
    focus_rank = 1 + next(
        idx for idx, item in enumerate(sorted_by_overlap) if item["channel"] == focus_channel
    )

    out_of_plan_channels = sorted(
        {int(item["primary_channel"]) for item in bssid_summaries if int(item["primary_channel"]) not in NON_OVERLAPPING_24_GHZ}
    )
    hidden_external = sum(1 for item in bssid_summaries if bool(item["hidden_ssid"]))
    warnings: list[str] = []
    if not external_observations:
        warnings.append(
            "No external 2.4 GHz APs were detected. This may mean a clean environment or an incomplete scan path."
        )
    if len(bssid_summaries) >= 10:
        warnings.append(
            f"Dense 2.4 GHz environment: {len(bssid_summaries)} unique external BSSIDs were detected."
        )
    if focus_row["mean_exact_bssid_count"] >= 2.0:
        warnings.append(
            f"Focus channel {focus_channel} has mean exact-channel occupancy {focus_row['mean_exact_bssid_count']:.2f} external APs."
        )
    if focus_row["strongest_exact_signal_dbm"] is not None and focus_row["strongest_exact_signal_dbm"] >= -67.0:
        warnings.append(
            f"Strong co-channel interferer detected on channel {focus_channel}: "
            f"{focus_row['strongest_exact_signal_dbm']:.1f} dBm-equivalent."
        )
    if focus_row["strongest_adjacent_signal_dbm"] is not None and focus_row["strongest_adjacent_signal_dbm"] >= -67.0:
        warnings.append(
            f"Strong adjacent-channel interferer overlaps focus channel {focus_channel}: "
            f"{focus_row['strongest_adjacent_signal_dbm']:.1f} dBm-equivalent."
        )
    if out_of_plan_channels:
        warnings.append(
            "Adjacent-channel overlap risk is present because APs were observed on non-1/6/11 channels: "
            + ", ".join(str(channel) for channel in out_of_plan_channels)
        )
    if hidden_external >= 3:
        warnings.append(f"Multiple hidden external SSIDs detected: {hidden_external} persistent or transient hidden APs.")
    if any(obs.signal_dbm_estimated for obs in all_observations):
        warnings.append(
            "Signal dBm values are estimated from scanner quality metrics for this backend; treat absolute dBm as approximate."
        )

    report = {
        "generated_at_utc": utc_now_iso(),
        "host": {
            "node": socket.gethostname(),
            "platform": platform.platform(),
            "system": platform.system(),
            "release": platform.release(),
        },
        "scanner": {
            "kind": samples[0].scanner,
            "interface": samples[0].interface,
            "sample_count": sample_count,
            "signal_scale": (
                "estimated_dbm_from_quality_pct"
                if any(obs.signal_dbm_estimated for obs in all_observations)
                else "dbm"
            ),
            "commands": [list(sample.command) for sample in samples],
        },
        "focus": {
            "channel": focus_channel,
            "experiment_ssid": experiment_ssid,
            "experiment_bssid": experiment_bssid,
        },
        "summary": {
            "sample_count": sample_count,
            "ap_observations_24ghz_all": len(all_observations),
            "ap_observations_24ghz_external": len(external_observations),
            "unique_bssids_24ghz_all": len(bssid_groups_all),
            "unique_bssids_24ghz_external": len(bssid_groups_external),
            "unique_ssids_24ghz_external": len({item["ssid"] for item in bssid_summaries if item["ssid"]}),
            "hidden_ssids_external": hidden_external,
            "recommended_channel_lowest_overlap": recommended_lowest_overlap,
            "recommended_channel_non_overlapping": recommended_non_overlapping,
            "focus_channel_overlap_rank": focus_rank,
            "focus_channel_mean_overlap_score_mw": focus_row["mean_overlap_score_mw"],
            "focus_channel_mean_exact_bssid_count": focus_row["mean_exact_bssid_count"],
        },
        "warnings": warnings,
        "channels": channel_rows,
        "top_interferers": bssid_summaries[:top_n],
        "bssids": bssid_summaries,
        "samples": sample_rows,
    }
    return report


def mean_or_zero(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    return float(sum(values) / len(values))


def mean_or_none(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return float(sum(values) / len(values))


def statistics_std(values: Sequence[float]) -> float:
    if len(values) <= 1:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def render_markdown_report(report: dict) -> str:
    summary = report["summary"]
    focus = report["focus"]
    scanner = report["scanner"]
    lines = [
        "# 2.4 GHz Radio State Report",
        "",
        f"- Generated at UTC: `{report['generated_at_utc']}`",
        f"- Host: `{report['host']['node']}` on `{report['host']['system']}`",
        f"- Scanner: `{scanner['kind']}`",
        f"- Interface: `{scanner['interface'] or 'default'}`",
        f"- Samples: `{summary['sample_count']}`",
        f"- Focus channel: `{focus['channel']}`",
        f"- Experiment SSID filter: `{focus['experiment_ssid'] or 'not set'}`",
        f"- Experiment BSSID filter: `{focus['experiment_bssid'] or 'not set'}`",
        "",
        "## Summary",
        "",
        f"- External unique 2.4 GHz BSSIDs: `{summary['unique_bssids_24ghz_external']}`",
        f"- External 2.4 GHz AP observations: `{summary['ap_observations_24ghz_external']}`",
        f"- Hidden external SSIDs: `{summary['hidden_ssids_external']}`",
        f"- Recommended lowest-overlap channel: `{summary['recommended_channel_lowest_overlap']}`",
        f"- Recommended non-overlapping channel (1/6/11): `{summary['recommended_channel_non_overlapping']}`",
        f"- Focus-channel overlap rank: `{summary['focus_channel_overlap_rank']}`",
        f"- Focus-channel mean overlap score: `{summary['focus_channel_mean_overlap_score_mw']:.6f}` mW-equivalent",
        f"- Focus-channel mean exact-BSSID count: `{summary['focus_channel_mean_exact_bssid_count']:.3f}`",
        "",
        "## Warnings",
        "",
    ]
    if report["warnings"]:
        lines.extend(f"- {warning}" for warning in report["warnings"])
    else:
        lines.append("- No major 2.4 GHz crowding warnings were triggered.")

    lines.extend(
        [
            "",
            "## Channel Table",
            "",
            "| Channel | Mean Exact BSSIDs | Max Exact BSSIDs | Mean Overlap Score (mW-eq) | Strongest Exact (dBm-eq) | Strongest Adjacent (dBm-eq) |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in report["channels"]:
        lines.append(
            f"| {row['channel']} | {row['mean_exact_bssid_count']:.2f} | {row['max_exact_bssid_count']} | "
            f"{row['mean_overlap_score_mw']:.6f} | {format_optional_float(row['strongest_exact_signal_dbm'])} | "
            f"{format_optional_float(row['strongest_adjacent_signal_dbm'])} |"
        )

    lines.extend(
        [
            "",
            "## Top External Interferers",
            "",
            "| BSSID | SSID | Primary Channel | Presence Ratio | Mean Signal (dBm-eq) | Max Signal (dBm-eq) | Overlap Weight To Focus |",
            "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in report["top_interferers"]:
        ssid = row["ssid"] or "<hidden>"
        lines.append(
            f"| {row['bssid']} | {ssid} | {row['primary_channel']} | {row['presence_ratio']:.2f} | "
            f"{row['signal_dbm_mean']:.2f} | {row['signal_dbm_max']:.2f} | "
            f"{row['overlap_weight_to_focus_channel']:.2f} |"
        )

    lines.extend(
        [
            "",
            "## Method Notes",
            "",
            "- Overlap score uses linear-power weighting with a simple 2.4 GHz channel-overlap kernel across +/-4 channels.",
            "- If the backend only reports signal quality percent, the report stores dBm-equivalent estimates and marks them as approximate.",
            "- Recommended channel is a planning heuristic for CSI/RSSI experiments, not a regulatory or enterprise RF design output.",
        ]
    )
    return "\n".join(lines) + "\n"


def format_optional_float(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.2f}"


def write_report_artifacts(report_dir: Path, report: dict, samples: Sequence[ScanSample], save_raw: bool) -> dict[str, Path]:
    report_dir.mkdir(parents=True, exist_ok=False)
    json_path = report_dir / "report.json"
    md_path = report_dir / "report.md"
    bssid_csv_path = report_dir / "bssid_summary.csv"
    channel_csv_path = report_dir / "channel_summary.csv"
    sample_csv_path = report_dir / "sample_summary.csv"
    json_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown_report(report), encoding="utf-8")

    write_csv(bssid_csv_path, report["bssids"])
    write_csv(channel_csv_path, report["channels"])
    write_csv(sample_csv_path, report["samples"])

    if save_raw:
        raw_dir = report_dir / "raw_scans"
        raw_dir.mkdir(parents=True, exist_ok=False)
        for sample in samples:
            raw_path = raw_dir / f"scan_{sample.sample_index:03d}.txt"
            raw_path.write_text(sample.raw_output, encoding="utf-8")

    return {
        "report_dir": report_dir,
        "json": json_path,
        "markdown": md_path,
        "bssid_csv": bssid_csv_path,
        "channel_csv": channel_csv_path,
        "sample_csv": sample_csv_path,
    }


def write_csv(path: Path, rows: Sequence[dict]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
