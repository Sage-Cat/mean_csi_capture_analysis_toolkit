# Angular Localization Analysis Notes

## Goal

Estimate azimuth angle (`angle_deg`) from Wi-Fi measurements using:

- RSSI-only baselines
- CSI-only model
- CSI+RSSI fusion model

This is the angle-estimation counterpart to the distance analysis pipeline.

## Run command

```bash
python tools/analyze_wifi_angular_localization.py \
  --data_dir experiments/exp_angular_localization_v1 \
  --out_dir out/angular_localization \
  --seed 42
```

Optional flags:

- `--group_col <name>`: leakage-safe split grouping (default: `group_id`).
- `--test_size <float>`: grouped test split fraction (default: `0.3`).
- `--angle_bins "-60,-45,-30,-15,0,15,30,45,60"`: evaluation bin centers.
- `--use_pca`: use summary+PCA features only for CSI models.
- `--knn_k <int>`: neighbors for CSI/fusion kNN regressors (default: `7`).
- `--downsample_step <int>`: CSI vector downsampling stride (default: `4`).

## Required metadata

Angle must be available through one of:

1. explicit field `angle_deg` in each row, or
2. scenario tag with angle token, e.g. `LoS_ang_m30`, `LoS_ang_000`, `NLoS_wall_ang_p45`.

The parser auto-extracts scenario base (`LoS`, `NLoS_human`, etc.) and angle from these tags.

## Leakage control

- Uses grouped split (`GroupShuffleSplit` when sklearn is installed).
- Default grouping key `group_id` is:
  `run_id | scenario_base | angle_deg`.
- PCA/scaling/model fitting are train-only; test data is transformed with train-fitted objects.

## Metrics

Primary metrics (degrees):

- `MAE_deg`
- `RMSE_deg`
- `MedAE_deg`

Operational metrics:

- `P_abs_err_le_5deg`
- `P_abs_err_le_10deg`
- `Bias_deg` (mean signed wrapped error)

All angle errors are computed as wrapped circular difference in `[-180, 180)`.

## Outputs

- `out/angular_localization/tables/table_metrics_overall.csv`
- `out/angular_localization/tables/table_metrics_by_scenario.csv`
- `out/angular_localization/tables/table_metrics_by_angle_bin.csv`
- `out/angular_localization/figs/cdf_abs_angle_error.png`
- `out/angular_localization/figs/scatter_pred_vs_true_angle.png`
- `out/angular_localization/figs/boxplot_angle_error_by_scenario.png`
- `out/angular_localization/figs/boxplot_angle_error_by_bin.png`
- `out/angular_localization/figs/polar_mean_error.png`
- `out/angular_localization/report.md`
