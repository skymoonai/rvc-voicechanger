"""Headless AMD Edition Qt smoke; verifies it adapts the stock screen in-place."""
import os
import sys
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    from PySide6.QtWidgets import (QApplication, QWidget, QMainWindow, QTabWidget,
                                    QSlider, QLineEdit, QComboBox, QCheckBox,
                                    QLabel, QVBoxLayout)
except ImportError:
    QApplication = None


class FakeHandle:
    def __init__(self, widget, kind="input", minimum=0, step=1):
        self.widget, self.kind = widget, kind
        self.minimum, self.step = minimum, step
    def get(self):
        if self.kind == "slider":
            return self.minimum + self.widget.value() * self.step
        if self.kind == "check":
            return self.widget.isChecked()
        if self.kind == "combo":
            return self.widget.currentData() or self.widget.currentText()
        return self.widget.text()
    def update(self, value):
        if self.kind == "slider":
            self.widget.setValue(int(round((float(value)-self.minimum)/self.step)))
        elif self.kind == "check":
            self.widget.setChecked(bool(value))
        elif self.kind == "combo":
            idx = self.widget.findData(str(value))
            self.widget.setCurrentIndex(idx)
        else:
            self.widget.setText(str(value))


class FakeConverter:
    busy = False
    def start(self, values): return None
    def poll(self): return (False, False)


class FakeTools:
    muted = False
    busy = False
    def compare(self): pass
    def _render(self, *args): pass


class FakeSoundpad:
    def _output_device(self, lane): return None
    def __init__(self):
        self.device_cache = {}
        self.error = ""


@unittest.skipIf(QApplication is None, "PySide6 missing")
class AmdEditionQtTests(unittest.TestCase):
    def test_stock_voice_tab_is_reused_without_amd_tab(self):
        from tools.rvc_amd_edition import AmdEdition
        app = QApplication.instance() or QApplication(sys.argv[:1])
        root = QMainWindow()
        tabs = QTabWidget()
        root.setCentralWidget(tabs)
        tab = QWidget()
        tab_layout = QVBoxLayout(tab)
        card = QWidget()
        card_layout = QVBoxLayout(card)
        tab_layout.addWidget(card)
        tabs.addTab(tab, "Ses Dönüşümü")
        widgets = {}
        for key in ("pth_path", "index_path", "infer_time", "delay_time", "gpu_usage", "sr_stream"):
            field = QLineEdit()
            card_layout.addWidget(field)
            widgets[key] = FakeHandle(field)
        for key, lower, higher, val in (
            ("pitch", -16, 16, 0), ("rms_mix_rate", 0, 100, 10),
            ("threhold", -60, 0, -60), ("index_rate", 0, 100, 0),
            ("formant", -2, 2, 0)):
            outer = QWidget()
            outer.setLayout(QVBoxLayout())
            slider = QSlider()
            slider.setMaximum(higher - lower)
            slider.setValue(val - lower)
            outer.layout().addWidget(slider)
            card_layout.addWidget(outer)
            widgets[key] = FakeHandle(slider, "slider", minimum=lower,
                                     step=.01 if key in ("rms_mix_rate", "index_rate") else 1)
        for key in ("sg_input_device", "sg_output_device", "sg_hostapi", "monitor_device"):
            combo = QComboBox()
            card_layout.addWidget(combo)
            widgets[key] = FakeHandle(combo, "combo")
        for key in ("monitor_enabled", "im", "rmvpe", "fcpe", "pm", "I_noise_reduce", "O_noise_reduce"):
            checkbox = QCheckBox()
            card_layout.addWidget(checkbox)
            widgets[key] = FakeHandle(checkbox, "check")
        monitor_volume = QSlider()
        card_layout.addWidget(monitor_volume)
        widgets["monitor_volume"] = FakeHandle(monitor_volume, "slider")
        owner = SimpleNamespace(
            stream=None, studio_tools=FakeTools(),
            file_converter=FakeConverter(), soundpad=FakeSoundpad(),
        )
        win = SimpleNamespace(root=root, tabs=tabs, widgets=widgets, owner=owner)
        with (patch("tools.rvc_amd_metrics.ChildGpuMeter.start"),
              patch("tools.rvc_amd_assets.ensure_models",
                    return_value=(Path("content.onnx"), Path("rmvpe.onnx"))),
              patch("tools.rvc_amd_devices.list_wasapi",
                    return_value=(["Mic"], ["Headphones"]))):
            edition = AmdEdition(win)
            try:
                for _ in range(50):
                    app.processEvents()
                    edition.tick()
                    if edition._assets_ready and len(edition._input_names):
                        break
                    time.sleep(.02)
                self.assertEqual(tabs.count(), 1)
                self.assertTrue(edition._assets_ready)
                self.assertEqual(widgets["pitch"].minimum, -24)
                self.assertEqual(widgets["pitch"].widget.maximum(), 48)
                self.assertEqual(widgets["sg_input_device"].widget.count(), 2)
                self.assertEqual(edition.chunk.maximum(), 100)
                self.assertFalse(widgets["index_rate"].widget.parentWidget().isVisible())
            finally:
                edition.close()
                root.close()
