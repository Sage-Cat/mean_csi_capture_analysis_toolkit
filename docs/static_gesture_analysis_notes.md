# Static Gesture Analysis Notes

## Goal

Estimate how well `baseline` vs `hands_up` can be separated from ESP32 RSSI/CSI captures in the `static_sign_v1` dataset.

This is a classification task, so the primary metrics differ from `distance_measurement` and `angular_localization`:

- accuracy
- balanced accuracy
- precision
- recall
- F1
- ROC AUC
- run-level majority-vote accuracy

## Run command

```bash
.venv/bin/python tools/analyze_wifi_static_gesture.py \
  --data_dir experiments/exp_2026_03_02_static_gesture_Павленко_Войтович_Дядюк/дані \
  --out_dir out/static_gesture_20260302 \
  --seed 42 \
  --window_s 1.0 \
  --overlap 0.5 \
  --test_size 0.3
```

## Split protocol

- Holdout is leakage-safe at the `run_id` level.
- The split is label-balanced: each class contributes held-out runs to the test set.
- All windows from one run stay entirely in train or entirely in test.

## Features

- RSSI summary features per window:
  `mean_rssi`, `std_rssi`, `median_rssi`, `range_rssi`
- CSI summary features per window:
  `mean_amp`, `std_amp`, `median_amp`, `rms_amp`, `iqr_amp`, `entropy_amp`
- Fusion models combine both sets.

## Output files

- `out/static_gesture_20260302/tables/table_dataset_summary.csv`
- `out/static_gesture_20260302/tables/table_feature_effect_sizes.csv`
- `out/static_gesture_20260302/tables/table_metrics_overall.csv`
- `out/static_gesture_20260302/tables/table_metrics_by_run.csv`
- `out/static_gesture_20260302/figs/boxplot_mean_rssi_by_label.png`
- `out/static_gesture_20260302/figs/boxplot_mean_amp_by_label.png`
- `out/static_gesture_20260302/figs/pca_windows.png`
- `out/static_gesture_20260302/figs/confusion_matrix_best_model.png`
- `out/static_gesture_20260302/figs/roc_curve_best_model.png`
- `out/static_gesture_20260302/report.md`
