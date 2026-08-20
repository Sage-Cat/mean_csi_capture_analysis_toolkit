# ESP32 CSI/RSSI Experiment Toolkit

A research toolkit for collecting ESP32 channel-state information (CSI) and RSSI,
organizing repeatable experiments, and running small analysis and classification
workflows.

The implemented hardware profile is an `esp32-s3-devkitc-1` TX/RX pair on
2.4 GHz, using ESP-IDF v5.5.3 and Espressif's `esp-csi` `csi_send` and
`csi_recv` examples. The Python tools also support Linux, macOS, and Windows
serial-device naming.

> **Status:** research prototype. Parsing, configuration, dataset layout,
> feature extraction, and CLI behavior are unit tested. Physical capture,
> firmware flashing, radio measurements, and model quality require the target
> boards and have not been validated by CI. Do not use this project for safety-
> critical sensing.

## Data flow

```text
csi_capture/              # Python capture/parser/experiment modules
scripts/                  # Operational scripts (TX/RX, local setup)
tools/                    # Analysis scripts
tests/                    # Unit tests
docs/                     # Reusable platform and architecture docs
docs/design/plantuml/     # PlantUML design source + rendered PNG diagrams
```

Concrete plans and privacy-safe sample profiles live in the registered study at
`../studies/csi_capture_characterization/`. Raw runs, datasets, generated
analysis, and artifacts live only under
`../../private/experiments/csi_capture_characterization/`.

## Repository layout

```text
csi_capture/   Python parser, capture, experiment, dataset, and model code
scripts/       board and protocol helpers
tools/         CLI wrapper, radio survey, and offline analysis programs
tests/         hardware-independent unit and CLI tests
docs/          designs, experiment notes, runbooks, and sample configurations
data/          small example CSI captures
out/           ignored local reports and figures
experiments/   ignored local raw captures
```

This runs the full distance, stability, angle, static-gesture, and obstacle
analyzers, then writes a consolidated suite summary under the private study's
`analysis/` directory.

- Python 3.10 or newer
- Two ESP32-S3 DevKitC-1 boards for capture
- ESP-IDF v5.5.3 with the `esp32s3` tools installed
- [Espressif esp-csi](https://github.com/espressif/esp-csi)
- Serial-port access (on Linux, usually membership in the `dialout` group)

Install the Python dependencies in a virtual environment:

```bash
git clone https://github.com/Sage-Cat/csi_capturing_example.git
cd csi_capturing_example
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
make test
```

For Windows, activate the environment with `.venv\Scripts\activate` and use
`py -3` where the examples use `python3`.

## Firmware setup

Install ESP-IDF and clone `esp-csi` outside this repository:

```bash
mkdir -p "$HOME/esp"
cd "$HOME/esp"
git clone -b v5.5.3 --recursive https://github.com/espressif/esp-idf.git
cd esp-idf
./install.sh esp32s3
cd ..
git clone https://github.com/espressif/esp-csi.git
```

The shell helpers use `$IDF_PATH` and `$ESP_CSI_PATH` when set, otherwise they
look under `$HOME/esp/esp-idf` and `$HOME/esp/esp-csi`.

## Capture

Connect one board to each laptop. The TX helper builds and flashes `csi_send`:

```bash
./scripts/run_tx_laptop.sh --port /dev/ttyACM0
```

The RX helper builds and flashes `csi_recv`, then records a distance run:

```bash
./scripts/run_rx_laptop.sh \
  --port /dev/ttyACM1 \
  --exp-id demo_distance \
  --scenario LoS \
  --run-id 1 \
  --distance-m 1.0 \
  --max-records 2500
```

Use `/dev/cu.usbmodem*` on macOS or `COM4`-style names on Windows. The unified
CLI can inspect detected devices and supported profiles:

```bash
./tools/exp --list-devices
./tools/exp --list-target-profiles
./tools/exp --list-experiments
```

By default, RX output is stored under `../../private/experiments/csi_capture_characterization/runs/<exp_id>/...`.

New unified config-driven runner (distance + angle):

```bash
python3 -m csi_capture.experiment distance \
  --config ../studies/csi_capture_characterization/configs/distance_capture.sample.json \
  --target-profile esp32s3_csi_v1

python3 -m csi_capture.experiment angle \
  --config ../studies/csi_capture_characterization/configs/angle_radial_45deg_2runs.sample.json \
  --device auto
```

For operator-paced angle capture, add `--wait-enter` when using the direct CLI
arguments shown by `python3 -m csi_capture.experiment angle --help`.

Static-sign capture and model training:

```bash
./tools/exp capture --experiment static_sign_v1 \
  --label baseline --runs 5 --duration 20s --device auto
./tools/exp capture --experiment static_sign_v1 \
  --label hands_up --runs 5 --duration 20s --device auto

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
../studies/csi_capture_characterization/legacy/scripts/run_static_sign_protocol.sh \
  --device /dev/esp32_csi \
  --target-profile esp32s3_csi_v1 \
  --dataset-id replace_with_private_dataset_code \
  --runs 5 \
  --duration 20s \
  --subject-id replace_with_private_participant_code \
  --environment-id replace_with_private_site_code

# Validate future-ready config shape for a new experiment family
./tools/exp validate-config \
  --experiment presence_v1 \
  --mode capture \
  --config ../studies/csi_capture_characterization/configs/presence_v1.capture.sample.json

# Interference protocol (cross-platform Python entrypoint)
python3 -m csi_capture.interference_protocol --list-scenarios
python3 -m csi_capture.interference_protocol --device auto --scenario-set core --runs 3 --max-records 1500
```

See [the experiment documentation](docs/experiments/README.md) for protocol
details and the two-laptop runbook.

- `../studies/csi_capture_characterization/runbooks/interference_v1_windows_workflow.md`

Device selection precedence for `tools/exp capture`:

1. `--device`
2. env var `CSI_CAPTURE_DEVICE` (or `ESP32_CSI_DEVICE`)
3. `/dev/esp32_csi` if present, otherwise auto-detected serial candidate

For complete AP+RX two-laptop setup instructions, see:

- `../studies/csi_capture_characterization/runbooks/static_sign_v1_two_laptop_workflow.md`

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
  - `../../private/experiments/csi_capture_characterization/runs/<exp_id>/meta.json`
  - `../../private/experiments/csi_capture_characterization/runs/<exp_id>/<scenario>/run_<run_id>/distance_<X>m.jsonl`
- Unified runner layout:
  - `../../private/experiments/csi_capture_characterization/runs/<exp_id>/<experiment_type>/run_<run_id>/manifest.json`
  - `../../private/experiments/csi_capture_characterization/runs/<exp_id>/<experiment_type>/run_<run_id>/trial_<trial_id>/capture.jsonl`

Every unified runner invocation writes a per-run `manifest.json` with config snapshot, git revision, device path, and trial summaries.

## 6) Analysis Commands

Distance measurement:

```bash
python3 tools/analyze_wifi_distance_measurement.py \
  --data_dir ../../private/experiments/csi_capture_characterization/runs/<exp_id> \
  --out_dir ../../private/experiments/csi_capture_characterization/analysis/distance_measurement
```

`make analyze-suite DATA_DIR=experiments` runs the repository's expected
distance, stability, angle, static-gesture, and obstacle dataset analyzers. It
requires those local datasets; they are intentionally not distributed here.

```bash
python3 tools/analyze_wifi_stability_statistics.py \
  --data_dir ../../private/experiments/csi_capture_characterization/runs/<exp_id> \
  --out_dir ../../private/experiments/csi_capture_characterization/analysis/stability_statistics
```

CSI can reveal occupancy, motion, posture, and environmental characteristics.
Capture only with informed permission, minimize identifiers, and review JSONL,
CSV, manifests, and radio-survey reports before sharing. Those files can include
timestamps, source MAC addresses, subject/environment labels, device paths,
hostnames, SSIDs, and BSSIDs. Local capture and output directories are ignored,
but that does not sanitize files copied elsewhere.

```bash
python3 tools/analyze_wifi_angle_dataset.py \
  --data_dir ../../private/experiments/csi_capture_characterization/runs/<exp_id>/angle \
  --out_dir ../../private/experiments/csi_capture_characterization/analysis/angle_dataset
```

Model artifacts use Python `pickle`. **Load only artifacts you created or trust:**
opening an untrusted pickle can execute arbitrary code. The diagram rendering
helper sends PlantUML source to the public Kroki service; do not render diagrams
containing confidential information with it.

## License

Survey output is written only to the explicit output path selected for the
private study; repository-local generated output is not a canonical data
surface.

Static sign train/eval:

```bash
./tools/exp train \
  --experiment static_sign_v1 \
  --dataset ../../private/experiments/csi_capture_characterization/datasets/static_sign_v1/<dataset_id> \
  --model svm_linear \
  --window 1s \
  --overlap 0.5

./tools/exp eval \
  --experiment static_sign_v1 \
  --dataset ../../private/experiments/csi_capture_characterization/datasets/static_sign_v1/<dataset_id> \
  --model artifacts/static_sign_v1/<stamp>/svm_linear.pkl \
  --report ../../private/experiments/csi_capture_characterization/analysis/static_sign_v1/report.json
```

## 7) Make Targets

```bash
make setup-vscode
make test
make tx-node PORT=/dev/ttyACM0
make rx-smoke PORT=/dev/ttyACM1 EXP_ID=exp_smoke
make experiment-distance DISTANCE_CONFIG=../studies/csi_capture_characterization/configs/distance_capture.sample.json
make experiment-angle ANGLE_CONFIG=../studies/csi_capture_characterization/configs/angle_radial_45deg_2runs.sample.json
make analyze-distance DATA_DIR=../../private/experiments/csi_capture_characterization/runs/<exp_id>
make survey-24ghz
make analyze-stability DATA_DIR=../../private/experiments/csi_capture_characterization/runs/<exp_id>
make analyze-angle DATA_DIR=../../private/experiments/csi_capture_characterization/runs/<exp_id>/angle
make analyze-all DATA_DIR=../../private/experiments/csi_capture_characterization/runs/<exp_id>
```

## 8) Documentation Index

- Experiment framework docs: `docs/experiments/README.md`
- Requirements: `docs/experiments/requirements.md`
- Design package (PlantUML + PNG): `docs/design/plantuml/README.md`
- Validation report: `docs/experiments/validation_report.md`
- UA playbooks:
  - `../studies/csi_capture_characterization/protocols/DISTANCE_EXPERIMENT_UA.md`
  - `../studies/csi_capture_characterization/protocols/ANGLE_EXPERIMENT_UA.md`
  - `../studies/csi_capture_characterization/protocols/EXPERIMENT_STATIC_SIGN_UA.md`
  - `../studies/csi_capture_characterization/protocols/EXPERIMENTS_PLAN_UA.md`
