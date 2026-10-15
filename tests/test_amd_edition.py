"""AMD Edition non-hardware regressions: UI routing, supported Rust flags."""
from __future__ import annotations
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.rvc_amd_edition import db_from_rms, db_to_linear, install
from tools.rvc_amd_soundpad import wasapi_device_index
from tools.rvc_amd_tasks import make_command


class RoutingWindow:
    def __init__(self, *args, **kwargs):
        self.tray = object()
        self.events = ["start_vc", "stop_vc", "_browse_pth", "reload_devices", "value_change"]
        self._studio_calls = []
    def read(self):
        return self.events.pop(0), {"im": False}
    def shutdown(self):
        return "closed"
    def _request_voice(self, mode):
        self.last_mode = mode
    def _file_browse(self, event):
        self.browsed = event
    def _studio_event(self, event):
        self._studio_calls.append(event)


class MockEdition:
    def __init__(self, window):
        self.window = window
        self.bridge = type("Bridge", (), {"running": False})()
        self.starts = 0
        self.stops = 0
        self.browses = 0
        self.devices = 0
        self.passthrough = None
    def start(self):
        self.starts += 1
    def stop(self):
        self.stops += 1
    def browse(self):
        self.browses += 1
    def refresh_devices(self):
        self.devices += 1
    def set_passthrough(self, state):
        self.passthrough = state
    def close(self):
        self.closed = True


class AmdEditionTests(unittest.TestCase):
    def test_rust_cli_settings(self):
        fake = type("Edition", (), {
            "_assets_ready": True,
            "_support_paths": ("C:/models/contentvec.onnx", "C:/models/rmvpe.onnx"),
        })()
        with patch("tools.rvc_amd_tasks.find_executable", return_value=Path("C:/engine/vc-rs.exe")):
            command = make_command(fake, Path("input.wav"), Path("output.wav"), Path("model.onnx"),
                                   {"pitch": 24, "rms_mix_rate": .7, "threhold": -30, "amd_chunk_ms": 200},
                                   rnnoise=True)
        for flag in ("--pitch-shift", "--rms-mix-rate", "--silence-threshold",
                     "--denoiser", "--chunk-ms", "--provider"):
            self.assertIn(flag, command)
        self.assertEqual(command[command.index("--pitch-shift") + 1], "24.0")
        self.assertEqual(command[command.index("--denoiser") + 1], "rnnoise")

    def test_meters_and_threshold(self):
        self.assertAlmostEqual(db_to_linear(-20), .1)
        self.assertEqual(db_from_rms(.1)[0], 400)
        self.assertEqual(db_from_rms(None), (0, "Sessiz"))

    def test_unique_wasapi_output_for_soundpad(self):
        apis = [{"name": "Windows WASAPI"}, {"name": "MME"}]
        devices = [
            {"name": "Line 1 (Virtual Audio Cable)", "hostapi": 1, "max_output_channels": 2},
            {"name": "Line 1 (Virtual Audio Cable)", "hostapi": 0, "max_output_channels": 2},
        ]
        self.assertEqual(wasapi_device_index("Line 1 (Virtual Audio Cable)", devices, apis), 1)
        self.assertIsNone(wasapi_device_index("Unmapped", devices, apis))

    def test_original_ui_tray_is_preserved_and_voice_events_redirected(self):
        with patch("tools.rvc_amd_edition.AmdEdition", MockEdition):
            install(RoutingWindow)
            w = RoutingWindow()
            e = w._amd_edition
            self.assertIsNotNone(w.tray)
            self.assertEqual(w.read()[0], "value_change")
            self.assertEqual((e.starts, e.stops, e.browses, e.devices), (1, 1, 1, 1))
            w._request_voice("im")
            self.assertTrue(e.passthrough)
            w.shutdown()
            self.assertTrue(e.closed)


if __name__ == "__main__":
    unittest.main()
