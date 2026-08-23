import argparse
import json
import tempfile
import unittest
from pathlib import Path

from csi_capture.core.environment import DEFAULT_ENVIRONMENT_PROFILE_ID
from csi_capture.experiment import (
    ExperimentConfigError,
    build_angle_cli_config,
    load_experiment_config,
)


class ExperimentConfigTests(unittest.TestCase):
    def _load(self, payload: dict):
        payload.setdefault("output_root", "/tmp/session-owned-output")
        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "config.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            return load_experiment_config(path)

    def _angle_cli_args(self, **overrides):
        base = dict(
            config=None,
            device="/dev/esp32_csi",
            target_profile=DEFAULT_ENVIRONMENT_PROFILE_ID,
            exp_id="exp_angle_cli",
            run_id=None,
            run_ids=None,
            runs=2,
            angles=["0", "45", "90"],
            repeats_per_angle=1,
            packets_per_repeat=50,
            duration_s=None,
            output_format="jsonl",
            inter_trial_pause_s=5.0,
            wait_enter=False,
            scenario_tags=["LoS"],
            room_id="room1",
            notes="notes",
            num_antennas=1,
            antenna_spacing_m=None,
            orientation_reference="0 deg points to AP",
            measurement_positions="AP center, RX on circle",
            output_root="experiments",
            baud=921600,
            timeout_s=1.0,
            reconnect_on_error=False,
            reconnect_delay_s=1.0,
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_angle_config_builds_trials(self):
        payload = {
            "experiment_type": "angle",
            "exp_id": "exp_angle_test",
            "run_id": "1",
            "capture": {"packets_per_repeat": 10},
            "angle": {
                "angles": [0, 30, 60],
                "repeats_per_angle": 2,
                "array_config": {"num_antennas": 1, "antenna_spacing_m": None},
                "geometry": {
                    "orientation_reference": "0 deg points to AP",
                    "measurement_positions": "AP fixed, receiver rotated",
                },
            },
        }
        _raw, config = self._load(payload)
        self.assertEqual(config.experiment_type, "angle")
        self.assertEqual(config.device.path, "/dev/esp32_csi")
        self.assertEqual(config.target_profile.profile_id, DEFAULT_ENVIRONMENT_PROFILE_ID)
        self.assertEqual(len(config.trials), 6)
        self.assertIn("angle_deg", config.trials[0].ground_truth)
        self.assertEqual(config.angle_array_config["num_antennas"], 1)
        self.assertEqual(config.capture.inter_trial_pause_s, 0.0)
        self.assertFalse(config.capture.wait_for_enter_between_trials)

    def test_distance_config_builds_trials(self):
        payload = {
            "experiment_type": "distance",
            "exp_id": "exp_distance_test",
            "run_id": "1",
            "capture": {"packets_per_repeat": 5},
            "distance": {"distances_m": [1.0, 2.0], "repeats_per_distance": 3},
            "scenario_tags": ["LoS"],
        }
        _raw, config = self._load(payload)
        self.assertEqual(config.experiment_type, "distance")
        self.assertEqual(len(config.trials), 6)
        self.assertEqual(config.trials[0].ground_truth["distance_m"], 1.0)
        self.assertEqual(config.scenario_tags, ["LoS"])
        self.assertEqual(config.run_ids, ["1"])

    def test_config_accepts_multiple_run_ids(self):
        payload = {
            "experiment_type": "angle",
            "exp_id": "exp_angle_test",
            "run_id": "legacy_fallback",
            "run_ids": ["run_a", "run_b"],
            "capture": {"packets_per_repeat": 10},
            "angle": {
                "angles": [0, 45, 90],
                "repeats_per_angle": 1,
                "array_config": {"num_antennas": 1, "antenna_spacing_m": None},
                "geometry": {
                    "orientation_reference": "0 deg points to AP",
                    "measurement_positions": "AP center, receiver on circle",
                },
            },
        }
        _raw, config = self._load(payload)
        self.assertEqual(config.run_ids, ["run_a", "run_b"])

    def test_config_rejects_empty_run_ids(self):
        payload = {
            "experiment_type": "angle",
            "exp_id": "exp_angle_test",
            "run_ids": [],
            "capture": {"packets_per_repeat": 10},
            "angle": {
                "angles": [0, 45, 90],
                "repeats_per_angle": 1,
                "array_config": {"num_antennas": 1, "antenna_spacing_m": None},
                "geometry": {
                    "orientation_reference": "0 deg points to AP",
                    "measurement_positions": "AP center, receiver on circle",
                },
            },
        }
        with self.assertRaises(ExperimentConfigError):
            self._load(payload)

    def test_capture_budget_is_required(self):
        payload = {
            "experiment_type": "distance",
            "exp_id": "exp_distance_test",
            "run_id": "1",
            "capture": {},
            "distance": {"distances_m": [1.0], "repeats_per_distance": 1},
        }
        with self.assertRaises(ExperimentConfigError):
            self._load(payload)

    def test_inter_trial_pause_must_be_non_negative(self):
        payload = {
            "experiment_type": "angle",
            "exp_id": "exp_angle_test",
            "capture": {"packets_per_repeat": 5, "inter_trial_pause_s": -1},
            "angle": {
                "angles": [0],
                "repeats_per_angle": 1,
                "array_config": {"num_antennas": 1, "antenna_spacing_m": None},
                "geometry": {
                    "orientation_reference": "0 deg points to AP",
                    "measurement_positions": "AP center, receiver on circle",
                },
            },
        }
        with self.assertRaises(ExperimentConfigError):
            self._load(payload)

    def test_packets_and_duration_are_mutually_exclusive(self):
        payload = {
            "experiment_type": "distance",
            "exp_id": "exp_distance_test",
            "run_id": "1",
            "capture": {"packets_per_repeat": 5, "duration_s": 1.0},
            "distance": {"distances_m": [1.0], "repeats_per_distance": 1},
        }
        with self.assertRaises(ExperimentConfigError):
            self._load(payload)

    def test_angle_geometry_required(self):
        payload = {
            "experiment_type": "angle",
            "exp_id": "exp_angle_test",
            "run_id": "1",
            "capture": {"packets_per_repeat": 5},
            "angle": {
                "angles": [0],
                "repeats_per_angle": 1,
                "array_config": {"num_antennas": 2, "antenna_spacing_m": 0.05},
                "geometry": {"orientation_reference": "front axis"},
            },
        }
        with self.assertRaises(ExperimentConfigError):
            self._load(payload)

    def test_build_angle_cli_config_generates_runs(self):
        args = self._angle_cli_args()
        raw = build_angle_cli_config(args)
        self.assertEqual(raw["experiment_type"], "angle")
        self.assertEqual(raw["run_ids"], ["001", "002"])
        self.assertFalse(raw["capture"]["wait_for_enter_between_trials"])
        _raw, config = self._load(raw)
        self.assertEqual(config.run_ids, ["001", "002"])
        self.assertEqual(len(config.trials), 3)
        self.assertFalse(config.capture.wait_for_enter_between_trials)

    def test_build_angle_cli_config_sets_wait_for_enter(self):
        args = self._angle_cli_args(wait_enter=True)
        raw = build_angle_cli_config(args)
        self.assertTrue(raw["capture"]["wait_for_enter_between_trials"])
        _raw, config = self._load(raw)
        self.assertTrue(config.capture.wait_for_enter_between_trials)

    def test_build_angle_cli_config_requires_angles(self):
        args = self._angle_cli_args(angles=None)
        with self.assertRaises(ExperimentConfigError):
            build_angle_cli_config(args)

    def test_config_accepts_explicit_target_profile(self):
        payload = {
            "experiment_type": "distance",
            "target_profile": DEFAULT_ENVIRONMENT_PROFILE_ID,
            "exp_id": "exp_distance_test",
            "run_id": "1",
            "capture": {"packets_per_repeat": 5},
            "distance": {"distances_m": [1.0], "repeats_per_distance": 1},
        }
        _raw, config = self._load(payload)
        self.assertEqual(config.target_profile.profile_id, DEFAULT_ENVIRONMENT_PROFILE_ID)

    def test_config_rejects_unknown_target_profile(self):
        payload = {
            "experiment_type": "distance",
            "target_profile": "unknown_profile",
            "exp_id": "exp_distance_test",
            "run_id": "1",
            "capture": {"packets_per_repeat": 5},
            "distance": {"distances_m": [1.0], "repeats_per_distance": 1},
        }
        with self.assertRaises(ExperimentConfigError):
            self._load(payload)

    def test_capture_wait_for_enter_must_be_boolean(self):
        payload = {
            "experiment_type": "angle",
            "exp_id": "exp_angle_test",
            "capture": {"packets_per_repeat": 5, "wait_for_enter_between_trials": "yes"},
            "angle": {
                "angles": [0],
                "repeats_per_angle": 1,
                "array_config": {"num_antennas": 1, "antenna_spacing_m": None},
                "geometry": {
                    "orientation_reference": "0 deg points to AP",
                    "measurement_positions": "AP center, receiver on circle",
                },
            },
        }
        with self.assertRaises(ExperimentConfigError):
            self._load(payload)


if __name__ == "__main__":
    unittest.main()
