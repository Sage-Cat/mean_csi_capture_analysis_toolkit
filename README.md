# ESP32 CSI/RSSI Experiment Toolkit

Toolkit for capturing ESP32 CSI packets, organizing experiment runs, and producing RSSI/CSI analysis reports.

Primary workflow:

- Laptop A (TX): flash/run `csi_send`
- Laptop B (RX): flash/run `csi_recv`, capture CSI stream, attach experiment metadata

## Repository Layout

```text
csi_capture/              # Python capture/parser/experiment modules
scripts/                  # Operational scripts (TX/RX, local setup)
tools/                    # Analysis scripts
tests/                    # Unit tests
docs/                     # Notes + config templates + architecture docs
docs/design/plantuml/     # PlantUML design source + rendered PNG diagrams
experiments/              # Local raw runs (git-ignored, often a local symlink)
out/                      # Local generated figures/tables/reports (git-ignored except README/.gitkeep)
data/                     # Small reusable sample data only
```

`experiments/`, `out/`, and build files are intentionally ignored so students can run scripts locally without polluting git history.

## 1) System Dependencies (Both Laptops)

Linux (Ubuntu/Debian):

```bash
sudo apt update
sudo apt install -y \
  git wget flex bison gperf python3 python3-pip python3-venv python3-serial \
  cmake ninja-build ccache libffi-dev libssl-dev dfu-util libusb-1.0-0
sudo usermod -a -G dialout $USER
```

Log out and log in again after adding `dialout`.

macOS (Homebrew):

```bash
brew install python cmake ninja ccache dfu-util libusb
python3 -m pip install -r requirements.txt
```

On macOS, serial ports are usually like `/dev/cu.usbmodem*` or `/dev/tty.usbmodem*`.

## 2) ESP-IDF + esp-csi Setup

```bash
mkdir -p ~/esp
cd ~/esp
git clone -b v5.5.3 --recursive https://github.com/espressif/esp-idf.git
cd esp-idf
./install.sh esp32s3
```

Optional helper alias:

```bash
echo "alias get_idf='. $HOME/esp/esp-idf/export.sh'" >> ~/.bashrc
source ~/.bashrc
```

Clone `esp-csi`:

```bash
cd ~/esp
git clone https://github.com/espressif/esp-csi.git
```

## 3) Project Setup

```bash
git clone git@github.com:Sage-Cat/csi_capturing_example.git
cd csi_capturing_example
python3 -m pip install -r requirements.txt
```

Shared VS Code workspace:

```bash
code /home/sagecat/Projects/research-workspace
```

Workspace tasks, debugging, and agent/editor settings are centralized at the parent `research-workspace` root.

Target environment profile (common across experiments):

```bash
./tools/exp --list-target-profiles
```

Current baseline profile:

- `esp32s3_csi_v1`

Architecture/refactor blueprint:

- `docs/platform_refactor_blueprint.md`

## 4) Capture Commands

TX node:

```bash
./scripts/run_tx_laptop.sh
```

RX node:

```bash
./scripts/run_rx_laptop.sh \
  --port /dev/esp32_csi \
  --exp-id exp_2026_02_23_lab \
  --scenario LoS \
  --run-id 1 \
  --distance-m 1.0 \
  --max-records 2500
```

On macOS, pass explicit ports when needed, for example:

```bash
./scripts/run_tx_laptop.sh --port /dev/cu.usbmodem2101
./scripts/run_rx_laptop.sh --port /dev/cu.usbmodem1101 --exp-id exp_macos_test --scenario LoS --run-id 1 --distance-m 1.0 --max-records 200
```

By default, RX output is stored under `experiments/<exp_id>/...`.

New unified config-driven runner (distance + angle):

```bash
# Distance experiment from config
python3 -m csi_capture.experiment distance \
  --config docs/configs/distance_capture.sample.json \
  --target-profile esp32s3_csi_v1

# Angle/AoA dataset capture directly from CLI (no JSON editing)
python3 -m csi_capture.experiment angle \
  --exp-id exp_angle_radial_demo \
  --target-profile esp32s3_csi_v1 \
  --runs 2 \
  --angles 0 45 90 135 180 225 270 315 \
  --repeats-per-angle 1 \
  --packets-per-repeat 300 \
  --scenario-tags LoS \
  --room-id room_a \
  --notes "AP center, RX on circle R=2m" \
  --num-antennas 1 \
  --device /dev/esp32_csi

# Same flow but operator-controlled: wait for Enter between angles
python3 -m csi_capture.experiment angle \
  --exp-id exp_angle_radial_demo_manual_step \
  --target-profile esp32s3_csi_v1 \
  --runs 1 \
  --angles 0 45 90 135 180 225 270 315 \
  --repeats-per-angle 1 \
  --packets-per-repeat 300 \
  --wait-enter \
  --scenario-tags LoS \
  --room-id room_a \
  --notes "press Enter after moving RX to next angle mark" \
  --num-antennas 1 \
  --device /dev/esp32_csi

# Same idea, but with explicit run ids
python3 -m csi_capture.experiment angle \
  --exp-id exp_angle_radial_demo \
  --target-profile esp32s3_csi_v1 \
  --run-ids 001 002 \
  --angles 0 45 90 135 180 225 270 315 \
  --packets-per-repeat 300 \
  --scenario-tags LoS \
  --room-id room_a \
  --notes "AP center, RX on circle R=2m" \
  --num-antennas 1
```

Angle CLI defaults to `/dev/esp32_csi`. Use `--device auto` for cross-platform auto-detection
(`/dev/esp32_csi`, `/dev/ttyACM*`, `/dev/cu.usbmodem*`, etc.). JSON configs for `angle`
are still supported for backward compatibility:

```bash
python3 -m csi_capture.experiment angle \
  --config docs/configs/angle_radial_45deg_2runs.sample.json \
  --device auto
```

New experiment framework CLI (includes `static_sign_v1`):

```bash
# List serial candidates (includes /dev/esp32_csi symlink resolution)
./tools/exp --list-devices

# List available target environment profiles
./tools/exp --list-target-profiles

# List registered experiment families and supported actions
./tools/exp --list-experiments

# Dry-run: open serial and parse N CSI packets, then exit
./tools/exp capture --experiment static_sign_v1 --target-profile esp32s3_csi_v1 --dry-run-packets 5 --dry-run-timeout 10s

# Capture static sign dataset
./tools/exp capture --experiment static_sign_v1 --target-profile esp32s3_csi_v1 --label hands_up --runs 5 --duration 20s
./tools/exp capture --experiment static_sign_v1 --target-profile esp32s3_csi_v1 --label baseline --runs 5 --duration 20s

# Protocol helper (baseline then hands_up with prompts)
./scripts/run_static_sign_protocol.sh \
  --device /dev/esp32_csi \
  --target-profile esp32s3_csi_v1 \
  --dataset-id 20260302_subject01_labA \
  --runs 5 \
  --duration 20s \
  --subject-id subject01 \
  --environment-id labA

# Validate future-ready config shape for a new experiment family
./tools/exp validate-config \
  --experiment presence_v1 \
  --mode capture \
  --config docs/configs/presence_v1.capture.sample.json

# Interference protocol (cross-platform Python entrypoint)
python3 -m csi_capture.interference_protocol --list-scenarios
python3 -m csi_capture.interference_protocol --device auto --scenario-set core --runs 3 --max-records 1500
```

Native Windows workflow for `interference_v1`:

- `docs/experiments/interference_v1_windows_workflow.md`

Device selection precedence for `tools/exp capture`:

1. `--device`
2. env var `CSI_CAPTURE_DEVICE` (or `ESP32_CSI_DEVICE`)
3. `/dev/esp32_csi` if present, otherwise auto-detected serial candidate

For complete AP+RX two-laptop setup instructions, see:

- `docs/experiments/static_sign_v1_two_laptop_workflow.md`

## 5) Experiment Data Structure

Each captured row stores:

- `timestamp` (host Unix ms)
- `rssi`
- `csi` (I/Q integer array)
- `esp_timestamp`
- `mac`
- plus metadata tags such as `exp_id`, `experiment_type`, `run_id`, `trial_id`, `device_path`, scenario fields, and ground-truth (`distance_m` or `angle_deg`)
- all unified outputs also include `target_profile` and environment profile snapshot for reproducibility

Example:

```json
{"timestamp":1700000000000,"rssi":-15,"csi":[1,-2,3,-4],"esp_timestamp":119050,"mac":"1a:00:00:00:00:00","exp_id":"exp_2026_02_23_lab","experiment_type":"angle","run_id":"1","trial_id":"angle_30deg_rep_001","device_path":"/dev/esp32_csi","scenario_tags":["LoS"],"angle_deg":30.0}
```

Layout:

- Legacy distance script layout (unchanged):
  - `experiments/<exp_id>/meta.json`
  - `experiments/<exp_id>/<scenario>/run_<run_id>/distance_<X>m.jsonl`
- Unified runner layout:
  - `experiments/<exp_id>/<experiment_type>/run_<run_id>/manifest.json`
  - `experiments/<exp_id>/<experiment_type>/run_<run_id>/trial_<trial_id>/capture.jsonl`

Every unified runner invocation writes a per-run `manifest.json` with config snapshot, git revision, device path, and trial summaries.

## 6) Analysis Commands

Distance measurement:

```bash
python3 tools/analyze_wifi_distance_measurement.py \
  --data_dir experiments/<exp_id> \
  --out_dir out/distance_measurement
```

Stability statistics:

```bash
python3 tools/analyze_wifi_stability_statistics.py \
  --data_dir experiments/<exp_id> \
  --out_dir out/stability_statistics
```

Angle dataset summary:

```bash
python3 tools/analyze_wifi_angle_dataset.py \
  --data_dir experiments/<exp_id>/angle \
  --out_dir out/angle_dataset
```

2.4 GHz radio-state survey before/while experiments:

```bash
python3 tools/survey_wifi_24ghz.py \
  --focus-channel 11 \
  --samples 3 \
  --interval-s 2.0 \
  --experiment-ssid <your_experiment_ssid>
```

Outputs are written to `out/` and are git-ignored.

Static sign train/eval:

```bash
./tools/exp train \
  --experiment static_sign_v1 \
  --dataset data/experiments/static_sign_v1/<dataset_id> \
  --model svm_linear \
  --window 1s \
  --overlap 0.5

./tools/exp eval \
  --experiment static_sign_v1 \
  --dataset data/experiments/static_sign_v1/<dataset_id> \
  --model artifacts/static_sign_v1/<stamp>/svm_linear.pkl \
  --report out/static_sign_v1/report.json
```

## 7) Make Targets

```bash
make setup-vscode
make test
make tx-node PORT=/dev/ttyACM0
make rx-smoke PORT=/dev/ttyACM1 EXP_ID=exp_smoke
make experiment-distance DISTANCE_CONFIG=docs/configs/distance_capture.sample.json
make experiment-angle ANGLE_CONFIG=docs/configs/angle_radial_45deg_2runs.sample.json
make analyze-distance DATA_DIR=experiments/<exp_id>
make survey-24ghz
make analyze-stability DATA_DIR=experiments/<exp_id>
make analyze-angle DATA_DIR=experiments/<exp_id>/angle
make analyze-all DATA_DIR=experiments/<exp_id>
```

## 8) Documentation Index

- Experiment framework docs: `docs/experiments/README.md`
- Requirements: `docs/experiments/requirements.md`
- Design package (PlantUML + PNG): `docs/design/plantuml/README.md`
- Validation report: `docs/experiments/validation_report.md`
- UA playbooks:
  - `DISTANCE_EXPERIMENT_UA.md`
  - `ANGLE_EXPERIMENT_UA.md`
  - `EXPERIMENT_STATIC_SIGN_UA.md`
  - `EXPERIMENTS_PLAN_UA.md`
