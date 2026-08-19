# Verification Report: static_sign_v1

Date: 2026-03-02
Repo: `/home/sagecat/Projects/csi_capture`

## 1) Unit Tests

Command:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Result:

- `Ran 33 tests`
- `OK`

## 2) CLI Help Smoke Test

Command:

```bash
./tools/exp --help
```

Result (excerpt):

- subcommands include `list-devices`, `list-target-profiles`, `capture`, `train`, `eval`, `validate-config`, `distance`
- exit code `0`

## 3) Config Validation Smoke Test

Command:

```bash
./tools/exp validate-config --experiment static_sign_v1 --mode capture --config ../studies/csi_capture_characterization/configs/static_sign_v1.capture.sample.json
```

Result:

- `Config validation passed: mode=capture file=../studies/csi_capture_characterization/configs/static_sign_v1.capture.sample.json`
- exit code `0`

## 4) Device Listing Helper

Command:

```bash
./tools/exp --list-devices
./tools/exp --list-target-profiles
```

Result:

```text
Serial device candidates:
- /dev/esp32_csi -> /dev/ttyACM0
- /dev/ttyACM0
```

## 5) Dry-run Capture Mode

Command:

```bash
./tools/exp capture --experiment static_sign_v1 --target-profile esp32s3_csi_v1 --dry-run-packets 5 --dry-run-timeout 10s --device /dev/esp32_csi
```

Result:

```text
Serial device: /dev/esp32_csi
Resolved path: /dev/ttyACM0
Selection source: cli
Error: dry-run timed out after 10.0s; expected 5 packets, got 0
```

Interpretation:

- Serial device open and resolution checks worked.
- No CSI packets were observed within timeout (likely TX source not running at test time).

## 6) static_sign_v1 Train/Eval Functional Smoke (Synthetic Local Dataset)

Synthetic dataset generated at:

- `../../private/experiments/csi_capture_characterization/datasets/static_sign_v1/smoke_20260302`

Train command:

```bash
./tools/exp train --experiment static_sign_v1 --dataset ../../private/experiments/csi_capture_characterization/datasets/static_sign_v1/smoke_20260302 --model svm_linear --window 1s --overlap 0.5 --artifact ../../private/experiments/csi_capture_characterization/analysis/static_sign_v1_smoke/model.pkl
```

Train result:

```text
Model artifact: ../../private/experiments/csi_capture_characterization/analysis/static_sign_v1_smoke/model.pkl
Metrics file: ../../private/experiments/csi_capture_characterization/analysis/static_sign_v1_smoke/model.metrics.json
Train split metrics: accuracy=1.0000 precision=1.0000 recall=1.0000 f1=1.0000
```

Eval command:

```bash
./tools/exp eval --experiment static_sign_v1 --dataset ../../private/experiments/csi_capture_characterization/datasets/static_sign_v1/smoke_20260302 --model ../../private/experiments/csi_capture_characterization/analysis/static_sign_v1_smoke/model.pkl --report ../../private/experiments/csi_capture_characterization/analysis/static_sign_v1_smoke/eval_report.json
```

Eval result:

```text
Eval report: ../../private/experiments/csi_capture_characterization/analysis/static_sign_v1_smoke/eval_report.json
Metrics: accuracy=1.0000 precision=1.0000 recall=1.0000 f1=1.0000
Confusion matrix (['baseline', 'hands_up']): [[20, 0], [0, 20]]
```

## 7) Lint/Format

- No dedicated lint/format target/tool is defined in repository automation.
- `make test` equivalent was covered by unit test command above.

## 8) Backward Compatibility Spot Check

Command:

```bash
python3 -m csi_capture.experiment --help
```

Result:

- existing `run`, `distance`, `angle` commands still available
- exit code `0`
