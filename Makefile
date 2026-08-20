PYTHON ?= python3
PORT ?= /dev/ttyACM1
BAUD ?= 921600
DATASET_ID ?= $$(date -u +%Y%m%d)
RUNS ?= 5
DURATION ?= 20s
SUBJECT_ID ?= subject_example
ENVIRONMENT_ID ?= site_example
EXP_ID ?= smoke_$(shell date +%Y%m%d_%H%M%S)
SCENARIO ?= LoS
RUN_ID ?= 1
DISTANCE_M ?= 1.0
MAX_RECORDS ?= 20
STUDY_ROOT ?= ../studies/csi_capture_characterization
PRIVATE_ROOT ?= ../../private/experiments/csi_capture_characterization
DISTANCE_CONFIG ?= $(STUDY_ROOT)/configs/distance_capture.sample.json
ANGLE_CONFIG ?= $(STUDY_ROOT)/configs/angle_capture.sample.json
DATA_DIR ?= $(PRIVATE_ROOT)/runs
OUT_DIR ?= $(PRIVATE_ROOT)/analysis

.PHONY: test setup-vscode capture tx-node rx-node rx-smoke static-sign-protocol static-sign-train-eval experiment-distance experiment-angle exp-help exp-list-devices exp-list-target-profiles exp-dry-run render-design analyze-distance analyze-stability analyze-angle analyze-suite analyze-all survey-24ghz

setup-vscode:
	code .

test:
	$(PYTHON) -m unittest discover -s tests -p "test_*.py" -v

capture:
	$(PYTHON) -m csi_capture.capture -p $(PORT) -b $(BAUD) -o $(PRIVATE_ROOT)/runs/manual/csi_capture.jsonl --format jsonl

tx-node:
	./scripts/run_tx_laptop.sh --port $(PORT)

rx-node:
	./scripts/run_rx_csi_node.sh --port $(PORT)

rx-smoke:
	./scripts/run_rx_laptop.sh --port $(PORT) --exp-id $(EXP_ID) --scenario $(SCENARIO) --run-id $(RUN_ID) --distance-m $(DISTANCE_M) --max-records $(MAX_RECORDS) --skip-build --skip-flash

static-sign-protocol:
	$(STUDY_ROOT)/legacy/scripts/run_static_sign_protocol.sh --device $(PORT) --dataset-root $(PRIVATE_ROOT)/datasets/static_sign_v1 --dataset-id $(DATASET_ID) --runs $(RUNS) --duration $(DURATION) --subject-id $(SUBJECT_ID) --environment-id $(ENVIRONMENT_ID)

static-sign-train-eval:
	$(STUDY_ROOT)/legacy/scripts/run_static_sign_train_eval.sh --dataset-root $(PRIVATE_ROOT)/datasets/static_sign_v1 --dataset-id $(DATASET_ID) --artifact $(PRIVATE_ROOT)/artifacts/static_sign_v1/$(DATASET_ID)/svm_linear.pkl --report $(PRIVATE_ROOT)/analysis/static_sign_v1/$(DATASET_ID)/eval_report.json

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

analyze-suite:
	$(PYTHON) tools/analyze_experiment_suite.py --data_root $(DATA_DIR) --out_dir $(OUT_DIR) --seed 42

survey-24ghz:
	$(PYTHON) tools/survey_wifi_24ghz.py --focus-channel 11 --samples 3 --interval-s 2.0

analyze-all: analyze-suite
