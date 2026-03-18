#!/usr/bin/env python3
"""Survey 2.4 GHz AP state and produce an experiment-oriented radio report."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from csi_capture.analysis.radio_state import (  # noqa: E402
    CHANNELS_24_GHZ,
    RadioSurveyError,
    capture_scan_samples,
    default_report_id,
    detect_scanner,
    summarize_radio_state,
    write_report_artifacts,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scanner", choices=("auto", "nmcli", "netsh"), default="auto")
    parser.add_argument("--interface", default=None, help="Wi-Fi interface name when supported.")
    parser.add_argument(
        "--rescan",
        choices=("yes", "no", "auto"),
        default="yes",
        help="Rescan behavior for nmcli backend (default: yes).",
    )
    parser.add_argument("--samples", type=int, default=3, help="Number of repeated scan samples.")
    parser.add_argument("--interval-s", type=float, default=2.0, help="Delay between samples.")
    parser.add_argument(
        "--focus-channel",
        type=int,
        default=11,
        help="Experiment channel to evaluate against the external AP field.",
    )
    parser.add_argument("--experiment-ssid", default=None, help="SSID of your experiment AP to exclude from interference ranking.")
    parser.add_argument("--experiment-bssid", default=None, help="BSSID of your experiment AP to exclude from interference ranking.")
    parser.add_argument("--top-n", type=int, default=10, help="Number of top interferers to keep in the report.")
    parser.add_argument("--out-dir", default="out/radio_state_24ghz", help="Output root directory.")
    parser.add_argument("--report-id", default=None, help="Report directory id (default: UTC timestamped).")
    parser.add_argument("--no-save-raw", action="store_true", help="Do not persist raw scan outputs.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.focus_channel not in CHANNELS_24_GHZ:
        parser.error(f"--focus-channel must be one of {CHANNELS_24_GHZ}")
    if args.samples <= 0:
        parser.error("--samples must be > 0")
    if args.interval_s < 0:
        parser.error("--interval-s must be >= 0")
    if args.top_n <= 0:
        parser.error("--top-n must be > 0")

    try:
        scanner = detect_scanner(args.scanner)
        samples = capture_scan_samples(
            scanner=scanner,
            interface=args.interface,
            rescan=args.rescan,
            samples=args.samples,
            interval_s=args.interval_s,
        )
        report = summarize_radio_state(
            samples,
            focus_channel=args.focus_channel,
            experiment_ssid=args.experiment_ssid,
            experiment_bssid=args.experiment_bssid,
            top_n=args.top_n,
        )
        report_id = args.report_id or default_report_id()
        report_dir = Path(args.out_dir) / report_id
        artifacts = write_report_artifacts(
            report_dir,
            report,
            samples,
            save_raw=not args.no_save_raw,
        )
    except RadioSurveyError as err:
        print(f"Error: {err}")
        return 2

    summary = report["summary"]
    print(f"Scanner: {report['scanner']['kind']}")
    print(f"Samples: {summary['sample_count']}")
    print(f"External unique 2.4 GHz BSSIDs: {summary['unique_bssids_24ghz_external']}")
    print(f"Recommended lowest-overlap channel: {summary['recommended_channel_lowest_overlap']}")
    print(f"Recommended non-overlapping channel (1/6/11): {summary['recommended_channel_non_overlapping']}")
    print(f"Focus-channel overlap rank: {summary['focus_channel_overlap_rank']}")
    if report["warnings"]:
        print("Warnings:")
        for warning in report["warnings"]:
            print(f"- {warning}")
    print(f"Report directory: {artifacts['report_dir']}")
    print(f"Markdown report: {artifacts['markdown']}")
    print(f"JSON report: {artifacts['json']}")
    print(f"BSSID CSV: {artifacts['bssid_csv']}")
    print(f"Channel CSV: {artifacts['channel_csv']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
