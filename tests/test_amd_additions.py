"""AMD UI data parsing and checksum tests without GPU, network, or Qt."""
import hashlib
import tempfile
import unittest
from pathlib import Path

from tools.rvc_amd_assets import verified
from tools.rvc_amd_bridge import VcRsBridge
from tools.rvc_amd_devices import parse_devices


class AmdIntegrationTests(unittest.TestCase):
    def test_device_lists_use_exact_cpal_names(self):
        output = """WASAPI input devices:
  Microphone (USB Audio)
  Line 1 (Virtual Audio Cable)

WASAPI output devices:
  Speakers (Realtek)
  Line 1 (Virtual Audio Cable)
"""
        ins, outs = parse_devices(output)
        self.assertEqual(ins, ["Microphone (USB Audio)", "Line 1 (Virtual Audio Cable)"])
        self.assertEqual(outs, ["Speakers (Realtek)", "Line 1 (Virtual Audio Cable)"])

    def test_metrics_are_distinct_from_end_to_end_latency(self):
        bridge = VcRsBridge()
        bridge._parse_metrics(
            "2026 INFO state=Running chunks=2 infer=25832us processing=39210us "
            "content_delay_ms=44.500 (nominal; excludes devices)"
        )
        m = bridge.snapshot_metrics()
        self.assertAlmostEqual(m["infer_ms"], 25.832)
        self.assertAlmostEqual(m["processing_ms"], 39.210)
        self.assertAlmostEqual(m["content_delay_ms"], 44.5)

    def test_support_files_require_correct_sha_and_size(self):
        with tempfile.TemporaryDirectory() as folder:
            f = Path(folder) / "content_vec_500.onnx"
            payload = b"dummy fixture only"
            f.write_bytes(payload)
            metadata = {"size": len(payload), "sha256": hashlib.sha256(payload).hexdigest()}
            self.assertTrue(verified(f, metadata))
            metadata["sha256"] = "0" * 64
            self.assertFalse(verified(f, metadata))


if __name__ == "__main__":
    unittest.main()
