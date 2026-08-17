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
ESP32-S3 TX (csi_send) --2.4 GHz traffic--> ESP32-S3 RX (csi_recv)
       RX --USB serial/CSI_DATA--> Python capture --> JSONL/CSV + manifest
                                                --> analysis/model reports
```

Captured packet rows contain a host timestamp, ESP timestamp, RSSI, interleaved
CSI I/Q values, source MAC, and experiment metadata. Distance and angle use the
config-driven runner. `static_sign_v1` provides capture, feature extraction,
training, and evaluation. Presence is currently a validation-only placeholder;
the remaining analysis programs operate on previously collected datasets.

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

## Prerequisites

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

Run config-driven distance or angle capture:

```bash
python3 -m csi_capture.experiment distance \
  --config docs/configs/distance_capture.sample.json

python3 -m csi_capture.experiment angle \
  --config docs/configs/angle_radial_45deg_2runs.sample.json \
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

./tools/exp train --experiment static_sign_v1 \
  --dataset data/experiments/static_sign_v1/<dataset_id> \
  --model svm_linear --window 1s --overlap 0.5
```

See [the experiment documentation](docs/experiments/README.md) for protocol
details and the two-laptop runbook.

## Offline analysis

Individual analyzers accept an experiment directory and write ignored output
under `out/`. For example:

```bash
python3 tools/analyze_wifi_distance_measurement.py \
  --data_dir experiments/<exp_id> \
  --out_dir out/distance_measurement
```

`make analyze-suite DATA_DIR=experiments` runs the repository's expected
distance, stability, angle, static-gesture, and obstacle dataset analyzers. It
requires those local datasets; they are intentionally not distributed here.

## Privacy and security

CSI can reveal occupancy, motion, posture, and environmental characteristics.
Capture only with informed permission, minimize identifiers, and review JSONL,
CSV, manifests, and radio-survey reports before sharing. Those files can include
timestamps, source MAC addresses, subject/environment labels, device paths,
hostnames, SSIDs, and BSSIDs. Local capture and output directories are ignored,
but that does not sanitize files copied elsewhere.

The two files in `data/` are small example captures with a locally administered
placeholder MAC address and no subject, SSID, BSSID, or hostname fields.

Model artifacts use Python `pickle`. **Load only artifacts you created or trust:**
opening an untrusted pickle can execute arbitrary code. The diagram rendering
helper sends PlantUML source to the public Kroki service; do not render diagrams
containing confidential information with it.

## License

Original source code and documentation are available under the MIT License; see
[LICENSE](LICENSE). The example captures under `data/` are provided for inspection
only and are excluded from that license grant. External dependencies and
Espressif firmware are governed by their own licenses.
