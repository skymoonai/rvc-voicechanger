"""Unit tests for the vc-rs sidecar (no GPU or Qt required)."""
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools import rvc_amd_bridge as amd


class FakeProcess:
    def __init__(self, *args, **kwargs):
        self.args = args[0]
        self.stdout = io.StringIO("vc-rs test output\\n")
        self.exit_code = None
        self.terminated = False

    def poll(self):
        return self.exit_code

    def terminate(self):
        self.terminated = True
        self.exit_code = 1


class AmdBridgeTests(unittest.TestCase):
    def test_voice_model_onnx_and_matching_pth(self):
        with tempfile.TemporaryDirectory() as dirname:
            onnx = Path(dirname) / "model.onnx"
            onnx.write_bytes(b"fake")
            self.assertEqual(amd.resolve_voice_model(str(onnx)), onnx.resolve())
            self.assertEqual(amd.resolve_voice_model(str(onnx.with_suffix(".pth"))), onnx.resolve())

    def test_missing_onnx_is_explicit(self):
        with self.assertRaisesRegex(amd.VcRsError, "ONNX"):
            amd.resolve_voice_model("model.pth")

    def test_unknown_provider_cannot_be_executed(self):
        with mock.patch.object(amd.sys, "platform", "win32"):
            with self.assertRaises(amd.VcRsError):
                amd.VcRsBridge().start(model="x", embedder="", f0="", provider="arbitrary")

    def test_chunk_validation(self):
        with mock.patch.object(amd.sys, "platform", "win32"):
            for chunk in (0, 25, 2001):
                with self.subTest(chunk=chunk), self.assertRaises(amd.VcRsError):
                    amd.VcRsBridge().start(model="x", embedder="", f0="", chunk_ms=chunk)

    def test_windows_ml_launch_flags_and_stop(self):
        with tempfile.TemporaryDirectory() as dirname:
            voice = Path(dirname) / "voice.onnx"
            support = Path(dirname) / "content_vec_500.onnx"
            f0 = Path(dirname) / "rmvpe.onnx"
            exe = Path(dirname) / "vc-rs.exe"
            for path in (voice, support, f0, exe):
                path.write_bytes(b"dummy")
            spawned = []

            def popen(args, **kwargs):
                p = FakeProcess(args, **kwargs)
                spawned.append(p)
                return p

            with mock.patch.object(amd.sys, "platform", "win32"), \
                 mock.patch.object(amd, "find_executable", return_value=exe), \
                 mock.patch.object(amd.subprocess, "Popen", side_effect=popen):
                controller = amd.VcRsBridge()
                controller.start(model=str(voice), embedder=str(support), f0=str(f0),
                                 input_device="Microphone A", output_device="Cable B",
                                 provider="DirectML", chunk_ms=500, pitch=3)
                args = spawned[0].args
                self.assertEqual(args[:4], [str(exe), "run", "--provider", "windowsml-directml"])
                self.assertIn("--input", args)
                self.assertIn("Microphone A", args)
                self.assertIn("--output", args)
                self.assertIn("Cable B", args)
                self.assertTrue(controller.running)
                controller.stop()
                self.assertFalse(controller.running)
                self.assertTrue(spawned[0].terminated)

    def test_prefs_save_is_local(self):
        with tempfile.TemporaryDirectory() as dirname, \
             mock.patch.object(amd, "SETTINGS_FILE", Path(dirname) / "settings.json"):
            amd.save_preferences({"model": "test.onnx", "provider": "DirectML", "not_allowed": "secret"})
            raw = amd.load_preferences()
            self.assertEqual(raw["model"], "test.onnx")
            self.assertNotIn("not_allowed", raw)


if __name__ == "__main__":
    unittest.main()
