# Repository Organization Guide

This repository is organized for a repeated capture-analysis cycle with multiple students.

## Tracked in Git

- `csi_capture/`: reusable capture/parser/experiment Python code.
- `scripts/`: operational shell scripts (`run_tx_laptop.sh`, `run_rx_laptop.sh`).
- `tools/`: analysis scripts for RSSI/CSI.
- `tests/`: unit tests.
- `docs/`: notes, workflow, and methodology.
- `../../.vscode/`: shared VS Code workspace settings/tasks/debug config at the
  workspace root.

## Central study ownership

- `../studies/csi_capture_characterization/`: plans, privacy-safe profiles, and
  operator workflows.
- `../../private/experiments/csi_capture_characterization/runs/`: raw runs.
- `../../private/experiments/csi_capture_characterization/datasets/`: private datasets.
- `../../private/experiments/csi_capture_characterization/analysis/`: generated reports.
- build and temporary artifacts remain local and reproducible.

## Capture to Analysis Flow

1. Capture raw packets using `scripts/run_rx_laptop.sh`.
2. Raw logs are stored under `../../private/experiments/csi_capture_characterization/runs/<exp_id>/...`.
3. Run analysis scripts from `tools/` with `--data_dir ../../private/experiments/csi_capture_characterization/runs/<exp_id>`.
4. Reports/figures/tables are generated under the private study `analysis/` root.

Alternative config-driven flow:

1. Prepare JSON config under `../studies/csi_capture_characterization/configs/`.
2. Run `python3 -m csi_capture.experiment <distance|angle> --config <path>`.
3. Output is written to `../../private/experiments/csi_capture_characterization/runs/<exp_id>/<experiment_type>/run_<run_id>/...` with `manifest.json`.

This keeps source code clean while allowing unlimited local experiments.

Open the research workspace root in VS Code to use the shared workspace
configuration.
