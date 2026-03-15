PYTHON ?= python3
PORT ?= /dev/ttyACM1
BAUD ?= 921600
DATASET_ID ?= $$(date -u +%Y%m%d)
RUNS ?= 5
DURATION ?= 20s
SUBJECT_ID ?= subject01
ENVIRONMENT_ID ?= labA
EXP_ID ?= smoke_$(shell date +%Y%m%d_%H%M%S)
SCENARIO ?= LoS
RUN_ID ?= 1
DISTANCE_M ?= 1.0
MAX_RECORDS ?= 20
DISTANCE_CONFIG ?= docs/configs/distance_capture.sample.json
ANGLE_CONFIG ?= docs/configs/angle_capture.sample.json
DATA_DIR ?= experiments
OUT_DIR ?= out

.PHONY: test setup-vscode capture tx-node rx-node rx-smoke static-sign-protocol static-sign-train-eval experiment-distance experiment-angle exp-help exp-list-devices exp-list-target-profiles exp-dry-run render-design analyze-distance analyze-stability analyze-angle analyze-all

setup-vscode:
	@echo "Open /home/sagecat/Projects/research-workspace in VS Code for the shared workspace configuration."

test:
	$(PYTHON) -m unittest discover -s tests -p "test_*.py" -v

capture:
	$(PYTHON) -m csi_capture.capture -p $(PORT) -b $(BAUD) -o experiments/manual/csi_capture.jsonl --format jsonl

tx-node:
	./scripts/run_tx_laptop.sh --port $(PORT)

rx-node:
	./scripts/run_rx_csi_node.sh --port $(PORT)

rx-smoke:
	./scripts/run_rx_laptop.sh --port $(PORT) --exp-id $(EXP_ID) --scenario $(SCENARIO) --run-id $(RUN_ID) --distance-m $(DISTANCE_M) --max-records $(MAX_RECORDS) --skip-build --skip-flash

static-sign-protocol:
	./scripts/run_static_sign_protocol.sh --device $(PORT) --dataset-id $(DATASET_ID) --runs $(RUNS) --duration $(DURATION) --subject-id $(SUBJECT_ID) --environment-id $(ENVIRONMENT_ID)

static-sign-train-eval:
	./scripts/run_static_sign_train_eval.sh --dataset-id $(DATASET_ID)

experiment-distance:
	$(PYTHON) -m csi_capture.experiment distance --config $(DISTANCE_CONFIG)

experiment-angle:
	$(PYTHON) -m csi_capture.experiment angle --config $(ANGLE_CONFIG)

exp-help:
	./tools/exp --help

exp-list-devices:
	./tools/exp --list-devices

exp-list-target-profiles:
	./tools/exp --list-target-profiles

exp-dry-run:
	./tools/exp capture --experiment static_sign_v1 --dry-run-packets $(MAX_RECORDS) --dry-run-timeout 10s --device $(PORT)

render-design:
	./scripts/generate_plantuml_pngs.sh

analyze-distance:
	$(PYTHON) tools/analyze_wifi_distance_measurement.py --data_dir $(DATA_DIR) --out_dir $(OUT_DIR)/distance_measurement --seed 42

analyze-stability:
	$(PYTHON) tools/analyze_wifi_stability_statistics.py --data_dir $(DATA_DIR) --out_dir $(OUT_DIR)/stability_statistics --seed 42

analyze-angle:
	$(PYTHON) tools/analyze_wifi_angle_dataset.py --data_dir $(DATA_DIR) --out_dir $(OUT_DIR)/angle_dataset

analyze-all: analyze-distance analyze-stability analyze-angle
