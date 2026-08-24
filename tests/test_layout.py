import unittest
from pathlib import Path

from csi_capture.core.layout import LAYOUT_CANONICAL_V1, build_run_layout


class LayoutTests(unittest.TestCase):
    def test_build_run_layout_canonical(self):
        layout = build_run_layout(
            root=Path("session-owned-root"),
            experiment_id="experiment-a",
            dataset_id="dataset-001",
            run_id="001",
        )
        self.assertEqual(layout.layout_style, LAYOUT_CANONICAL_V1)
        self.assertEqual(
            layout.run_dir,
            Path("session-owned-root") / "experiment-a" / "dataset-001" / "runs" / "run_001",
        )
        self.assertEqual(
            layout.trial_paths("trial-a").packet_path,
            Path("session-owned-root")
            / "experiment-a"
            / "dataset-001"
            / "runs"
            / "run_001"
            / "trials"
            / "trial_trial-a"
            / "packets.jsonl",
        )

    def test_trial_path_rejects_unknown_packet_format(self):
        layout = build_run_layout(
            root=Path("session-owned-root"),
            experiment_id="experiment-a",
            dataset_id="dataset-001",
            run_id="001",
        )
        with self.assertRaises(ValueError):
            layout.trial_paths("trial-a", output_format="parquet")


if __name__ == "__main__":
    unittest.main()
