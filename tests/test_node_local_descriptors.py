import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools/export_node_local_descriptors.py"


class NodeLocalDescriptorExportTests(unittest.TestCase):
    def test_export_is_deterministic_and_records_resources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "archive"
            source_dir = archive / "sources/source-a"
            source_dir.mkdir(parents=True)
            (archive / "manifest.json").write_text("{}\n", encoding="utf-8")
            (archive / "SHA256SUMS").write_text("fixture\n", encoding="utf-8")
            chunk = source_dir / "chunk.ndjson.gz"
            with gzip.open(chunk, "wt", encoding="utf-8") as handle:
                for index in range(160):
                    raw = f'CSI_DATA,{index},aa:bb,-{40 + index % 3},"[1,2,3,4]"'
                    handle.write(json.dumps({
                        "source_id": "source-a",
                        "session_id": "session-a",
                        "connection_epoch": 1,
                        "ingest_wall_time_ns": 1_800_000_000_000_000_000 + index * 25_000_000,
                        "raw": raw,
                    }) + "\n")
            outputs = []
            for name in ("local", "central"):
                output = root / name
                result = subprocess.run([
                    sys.executable,
                    str(TOOL),
                    "--archive", str(archive),
                    "--output", str(output),
                    "--source-id", "source-a",
                    "--run-id", "run-a",
                    "--node-id", "node-a",
                    "--execution-boundary", "central-replay",
                ], text=True, capture_output=True, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                outputs.append(output)
            self.assertEqual(
                (outputs[0] / "descriptors.ndjson").read_bytes(),
                (outputs[1] / "descriptors.ndjson").read_bytes(),
            )
            manifest = json.loads((outputs[0] / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["counts"]["selected_csi_records"], 160)
            self.assertEqual(manifest["counts"]["malformed_selected_csi_records"], 0)
            self.assertEqual(manifest["counts"]["descriptor_windows"], 2)
            self.assertEqual(len(manifest["feature_order"]), 12)
            self.assertGreater(manifest["resource_accounting"]["cpu_time_ns"], 0)
            self.assertGreater(manifest["descriptor_bytes"], 0)

    def test_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / "archive"
            archive.mkdir()
            output = root / "existing"
            output.mkdir()
            result = subprocess.run([
                sys.executable,
                str(TOOL),
                "--archive", str(archive),
                "--output", str(output),
                "--source-id", "source-a",
                "--run-id", "run-a",
                "--node-id", "node-a",
                "--execution-boundary", "central-replay",
            ], text=True, capture_output=True, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("outputs are immutable", result.stderr)


if __name__ == "__main__":
    unittest.main()
