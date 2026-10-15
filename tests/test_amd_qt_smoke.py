"""Offscreen Qt smoke test: actual AMD page widgets, no network/GPU/download."""
from __future__ import annotations
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from PySide6.QtWidgets import QApplication, QMainWindow, QTabWidget, QPlainTextEdit
except ImportError:
    QApplication = None


@unittest.skipIf(QApplication is None, "PySide6 not installed")
class AmdQtSmokeTests(unittest.TestCase):
    def test_amd_page_starts_without_external_gui_and_routes_to_studio_tab(self):
        from tools.rvc_amd_extension import AmdPanel

        app = QApplication.instance() or QApplication(sys.argv[:1])
        root = QMainWindow()
        tabs = QTabWidget()
        root.setCentralWidget(tabs)
        tabs.addTab(QTabWidget(), "Ses Dönüşümü")
        window = SimpleNamespace(
            root=root, tabs=tabs, owner=SimpleNamespace(stream=None), widgets={}
        )
        with (patch("tools.rvc_amd_assets.ensure_models",
                    return_value=(Path("content_vec_500.onnx"), Path("rmvpe.onnx"))),
              patch("tools.rvc_amd_devices.list_wasapi",
                    return_value=(["Microphone (Demo)"], ["Speakers (Demo)"])),
              patch("tools.rvc_amd_metrics.ChildGpuMeter.start")):
            panel = AmdPanel(window)
            try:
                for _ in range(50):
                    app.processEvents()
                    panel.tick()
                    if panel._models_ready and not panel._listing:
                        break
                    time.sleep(.02)
                self.assertTrue(panel._models_ready)
                self.assertEqual(panel.input_device.count(), 2)
                self.assertEqual(panel.output_device.count(), 2)
                self.assertEqual(panel.tab.property("studio_tab_id"), "amd")
                self.assertEqual(panel.chunk.minimum(), 50)
                self.assertEqual(panel.chunk.maximum(), 1000)
                self.assertEqual(panel.provider.currentText(), "DirectML")
                self.assertEqual(len(panel.tab.findChildren(QPlainTextEdit)), 0,
                                 "Raw CLI console must not be on the AMD page")
            finally:
                panel.close()
                root.close()
