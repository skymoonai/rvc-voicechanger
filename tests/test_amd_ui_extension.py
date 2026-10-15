"""Compatibility contract tests: the 1.5.2 Window owns its own tray/UI."""
import unittest
from unittest.mock import patch
from tools import rvc_amd_extension as ext


class FakeBridge:
    running = False


class FakePanel:
    instances = []

    def __init__(self, window):
        self.window = window
        self.bridge = FakeBridge()
        self.closed = False
        self.blocked = []
        self.instances.append(self)

    def close(self):
        self.closed = True

    def is_current(self):
        return False

    def start(self):
        self.bridge.running = True

    def stop(self):
        self.bridge.running = False

    def error(self, message):
        self.blocked.append(message)


class CompatibleWindow:
    def __init__(self, owner, data):
        self.owner = owner
        self.data = data
        self.tray = object()  # introduced by modern release; must be untouched
        self._events = ["start_vc", "stop_vc"]
        self.shutdown_calls = 0

    def read(self):
        return (self._events.pop(0), {"pitch": 0})

    def shutdown(self):
        self.shutdown_calls += 1


class CompatibilityTests(unittest.TestCase):
    def setUp(self):
        FakePanel.instances.clear()

    def test_preserve_tray_and_original_methods(self):
        with patch.object(ext, "AmdPanel", FakePanel):
            ext.install(CompatibleWindow)
            original = CompatibleWindow("owner", {"model": "file.pth"})
            self.assertIsNotNone(original.tray)
            self.assertEqual(original.owner, "owner")
            self.assertEqual(original.data["model"], "file.pth")
            self.assertEqual(len(FakePanel.instances), 1)
            original.shutdown()
            self.assertEqual(original.shutdown_calls, 1)
            self.assertTrue(FakePanel.instances[0].closed)

    def test_cannot_start_two_audio_engines(self):
        with patch.object(ext, "AmdPanel", FakePanel):
            ext.install(CompatibleWindow)
            window = CompatibleWindow(None, {})
            window._amd_vcrs_panel.bridge.running = True
            window._events.append("after-stop")
            event, _ = window.read()
            self.assertEqual(event, "after-stop")
            self.assertEqual(len(window._amd_vcrs_panel.blocked), 1)
            self.assertFalse(window._amd_vcrs_panel.bridge.running)

    def test_extension_failure_does_not_crash_stock_window(self):
        with patch.object(ext, "AmdPanel", side_effect=RuntimeError("Qt module missing")):
            ext.install(CompatibleWindow)
            window = CompatibleWindow(None, {})
            self.assertTrue(hasattr(window, "tray"))
            self.assertFalse(hasattr(window, "_amd_vcrs_panel"))
            self.assertEqual(window.read()[0], "start_vc")
            window.shutdown()
            self.assertEqual(window.shutdown_calls, 1)

    def test_install_is_idempotent(self):
        with patch.object(ext, "AmdPanel", FakePanel):
            ext.install(CompatibleWindow)
            ext.install(CompatibleWindow)
            CompatibleWindow(None, {})
            self.assertEqual(len(FakePanel.instances), 1)


if __name__ == "__main__":
    unittest.main()
