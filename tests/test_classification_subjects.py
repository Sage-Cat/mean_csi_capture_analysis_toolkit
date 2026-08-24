import json
import tempfile
import unittest
from pathlib import Path

from tools.analyze_wifi_classification import (
    _opaque_subject_mapping,
    _subject_group_token,
    load_subject_map,
)


class ClassificationSubjectMappingTests(unittest.TestCase):
    def test_exact_map_and_opaque_ids_are_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subject-map.json"
            path.write_text(
                json.dumps(
                    {
                        "run-a": "private-token-z",
                        "run-b": "private-token-a",
                        "run-c": "private-token-z",
                    }
                ),
                encoding="utf-8",
            )
            subject_map = load_subject_map(
                path,
                selected_run_ids=("run-a", "run-b", "run-c"),
            )

        self.assertIsNotNone(subject_map)
        opaque = _opaque_subject_mapping(set(subject_map.values()))
        self.assertEqual(opaque["private-token-a"], "subject-001")
        self.assertEqual(opaque["private-token-z"], "subject-002")
        self.assertTrue(all(value.startswith("subject-") for value in opaque.values()))

    def test_map_requires_exact_run_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "subject-map.json"
            path.write_text(
                json.dumps({"run-a": "group-a", "run-extra": "group-b"}),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "exactly match selected run IDs"):
                load_subject_map(path, selected_run_ids=("run-a", "run-b"))

    def test_notes_never_supply_identity(self):
        metadata = {
            "selected_run_id": "run-a",
            "subject_id": "explicit-subject",
            "notes": "untrusted-note-token | arbitrary text",
        }
        self.assertEqual(
            _subject_group_token(metadata, subject_map=None),
            "explicit-subject",
        )
        metadata["subject_id"] = None
        self.assertIsNone(_subject_group_token(metadata, subject_map=None))
        self.assertEqual(
            _subject_group_token(metadata, subject_map={"run-a": "mapped-private-token"}),
            "mapped-private-token",
        )


if __name__ == "__main__":
    unittest.main()
