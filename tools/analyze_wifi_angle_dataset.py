#!/usr/bin/env python3
"""Basic AoA/angle dataset summary (capture-stage validation only)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Iterator

import pandas as pd


ANGLE_COLUMNS = ["angle_deg", "exp_id", "run_id", "trial_id", "scenario", "source_file"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data_dir", required=True, help="Input dataset root.")
    parser.add_argument("--out_dir", required=True, help="Session-owned output directory.")
    return parser.parse_args()


def discover_files(data_dir: Path) -> list[Path]:
    if not data_dir.exists():
        raise FileNotFoundError(f"data_dir does not exist: {data_dir}")
    files = sorted(
        p for p in data_dir.rglob("*") if p.is_file() and p.suffix.lower() in (".jsonl", ".csv")
    )
    if not files:
        raise FileNotFoundError(f"No .jsonl/.csv files found under: {data_dir}")
    return files


def iter_records(path: Path) -> Iterator[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        frame = pd.read_csv(path)
        for row in frame.to_dict(orient="records"):
            if isinstance(row, dict):
                yield row
        return
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                yield row


def _scenario_label(record: dict[str, Any]) -> str:
    scenario_tags = record.get("scenario_tags")
    if isinstance(scenario_tags, list) and scenario_tags:
        tag = str(scenario_tags[0]).strip()
        if tag:
            return tag
    if record.get("scenario") is not None:
        return str(record["scenario"])
    return "unknown"


def build_frame(files: list[Path]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for path in files:
        for record in iter_records(path):
            experiment_type = str(record.get("experiment_type", "")).strip().lower()
            if experiment_type and experiment_type != "angle":
                continue
            angle_value = record.get("angle_deg")
            if angle_value is None:
                continue
            try:
                angle_deg = float(angle_value)
            except (TypeError, ValueError):
                continue
            rows.append(
                {
                    "angle_deg": angle_deg,
                    "exp_id": str(record.get("exp_id", "")),
                    "run_id": str(record.get("run_id", "")),
                    "trial_id": str(record.get("trial_id", "")),
                    "scenario": _scenario_label(record),
                    "source_file": str(path),
                }
            )
    if not rows:
        return pd.DataFrame(columns=ANGLE_COLUMNS)
    return pd.DataFrame(rows, columns=ANGLE_COLUMNS)


def write_outputs(frame: pd.DataFrame, out_dir: Path) -> None:
    tables_dir = out_dir / "tables"
    tables_dir.mkdir(parents=True, exist_ok=True)

    if frame.empty:
        summary = pd.DataFrame(
            [
                {
                    "total_packets": 0,
                    "num_angles": 0,
                    "angles_deg": "",
                    "num_runs": 0,
                    "num_trials": 0,
                    "num_scenarios": 0,
                }
            ]
        )
        by_angle = pd.DataFrame(columns=["angle_deg", "packet_count"])
        by_trial = pd.DataFrame(columns=["run_id", "trial_id", "angle_deg", "scenario", "packet_count"])
        angle_line = "none"
        run_count = 0
        trial_count = 0
    else:
        summary = pd.DataFrame(
            [
                {
                    "total_packets": int(len(frame)),
                    "num_angles": int(frame["angle_deg"].nunique()),
                    "angles_deg": ";".join(f"{x:g}" for x in sorted(frame["angle_deg"].unique())),
                    "num_runs": int(frame["run_id"].nunique()),
                    "num_trials": int(frame["trial_id"].nunique()),
                    "num_scenarios": int(frame["scenario"].nunique()),
                }
            ]
        )
        by_angle = (
            frame.groupby("angle_deg", as_index=False)
            .size()
            .rename(columns={"size": "packet_count"})
            .sort_values("angle_deg")
        )
        by_trial = (
            frame.groupby(["run_id", "trial_id", "angle_deg", "scenario"], as_index=False)
            .size()
            .rename(columns={"size": "packet_count"})
            .sort_values(["run_id", "trial_id"])
        )
        angle_line = ", ".join(f"{x:g}" for x in sorted(frame["angle_deg"].unique()))
        run_count = int(frame["run_id"].nunique())
        trial_count = int(frame["trial_id"].nunique())

    summary.to_csv(tables_dir / "table_dataset_summary.csv", index=False)
    by_angle.to_csv(tables_dir / "table_packets_by_angle.csv", index=False)
    by_trial.to_csv(tables_dir / "table_packets_by_trial.csv", index=False)

    report_lines = [
        "# Angle Dataset Summary",
        "",
        f"- Total packets: {int(len(frame))}",
        f"- Angles (deg): {angle_line}",
        f"- Runs: {run_count}",
        f"- Trials: {trial_count}",
        "",
        "Generated tables:",
        "- `tables/table_dataset_summary.csv`",
        "- `tables/table_packets_by_angle.csv`",
        "- `tables/table_packets_by_trial.csv`",
    ]
    (out_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    data_dir = Path(args.data_dir)
    out_dir = Path(args.out_dir)
    files = discover_files(data_dir)
    frame = build_frame(files)
    write_outputs(frame, out_dir)
    print(f"Angle dataset analysis complete. rows={len(frame)} out_dir={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
