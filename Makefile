PYTHON ?= python3
PORT ?= /dev/ttyACM1
EXPERIMENT_OUTPUT ?=
DATA_DIR ?=
EXPERIMENT_ID ?=
LABELS ?=
POSITIVE_LABEL ?=
RUN_IDS ?=
SUBJECT_MAP ?=

.PHONY: test capture analyze-classification analyze-distance analyze-stability analyze-angle survey-24ghz

test:
	$(PYTHON) -m unittest discover -s tests -p "test_*.py" -v

capture:
	@test -n "$(EXPERIMENT_OUTPUT)" || (echo "Set EXPERIMENT_OUTPUT to an experiment-owned file path" >&2; exit 2)
	$(PYTHON) -m csi_capture.capture -p $(PORT) -o $(EXPERIMENT_OUTPUT) --format jsonl

analyze-classification:
	@test -n "$(DATA_DIR)" -a -n "$(EXPERIMENT_OUTPUT)" -a -n "$(EXPERIMENT_ID)" -a -n "$(RUN_IDS)" -a -n "$(LABELS)" -a -n "$(POSITIVE_LABEL)" || (echo "Set DATA_DIR, EXPERIMENT_OUTPUT, EXPERIMENT_ID, RUN_IDS, LABELS, and POSITIVE_LABEL" >&2; exit 2)
	$(PYTHON) tools/analyze_wifi_classification.py --data_dir $(DATA_DIR) --out_dir $(EXPERIMENT_OUTPUT) --experiment_id $(EXPERIMENT_ID) $(foreach id,$(RUN_IDS),--run_id $(id)) $(if $(SUBJECT_MAP),--subject-map $(SUBJECT_MAP),) --labels $(LABELS) --positive_label $(POSITIVE_LABEL) --seed 42

analyze-distance:
	@test -n "$(DATA_DIR)" -a -n "$(EXPERIMENT_OUTPUT)" || (echo "Set DATA_DIR and EXPERIMENT_OUTPUT to experiment-owned paths" >&2; exit 2)
	$(PYTHON) tools/analyze_wifi_distance_measurement.py --data_dir $(DATA_DIR) --out_dir $(EXPERIMENT_OUTPUT) --seed 42

analyze-stability:
	@test -n "$(DATA_DIR)" -a -n "$(EXPERIMENT_OUTPUT)" || (echo "Set DATA_DIR and EXPERIMENT_OUTPUT to experiment-owned paths" >&2; exit 2)
	$(PYTHON) tools/analyze_wifi_stability_statistics.py --data_dir $(DATA_DIR) --out_dir $(EXPERIMENT_OUTPUT) --seed 42

analyze-angle:
	@test -n "$(DATA_DIR)" -a -n "$(EXPERIMENT_OUTPUT)" || (echo "Set DATA_DIR and EXPERIMENT_OUTPUT to experiment-owned paths" >&2; exit 2)
	$(PYTHON) tools/analyze_wifi_angle_dataset.py --data_dir $(DATA_DIR) --out_dir $(EXPERIMENT_OUTPUT)

survey-24ghz:
	@test -n "$(EXPERIMENT_OUTPUT)" || (echo "Set EXPERIMENT_OUTPUT to an experiment-owned directory" >&2; exit 2)
	$(PYTHON) tools/survey_wifi_24ghz.py --out-dir $(EXPERIMENT_OUTPUT)
