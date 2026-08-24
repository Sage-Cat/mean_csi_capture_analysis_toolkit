PYTHON ?= python3
PORT ?= /dev/ttyACM1
SESSION_OUTPUT ?=
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
	@test -n "$(SESSION_OUTPUT)" || (echo "Set SESSION_OUTPUT to a session-owned file path" >&2; exit 2)
	$(PYTHON) -m csi_capture.capture -p $(PORT) -o $(SESSION_OUTPUT) --format jsonl

analyze-classification:
	@test -n "$(DATA_DIR)" -a -n "$(SESSION_OUTPUT)" -a -n "$(EXPERIMENT_ID)" -a -n "$(RUN_IDS)" -a -n "$(LABELS)" -a -n "$(POSITIVE_LABEL)" || (echo "Set DATA_DIR, SESSION_OUTPUT, EXPERIMENT_ID, RUN_IDS, LABELS, and POSITIVE_LABEL" >&2; exit 2)
	$(PYTHON) tools/analyze_wifi_classification.py --data_dir $(DATA_DIR) --out_dir $(SESSION_OUTPUT) --experiment_id $(EXPERIMENT_ID) $(foreach id,$(RUN_IDS),--run_id $(id)) $(if $(SUBJECT_MAP),--subject-map $(SUBJECT_MAP),) --labels $(LABELS) --positive_label $(POSITIVE_LABEL) --seed 42

analyze-distance:
	@test -n "$(DATA_DIR)" -a -n "$(SESSION_OUTPUT)" || (echo "Set DATA_DIR and SESSION_OUTPUT to session-owned paths" >&2; exit 2)
	$(PYTHON) tools/analyze_wifi_distance_measurement.py --data_dir $(DATA_DIR) --out_dir $(SESSION_OUTPUT) --seed 42

analyze-stability:
	@test -n "$(DATA_DIR)" -a -n "$(SESSION_OUTPUT)" || (echo "Set DATA_DIR and SESSION_OUTPUT to session-owned paths" >&2; exit 2)
	$(PYTHON) tools/analyze_wifi_stability_statistics.py --data_dir $(DATA_DIR) --out_dir $(SESSION_OUTPUT) --seed 42

analyze-angle:
	@test -n "$(DATA_DIR)" -a -n "$(SESSION_OUTPUT)" || (echo "Set DATA_DIR and SESSION_OUTPUT to session-owned paths" >&2; exit 2)
	$(PYTHON) tools/analyze_wifi_angle_dataset.py --data_dir $(DATA_DIR) --out_dir $(SESSION_OUTPUT)

survey-24ghz:
	@test -n "$(SESSION_OUTPUT)" || (echo "Set SESSION_OUTPUT to a session-owned directory" >&2; exit 2)
	$(PYTHON) tools/survey_wifi_24ghz.py --out-dir $(SESSION_OUTPUT)
