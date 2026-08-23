PYTHON ?= python3
PORT ?= /dev/ttyACM1
SESSION_OUTPUT ?=
DATA_DIR ?=

.PHONY: test capture analyze-distance analyze-stability analyze-angle analyze-suite survey-24ghz

test:
	$(PYTHON) -m unittest discover -s tests -p "test_*.py" -v

capture:
	@test -n "$(SESSION_OUTPUT)" || (echo "Set SESSION_OUTPUT to a session-owned file path" >&2; exit 2)
	$(PYTHON) -m csi_capture.capture -p $(PORT) -o $(SESSION_OUTPUT) --format jsonl

analyze-distance:
	@test -n "$(DATA_DIR)" -a -n "$(SESSION_OUTPUT)" || (echo "Set DATA_DIR and SESSION_OUTPUT to session-owned paths" >&2; exit 2)
	$(PYTHON) tools/analyze_wifi_distance_measurement.py --data_dir $(DATA_DIR) --out_dir $(SESSION_OUTPUT) --seed 42

analyze-stability:
	@test -n "$(DATA_DIR)" -a -n "$(SESSION_OUTPUT)" || (echo "Set DATA_DIR and SESSION_OUTPUT to session-owned paths" >&2; exit 2)
	$(PYTHON) tools/analyze_wifi_stability_statistics.py --data_dir $(DATA_DIR) --out_dir $(SESSION_OUTPUT) --seed 42

analyze-angle:
	@test -n "$(DATA_DIR)" -a -n "$(SESSION_OUTPUT)" || (echo "Set DATA_DIR and SESSION_OUTPUT to session-owned paths" >&2; exit 2)
	$(PYTHON) tools/analyze_wifi_angle_dataset.py --data_dir $(DATA_DIR) --out_dir $(SESSION_OUTPUT)

analyze-suite:
	@test -n "$(DATA_DIR)" -a -n "$(SESSION_OUTPUT)" || (echo "Set DATA_DIR and SESSION_OUTPUT to session-owned paths" >&2; exit 2)
	$(PYTHON) tools/analyze_experiment_suite.py --data_root $(DATA_DIR) --out_dir $(SESSION_OUTPUT) --seed 42

survey-24ghz:
	@test -n "$(SESSION_OUTPUT)" || (echo "Set SESSION_OUTPUT to a session-owned directory" >&2; exit 2)
	$(PYTHON) tools/survey_wifi_24ghz.py --out-dir $(SESSION_OUTPUT)
