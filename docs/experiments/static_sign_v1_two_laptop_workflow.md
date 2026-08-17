# static_sign_v1 Two-Laptop Workflow (AP + Receiver + Human Sign)

Date: 2026-03-02

This workflow assumes:

- Laptop A controls ESP32 TX board running `csi_send` (acts as AP/transmitter source).
- Laptop B controls ESP32 RX board running `csi_recv` and captures CSI.
- Human stands between AP and receiver, **back facing receiver**, and performs a static sign.
- RX serial device defaults to `/dev/esp32_csi` when available; on macOS it is typically `/dev/cu.usbmodem*` or `/dev/tty.usbmodem*`.
- Common environment profile for this workflow: `esp32s3_csi_v1`.

## 1) Physical Setup

1. Place TX ESP32 at one side (fixed orientation).
2. Place RX ESP32 opposite side (fixed orientation).
3. Keep distance and orientation fixed for all runs.
4. Subject stands between nodes, back to RX.
5. Define labels:
- `baseline`: neutral pose, no sign.
- `hands_up`: both hands raised in the agreed static posture.

## 2) Laptop A (TX/AP board) Commands

First-time build+flash:

```bash
cd csi_capturing_example
./scripts/run_tx_laptop.sh
```

Subsequent runs (skip rebuild/reflash):

```bash
cd csi_capturing_example
./scripts/run_tx_laptop.sh --skip-build --skip-flash
```

On macOS with explicit port:

```bash
./scripts/run_tx_laptop.sh --port /dev/cu.usbmodem2101
```

Expected outcome:

- TX node prints ready message and continuously transmits CSI source traffic.

## 3) Laptop B (RX board) Commands

### 3.1 Flash/prepare RX firmware

First-time build+flash `csi_recv`:

```bash
cd csi_capturing_example
./scripts/run_rx_csi_node.sh --port /dev/esp32_csi
```

Subsequent runs (skip rebuild/reflash):

```bash
cd csi_capturing_example
./scripts/run_rx_csi_node.sh --port /dev/esp32_csi --skip-build --skip-flash
```

### 3.2 Verify serial path and stream readiness

```bash
cd csi_capturing_example
./tools/exp --list-devices
./tools/exp --list-target-profiles
./tools/exp capture --experiment static_sign_v1 --dry-run-packets 5 --dry-run-timeout 10s
```

If dry-run reports `0` packets, verify TX board is running and both boards are powered.

On macOS, you can also pass an explicit device if multiple ports exist:

```bash
./tools/exp capture --experiment static_sign_v1 --dry-run-packets 5 --dry-run-timeout 10s --device /dev/cu.usbmodem1101
```

### 3.3 Capture dataset (protocol runner)

Recommended interactive protocol (captures baseline then hands_up with prompts):

```bash
cd csi_capturing_example
./scripts/run_static_sign_protocol.sh \
  --target-profile esp32s3_csi_v1 \
  --dataset-id 20260302_subject01_labA \
  --runs 5 \
  --duration 20s \
  --subject-id subject01 \
  --environment-id labA \
  --notes "back-to-rx posture, fixed feet marker"
```

On macOS with explicit port:

```bash
./scripts/run_static_sign_protocol.sh \
  --device /dev/cu.usbmodem1101 \
  --target-profile esp32s3_csi_v1 \
  --dataset-id 20260302_subject01_labA \
  --runs 5 \
  --duration 20s \
  --subject-id subject01 \
  --environment-id labA
```

This creates:

- `data/experiments/static_sign_v1/20260302_subject01_labA/baseline/...`
- `data/experiments/static_sign_v1/20260302_subject01_labA/hands_up/...`

## 4) Train and Evaluate (Laptop B)

Use helper script:

```bash
cd csi_capturing_example
./scripts/run_static_sign_train_eval.sh \
  --dataset-id 20260302_subject01_labA \
  --model svm_linear \
  --window 1s \
  --overlap 0.5
```

Or direct CLI commands:

```bash
cd csi_capturing_example
./tools/exp train \
  --experiment static_sign_v1 \
  --dataset data/experiments/static_sign_v1/20260302_subject01_labA \
  --model svm_linear \
  --window 1s \
  --overlap 0.5 \
  --artifact artifacts/static_sign_v1/20260302_subject01_labA/svm_linear.pkl

./tools/exp eval \
  --experiment static_sign_v1 \
  --dataset data/experiments/static_sign_v1/20260302_subject01_labA \
  --model artifacts/static_sign_v1/20260302_subject01_labA/svm_linear.pkl \
  --report out/static_sign_v1/20260302_subject01_labA/eval_report.json
```

## 5) Output Artifacts

- Captures: `data/experiments/static_sign_v1/<dataset_id>/<label>/run_<run_id>/`
- Model artifact: `artifacts/static_sign_v1/<dataset_id>/...pkl`
- Train metrics: alongside artifact as `.metrics.json`
- Eval report: `out/static_sign_v1/<dataset_id>/eval_report.json`

## 6) Recommended Data Quality Rules

- Keep AP/RX positions fixed during all runs in one dataset.
- Keep subject orientation fixed (back to receiver).
- Capture equal number of runs per label.
- Avoid people walking in background during capture.
- If room conditions change, use a new `dataset_id`.
