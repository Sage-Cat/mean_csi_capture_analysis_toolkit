import io
import json
import unittest

from csi_capture.capture import capture_stream


class CaptureStreamTests(unittest.TestCase):
    def test_capture_jsonl_only_csi_lines(self):
        lines = [
            "I (123) csi_recv: compensate_gain 4.3\n",
            'CSI_DATA,2,aa:bb:cc:dd:ee:ff,-30,11,1,0,1,1,1,0,0,0,0,-97,0,11,2,10,0,47,0,384,0,"[5,6,-7]"\n',
            "random line\n",
        ]

        out = io.StringIO()
        written = capture_stream(lines, out, output_format="jsonl")
        self.assertEqual(written, 1)

        payload = json.loads(out.getvalue().strip())
        self.assertIn("timestamp", payload)
        self.assertEqual(payload["rssi"], -30)
        self.assertEqual(payload["csi"], [5, 6, -7])

    def test_capture_csv_with_max_records(self):
        lines = [
            'CSI_DATA,2,aa:bb:cc:dd:ee:ff,-30,11,1,0,1,1,1,0,0,0,0,-97,0,11,2,10,0,47,0,384,0,"[1]"\n',
            'CSI_DATA,3,aa:bb:cc:dd:ee:11,-31,11,1,0,1,1,1,0,0,0,0,-97,0,11,2,10,0,47,0,384,0,"[2]"\n',
        ]
        out = io.StringIO()
        written = capture_stream(lines, out, output_format="csv", max_records=1)
        self.assertEqual(written, 1)
        text = out.getvalue().splitlines()
        self.assertEqual(len(text), 2)  # header + one row
        self.assertIn("timestamp,rssi,csi,esp_timestamp,mac", text[0])

    def test_capture_jsonl_with_metadata(self):
        lines = [
            'CSI_DATA,2,aa:bb:cc:dd:ee:ff,-30,11,1,0,1,1,1,0,0,0,0,-97,0,11,2,10,0,47,0,384,0,"[5,6,-7]"\n',
        ]
        out = io.StringIO()
        written = capture_stream(
            lines,
            out,
            output_format="jsonl",
            metadata={
                "experiment_id": "experiment-a",
                "run_id": "run-001",
                "ground_truth": {"quantity": 2.0},
            },
        )
        self.assertEqual(written, 1)
        payload = json.loads(out.getvalue().strip())
        self.assertEqual(payload["experiment_id"], "experiment-a")
        self.assertEqual(payload["run_id"], "run-001")
        self.assertEqual(payload["ground_truth"], {"quantity": 2.0})

    def test_capture_jsonl_with_zero_max_records(self):
        lines = [
            'CSI_DATA,2,aa:bb:cc:dd:ee:ff,-30,11,1,0,1,1,1,0,0,0,0,-97,0,11,2,10,0,47,0,384,0,"[5,6,-7]"\n',
        ]
        out = io.StringIO()
        written = capture_stream(lines, out, output_format="jsonl", max_records=0)
        self.assertEqual(written, 0)
        self.assertEqual(out.getvalue(), "")

    def test_capture_rejects_invalid_output_format(self):
        with self.assertRaisesRegex(ValueError, "Unsupported output_format"):
            capture_stream([], io.StringIO(), output_format="yaml")

    def test_capture_rejects_negative_max_records(self):
        with self.assertRaisesRegex(ValueError, "max_records must be >= 0"):
            capture_stream([], io.StringIO(), output_format="jsonl", max_records=-1)


if __name__ == "__main__":
    unittest.main()
