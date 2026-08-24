from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from csi_capture.analysis.common import discover_test_case_files


class ExperimentRunDiscoveryTests(unittest.TestCase):
    def test_selects_only_runs_from_the_requested_test_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            for run_id, test_case_id in (("run-a", "distance"), ("run-b", "stability")):
                run = runs / run_id
                run.mkdir(parents=True)
                (run / "run.toml").write_text(
                    f'test_case_id = "{test_case_id}"\n', encoding="utf-8"
                )
                (run / f"{run_id}.csv").write_text("value\n1\n", encoding="utf-8")

            selected = discover_test_case_files(runs, "distance", suffixes={".csv"})

            self.assertEqual([path.name for path in selected], ["run-a.csv"])

    def test_rejects_unknown_test_case(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            run = runs / "run-a"
            run.mkdir(parents=True)
            (run / "run.toml").write_text('test_case_id = "distance"\n', encoding="utf-8")
            (run / "data.csv").write_text("value\n1\n", encoding="utf-8")

            with self.assertRaisesRegex(FileNotFoundError, "Unknown"):
                discover_test_case_files(runs, "unknown", suffixes={".csv"})

    def test_allows_an_empty_matching_run_when_another_has_data(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            runs = Path(temporary) / "runs"
            for run_id in ("run-a", "run-b"):
                run = runs / run_id
                run.mkdir(parents=True)
                (run / "run.toml").write_text(
                    'test_case_id = "distance"\n', encoding="utf-8"
                )
            (runs / "run-b/data.csv").write_text("value\n1\n", encoding="utf-8")

            selected = discover_test_case_files(runs, "distance", suffixes={".csv"})

            self.assertEqual([path.name for path in selected], ["data.csv"])


if __name__ == "__main__":
    unittest.main()
