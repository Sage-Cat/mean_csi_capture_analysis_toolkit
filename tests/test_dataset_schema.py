import json
import tempfile
import unittest
from pathlib import Path

from csi_capture.core.dataset import (
    DatasetValidationError,
    load_labeled_run_dirs,
    validate_run_metadata,
)


class DatasetSchemaTests(unittest.TestCase):
    def _metadata(self) -> dict:
        return {
            "schema_version": 1,
            "experiment_name": "classification-demo",
            "label": "class-a",
            "run_id": "run-001",
            "subject_id": "subject-source-key",
            "environment_id": "environment-a",
            "device": "esp32",
            "serial_dev": "/dev/serial-device",
            "start_time": "2026-01-01T00:00:00Z",
            "end_time": "2026-01-01T00:00:20Z",
            "sampling_params": {"duration_s": 20, "baud": 921600},
            "notes": "capture note",
        }

    def test_validate_run_metadata_uses_caller_contract(self):
        validate_run_metadata(
            self._metadata(),
            expected_experiment="classification-demo",
            allowed_labels=("class-a", "class-b"),
            expected_schema_version=1,
        )

    def test_validate_run_metadata_rejects_label_outside_caller_contract(self):
        metadata = self._metadata()
        metadata["label"] = "class-c"
        with self.assertRaises(DatasetValidationError):
            validate_run_metadata(
                metadata,
                expected_experiment="classification-demo",
                allowed_labels=("class-a", "class-b"),
                expected_schema_version=1,
            )

    def test_explicit_typed_run_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            typed_run = Path(tmp) / "runs" / "run-a"
            root = typed_run / "raw"
            root.mkdir(parents=True)
            (root / "metadata.json").write_text(
                json.dumps(self._metadata()),
                encoding="utf-8",
            )
            (root / "frames.jsonl").write_text(
                "\n".join(
                    [
                        json.dumps({"timestamp": 1, "csi": [1, 2, 3, 4], "rssi": -20}),
                        json.dumps({"timestamp": 2, "csi": [2, 3, 4, 5], "rssi": -21}),
                    ]
                )
                + "\n",
                encoding="utf-8",
            )

            runs = load_labeled_run_dirs(
                [typed_run],
                experiment_name="classification-demo",
                labels=("class-a", "class-b"),
                schema_version=1,
            )
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0].metadata["label"], "class-a")
            self.assertEqual(runs[0].metadata["selected_run_id"], "run-a")
            self.assertEqual(len(runs[0].frames), 2)

    def test_explicit_typed_run_rejects_batch_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            typed_run = Path(tmp) / "runs" / "batch-wrapper"
            for index, label in enumerate(("class-a", "class-b"), start=1):
                raw = typed_run / "raw" / f"capture-{index}"
                raw.mkdir(parents=True)
                metadata = self._metadata()
                metadata["run_id"] = f"source-{index}"
                metadata["label"] = label
                (raw / "metadata.json").write_text(json.dumps(metadata), encoding="utf-8")
                (raw / "frames.jsonl").write_text(
                    json.dumps({"timestamp": index, "csi": [1, 2], "rssi": -20}) + "\n",
                    encoding="utf-8",
                )

            with self.assertRaisesRegex(DatasetValidationError, "exactly one labeled capture"):
                load_labeled_run_dirs(
                    [typed_run],
                    experiment_name="classification-demo",
                    labels=("class-a", "class-b"),
                    schema_version=1,
                )


if __name__ == "__main__":
    unittest.main()
