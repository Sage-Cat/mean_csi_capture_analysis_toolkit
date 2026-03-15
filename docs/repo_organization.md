# Repository Organization Guide

This repository is organized for a repeated capture-analysis cycle with multiple students.

## Tracked in Git

- `csi_capture/`: reusable capture/parser/experiment Python code.
- `scripts/`: operational shell scripts (`run_tx_laptop.sh`, `run_rx_laptop.sh`).
- `tools/`: analysis scripts for RSSI/CSI.
- `tests/`: unit tests.
- `docs/`: notes, workflow, and methodology.
- `data/`: small reusable sample files only.
- `../.vscode/`: shared VS Code workspace settings/tasks/debug config at the parent workspace root.

## Local Only (Git-Ignored)

- `experiments/`: raw experiment runs and metadata.
- `out/`: generated figures/tables/reports.
- `out_*`: legacy output folders.
- build and temporary artifacts.

## Capture to Analysis Flow

1. Capture raw packets using `scripts/run_rx_laptop.sh`.
2. Raw logs are stored under `experiments/<exp_id>/...`.
3. Run analysis scripts from `tools/` with `--data_dir experiments/<exp_id>`.
4. Reports/figures/tables are generated under `out/`.

Alternative config-driven flow:

1. Prepare JSON config under `docs/configs/`.
2. Run `python3 -m csi_capture.experiment <distance|angle> --config <path>`.
3. Output is written to `experiments/<exp_id>/<experiment_type>/run_<run_id>/...` with `manifest.json`.

This keeps source code clean while allowing unlimited local experiments.

Open `/home/sagecat/Projects/research-workspace` in VS Code to use the shared workspace configuration.
