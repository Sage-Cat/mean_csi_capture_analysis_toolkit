# Angular Localization Experiment Plan

## Goal

Estimate node azimuth angle (`angle_deg`) from radio-channel parameters (CSI + RSSI) in a controlled Wi-Fi setup.

This is the next experiment after distance estimation, with the main target changed from `distance_m` to `angle_deg`.

## Run command (analysis phase)

```bash
python tools/analyze_wifi_angular_localization.py \
  --data_dir experiments/exp_angular_localization_v1 \
  --out_dir out/angular_localization \
  --seed 42
```

Planned optional flags:

- `--test_size <float>`: grouped test split fraction (default target: `0.3`).
- `--group_col <name>`: leakage-safe grouping column (default target: `run_id` or `acq_block_id`).
- `--angle_bins "-60,-45,-30,-15,0,15,30,45,60"`: angle grid used for evaluation by bins.
- `--use_pca`: use summary + PCA CSI features only.
- `--knn_k <int>`: neighbors for CSI kNN angle regressor (default target: `7`).

## Geometry and capture design

- RX node (anchor) fixed position and fixed orientation.
- TX node placed on an arc centered at RX.
- Fixed radius for baseline phase: `distance_m = 2.0`.
- Angle sweep for baseline phase: `-60` to `+60` degrees, step `15` degrees.
- Scenarios: `LoS`, `NLoS_human`, `NLoS_wall`.
- Runs per scenario: `3`.
- Packets per angle point per run: `2500`.

Total baseline packets:

- 9 angles x 3 scenarios x 3 runs x 2500 packets = `202,500` packets.

## Capture protocol (using current scripts)

Current capture script has no dedicated `angle_deg` field. For the next run, store angle in scenario tag, for example:

- `LoS_ang_m30`
- `LoS_ang_000`
- `LoS_ang_p45`

Example capture command per angle point:

```bash
./scripts/run_rx_laptop.sh \
  --port /dev/ttyACM1 \
  --exp-id exp_angular_localization_v1 \
  --scenario LoS_ang_p30 \
  --run-id 1 \
  --distance-m 2.0 \
  --max-records 2500
```

Suggested batch loop for one scenario and one run:

```bash
for a in -60 -45 -30 -15 0 15 30 45 60; do
  if (( a < 0 )); then
    printf -v tag "m%02d" "${a#-}"
  elif (( a > 0 )); then
    printf -v tag "p%02d" "$a"
  else
    tag="000"
  fi
  ./scripts/run_rx_laptop.sh \
    --port /dev/ttyACM1 \
    --exp-id exp_angular_localization_v1 \
    --scenario "LoS_ang_${tag}" \
    --run-id 1 \
    --distance-m 2.0 \
    --max-records 2500
done
```

## Metadata requirements

Minimum fields needed for analysis table:

- `exp_id`
- `scenario_base` (`LoS`, `NLoS_human`, `NLoS_wall`)
- `angle_deg` (parsed from scenario tag in v1)
- `run_id`
- `distance_m`
- `timestamp`
- `rssi`
- `csi`

Recommended additions for v2 capture schema:

- `angle_deg` as explicit numeric metadata argument in `run_rx_laptop.sh` and `csi_capture.capture`
- `rx_heading_deg` and `tx_heading_deg`
- `anchor_id` and `node_id`
- `environment_id`

## Split protocol (leakage control)

- Use grouped split so packets from one acquisition block are not split across train/test.
- Preferred grouping key: `run_id + angle_deg + scenario_base`.
- If a block identifier exists, use that as the primary grouping field.
- Fit PCA/scalers/models on train only, then transform test set.

## Models included

- RSSI baseline:
  angle from RSSI-only features using linear and polynomial baselines.
- CSI model:
  kNN regression on CSI-derived features (same philosophy as distance pipeline).
- CSI + RSSI fusion model:
  concatenate stable RSSI descriptors with CSI summary/PCA features.

Optional robust model for comparison:

- Gradient boosting regressor for nonlinear angle mapping.

## Metrics

Primary:

- MAE in degrees (`MAE_deg`)
- RMSE in degrees (`RMSE_deg`)
- Median absolute angle error (`MedAE_deg`)

Operational:

- `P(|error| <= 5 deg)`
- `P(|error| <= 10 deg)`
- Signed bias by angle bin

## Required outputs

- `out/angular_localization/tables/table_metrics_overall.csv`
- `out/angular_localization/tables/table_metrics_by_scenario.csv`
- `out/angular_localization/tables/table_metrics_by_angle_bin.csv`
- `out/angular_localization/figs/cdf_abs_angle_error.png`
- `out/angular_localization/figs/scatter_pred_vs_true_angle.png`
- `out/angular_localization/figs/boxplot_angle_error_by_scenario.png`
- `out/angular_localization/figs/boxplot_angle_error_by_bin.png`
- `out/angular_localization/figs/polar_mean_error.png`
- `out/angular_localization/report.md`

## Execution phases

Phase 1: Pilot (`LoS`, run 1 only)

- Capture all 9 angles once.
- Verify parsing of angle tags and feature extraction.
- Produce first report and sanity-check error trends.

Phase 2: Full baseline dataset

- Capture all scenarios and all planned runs.
- Run grouped evaluation.
- Freeze baseline metrics.

Phase 3: Robustness extension

- Repeat with slight distance offsets (`1.5 m`, `2.5 m`) or mild orientation perturbation.
- Evaluate angle generalization under geometry shift.

## Acceptance criteria for the next milestone

- End-to-end reproducible pipeline from capture to report.
- No leakage between train and test by grouping rule.
- Stable improvement of CSI/fusion model over RSSI baseline in MAE_deg.
- At least one scenario reaches `P(|error| <= 10 deg) >= 0.80`.

## Risks and controls

- Multipath can invert angle signatures:
  keep geometry fixed in baseline and separate LoS/NLoS analyses.
- Human/body blocking creates non-stationarity:
  run repeated acquisitions and grouped split by block.
- Metadata drift (angle coding mistakes):
  enforce strict scenario tag template and parse-time validation.
