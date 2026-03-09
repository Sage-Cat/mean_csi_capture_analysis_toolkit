import subprocess
import sys
import unittest


class InterferenceProtocolCLITests(unittest.TestCase):
    def test_list_scenarios_core_returns_zero(self):
        proc = subprocess.run(
            [sys.executable, "-m", "csi_capture.interference_protocol", "--list-scenarios"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("s01_ref_los_empty", proc.stdout)
        self.assertIn("s09_boxes_wood_partition", proc.stdout)

    def test_list_scenarios_full_includes_extended_cases(self):
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "csi_capture.interference_protocol",
                "--scenario-set",
                "full",
                "--list-scenarios",
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.assertEqual(proc.returncode, 0)
        self.assertIn("s13_boxes_and_human", proc.stdout)


if __name__ == "__main__":
    unittest.main()
