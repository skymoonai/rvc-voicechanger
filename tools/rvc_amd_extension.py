"""Native-feeling AMD/DirectML page for installed RVC Studio 1.5.2.

Hooks the *installed* Window only after its original init completed. Does not
replace tray, the Python audio engine, Soundpad, model/profile handling or UI
methods. Rust vc-rs operates as a separate subprocess and WASAPI stream.
"""
from __future__ import annotations

from functools import wraps
import queue
import threading


class AmdPanel:
    """Qt widgets live on the UI thread. Downloads, conversion, devices use workers."""

    def __init__(self, window):
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtWidgets import (
            QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
            QLineEdit, QMessageBox, QProgressBar, QPushButton, QScrollArea,
            QSlider, QTabWidget, QVBoxLayout, QWidget,
        )
        from tools.rvc_amd_bridge import VcRsBridge, load_preferences
        from tools.rvc_amd_metrics import ChildGpuMeter

        self.window = window
        self.bridge = VcRsBridge()
        self._pending = queue.Queue()
        self._closing = threading.Event()
        self._busy = False
        self._models_ready = False
        self._pending_start = False
        self._QFileDialog = QFileDialog
        self._QMessageBox = QMessageBox
        self._cfg = load_preferences()

        root = getattr(window, "root", None)
        if root is None:
            raise RuntimeError("Ana RVC Studio penceresi bulunamadı.")
        tabs = getattr(window, "tabs", None)
        if not isinstance(tabs, QTabWidget):
            tabs = next((x for x in root.findChildren(QTabWidget)
                         if x.count() >= 4), None)
        if tabs is None:
            raise RuntimeError("RVC Studio sekme alanı bulunamadı.")
        self.tabs = tabs

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setProperty("studio_tab_id", "amd")
        scroll.setProperty("rvc_tab_title", "AMD Motoru")
        body = QWidget()
        body.setObjectName("root")
        overall = QVBoxLayout(body)
        overall.setContentsMargins(20, 18, 20, 20)
        overall.setSpacing(14)

        hero = QLabel("AMD Ses Dönüşümü")
        hero.setObjectName("hero")
        hero.setStyleSheet("font-size:22px;font-weight:700")
        overall.addWidget(hero)
        explainer = QLabel("Rust / Windows ML · DirectML üzerinden canlı ses dönüşümü")
        explainer.setObjectName("subtitle")
        overall.addWidget(explainer)

        def card():
            wrapper = QFrame()
            wrapper.setObjectName("card")
            layout = QVBoxLayout(wrapper)
            layout.setContentsMargins(16, 16, 16, 16)
            layout.setSpacing(12)
            overall.addWidget(wrapper)
            return layout

        def header(layout, title, detail=None):
            text = QLabel(title)
            text.setObjectName("section")
            text.setStyleSheet("font-size:15px;font-weight:700")
            layout.addWidget(text)
            if detail:
                minor = QLabel(detail)
                minor.setWordWrap(True)
                minor.setObjectName("subtitle")
                layout.addWidget(minor)

        model_card = card()
        header(model_card, "Ses modeli", "PTH seçebilirsin; desteklenen RVC v2/F0 modelleri uygulama içinden ONNX'e dönüştürülür.")
        row = QHBoxLayout()
        self.model = QLineEdit(str(self._cfg.get("model", "")))
        self.model.setReadOnly(True)
        self.model.setPlaceholderText("Bir ses modeli seç (.pth veya .onnx)")
        row.addWidget(self.model, 1)
        browse = QPushButton("Model seç")
        browse.clicked.connect(self.choose_model)
        row.addWidget(browse)
        model_card.addLayout(row)
        self.convert_label = QLabel("PTH → ONNX dönüştürme gerektiğinde otomatik başlar.")
        self.convert_label.setWordWrap(True)
        model_card.addWidget(self.convert_label)

        support_card = card()
        header(support_card, "Ses altyapısı", "ContentVec ve RMVPE otomatik hazırlanır; kullanıcıdan dosya yolu istenmez.")
        self.assets_label = QLabel("Modeller denetleniyor…")
        self.assets_label.setWordWrap(True)
        support_card.addWidget(self.assets_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        support_card.addWidget(self.progress)
        retry = QPushButton("Destek modellerini yeniden denetle / indir")
        retry.clicked.connect(self.prepare_models)
        support_card.addWidget(retry)
        license_note = QLabel(
            "ContentVec ve RMVPE yaklaşık 741 MB'dır, GPL-3.0 lisanslı üçüncü taraf ağırlıklardır. "
            "SHA-256 doğrulaması yapılır ve %LOCALAPPDATA%\\RVCStudio\\engines\\vc-rs\\models konumunda saklanırlar."
        )
        license_note.setWordWrap(True)
        license_note.setObjectName("subtitle")
        support_card.addWidget(license_note)

        devices = card()
        header(devices, "Ses giriş ve çıkış", "Sistemdeki WASAPI cihazlarını listeden seç; sanal kabloyu giriş, dinlemek istediğin cihazı çıkış yap.")
        layout = QGridLayout()
        layout.addWidget(QLabel("Mikrofon girişi"), 0, 0)
        layout.addWidget(QLabel("Ses çıkışı"), 0, 1)
        self.input_device = QComboBox()
        self.output_device = QComboBox()
        self.input_device.addItem("Windows varsayılan mikrofon", "")
        self.output_device.addItem("Windows varsayılan ses çıkışı", "")
        layout.addWidget(self.input_device, 1, 0)
        layout.addWidget(self.output_device, 1, 1)
        devices.addLayout(layout)
        refresh = QPushButton("Ses cihazlarını yenile")
        refresh.clicked.connect(self.refresh_devices)
        devices.addWidget(refresh)

        engine = card()
        header(engine, "Ses dönüştürme", "Chunk küçüldükçe gecikme azalır; cihaz yetişemezse ses kesilebilir.")
        opts = QGridLayout()
        opts.addWidget(QLabel("GPU motoru"), 0, 0)
        self.provider = QComboBox()
        self.provider.addItems(["DirectML", "MIGraphX (deneysel)"])
        self.provider.setCurrentText(str(self._cfg.get("provider", "DirectML")))
        opts.addWidget(self.provider, 1, 0)
        opts.addWidget(QLabel("Pitch"), 0, 1)
        self.pitch = QSlider(Qt.Orientation.Horizontal)
        self.pitch.setRange(-12, 12)
        self.pitch.setValue(int(float(self._cfg.get("pitch", 0))))
        self.pitch_label = QLabel(str(self.pitch.value()))
        self.pitch.valueChanged.connect(lambda val: self.pitch_label.setText(str(val)))
        pitch_row = QHBoxLayout()
        pitch_row.addWidget(self.pitch, 1)
        pitch_row.addWidget(self.pitch_label)
        opts.addLayout(pitch_row, 1, 1)
        engine.addLayout(opts)

        self.chunk_label = QLabel("Chunk süresi: 200 ms")
        engine.addWidget(self.chunk_label)
        self.chunk = QSlider(Qt.Orientation.Horizontal)
        self.chunk.setMinimum(50)
        self.chunk.setMaximum(1000)
        self.chunk.setSingleStep(10)
        self.chunk.setPageStep(50)
        stored_chunk = max(50, min(1000, int(self._cfg.get("chunk_ms", 200))))
        self.chunk.setValue((stored_chunk // 10) * 10)
        self.chunk_label.setText(f"Chunk süresi: {self.chunk.value()} ms")
        self.chunk.valueChanged.connect(
            lambda value: self.chunk_label.setText(f"Chunk süresi: {(value // 10) * 10} ms")
        )
        engine.addWidget(self.chunk)

        controls = QHBoxLayout()
        self.start_button = QPushButton("▶ SESİ BAŞLAT")
        self.start_button.setObjectName("primary")
        self.stop_button = QPushButton("■ DURDUR")
        self.stop_button.setObjectName("stop")
        self.start_button.clicked.connect(self.start)
        self.stop_button.clicked.connect(self.stop)
        controls.addWidget(self.start_button, 1)
        controls.addWidget(self.stop_button)
        engine.addLayout(controls)
        self.status_label = QLabel("Hazırlanıyor…")
        self.status_label.setWordWrap(True)
        engine.addWidget(self.status_label)
        note = QLabel(
            "Çıkarım süresi ve nominal içerik gecikmesi alttaki RVC Studio göstergelerine aktarılır. "
            "Gerçek mikrofon-kulaklık gecikmesi ayrıca ölçülmelidir. AMD motorunun ayrıntılı çıktısı Günlük sekmesindedir."
        )
        note.setWordWrap(True)
        note.setObjectName("subtitle")
        engine.addWidget(note)
        overall.addStretch(1)

        scroll.setWidget(body)
        self.tab = scroll
        tab_index = tabs.addTab(scroll, "AMD Motoru")
        tabs.tabBar().setTabData(tab_index, "amd")
        self._gpu = ChildGpuMeter(lambda: (self.bridge.process.pid
                                           if self.bridge.running else None))
        self._gpu.start()
        self._timer = QTimer(root)
        self._timer.timeout.connect(self.tick)
        self._timer.start(200)
        self.refresh_devices()
        # User requested first-open automatic setup. Worker may fetch ~741MB.
        self.prepare_models()

    def _async(self, fn, kind):
        def task():
            try:
                payload = fn()
                self._pending.put((kind, payload))
            except Exception as exc:
                self._pending.put(("error_" + kind, str(exc)))
        threading.Thread(target=task, name="RVCAMD-" + kind, daemon=True).start()

    def _say(self, message):
        self.status_label.setText(message)
        print("[AMD] " + message, flush=True)

    def error(self, message):
        self._say(str(message))
        self._QMessageBox.warning(getattr(self.window, "root", None), "AMD Motoru", str(message))

    def choose_model(self):
        chosen, _ = self._QFileDialog.getOpenFileName(
            self.window.root, "RVC ses modeli seç", "",
            "RVC ses modelleri (*.pth *.onnx);;PTH (*.pth);;ONNX (*.onnx)")
        if not chosen:
            return
        self.model.setText(chosen)
        if chosen.lower().endswith(".pth"):
            self._convert_selected(chosen)
        else:
            self.convert_label.setText("ONNX modeli hazır.")

    def _convert_selected(self, pth):
        from pathlib import Path
        existing = Path(pth).with_suffix(".onnx")
        if existing.is_file():
            self.model.setText(str(existing))
            self.convert_label.setText("Daha önce dönüştürülmüş ONNX dosyası kullanılıyor.")
            return
        self._busy = True
        self.convert_label.setText("PTH modeli ONNX'e dönüştürülüyor…")
        from tools.rvc_amd_convert import convert_pth
        self._async(lambda: convert_pth(pth, lambda status:
                                        self._pending.put(("convert_progress", status))),
                    "converted")

    def prepare_models(self):
        if getattr(self, "_downloading", False):
            return
        self._downloading = True
        self._models_ready = False
        from tools.rvc_amd_assets import ensure_models
        self._async(lambda: ensure_models(
            lambda message, fraction: self._pending.put(("download_progress", (message, fraction))),
            self._closing,
        ), "assets")

    def refresh_devices(self):
        if getattr(self, "_listing", False):
            return
        self._listing = True
        self._async(self._discover_devices, "devices")

    @staticmethod
    def _discover_devices():
        from tools.rvc_amd_devices import list_wasapi
        return list_wasapi()

    def _select_device(self, combo, preferred):
        i = combo.findData(preferred)
        if i < 0 and preferred:
            i = combo.findText(preferred)
        combo.setCurrentIndex(max(0, i))

    def _fill_devices(self, input_names, output_names):
        current_in = str(self.input_device.currentData() or self._cfg.get("input", ""))
        current_out = str(self.output_device.currentData() or self._cfg.get("output", ""))
        self.input_device.clear()
        self.output_device.clear()
        self.input_device.addItem("Windows varsayılan mikrofon", "")
        self.output_device.addItem("Windows varsayılan ses çıkışı", "")
        for name in input_names:
            self.input_device.addItem(name, name)
        for name in output_names:
            self.output_device.addItem(name, name)
        # Match saved AMD devices first. Main RVC Studio devices use PortAudio
        # names, which may not equal vc-rs WASAPI strings; do not guess.
        self._select_device(self.input_device, current_in)
        self._select_device(self.output_device, current_out)
        if not input_names:
            self._say("WASAPI mikrofonu bulunamadı. Sanal makine mikrofon erişimini kontrol et.")

    def _stream_active(self):
        stream = getattr(self.window.owner, "stream", None)
        return bool(stream is not None and getattr(stream, "active", False))

    def is_current(self):
        return self.tabs.currentWidget() is self.tab

    def start(self):
        if self.bridge.running or self._busy:
            return
        if self._stream_active():
            self.error("Önce normal RVC Studio ses dönüşümünü durdur.")
            return
        if not self._models_ready:
            self.error("ContentVec/RMVPE hazırlanıyor. İndirme tamamlandığında tekrar dene.")
            return
        voice = self.model.text().strip()
        if voice.lower().endswith(".pth"):
            self._pending_start = True
            self._convert_selected(voice)
            return
        from tools.rvc_amd_bridge import save_preferences
        chunk = (self.chunk.value() // 10) * 10
        config = {
            "model": voice,
            "provider": self.provider.currentText(),
            "chunk_ms": str(chunk),
            "pitch": str(self.pitch.value()),
            "input": str(self.input_device.currentData() or ""),
            "output": str(self.output_device.currentData() or ""),
        }
        try:
            self.bridge.start(
                model=voice, embedder="", f0="",
                input_device=config["input"], output_device=config["output"],
                provider=config["provider"], chunk_ms=chunk,
                pitch=float(self.pitch.value()),
            )
            save_preferences(config)
            self._say("AMD motoru başlatılıyor…")
        except Exception as exc:
            self.error(str(exc))

    def stop(self):
        self._pending_start = False
        self.bridge.stop()
        self._say("AMD ses motoru durduruldu.")

    def tick(self):
        for _ in range(60):
            try:
                kind, payload = self._pending.get_nowait()
            except queue.Empty:
                break
            if kind == "download_progress":
                message, fraction = payload
                self.assets_label.setText(message)
                self.progress.setValue(int(float(fraction) * 100))
            elif kind == "assets":
                self._downloading = False
                self._models_ready = True
                self.assets_label.setText("✓ ContentVec ve RMVPE hazır")
                self.progress.setValue(100)
            elif kind == "error_assets":
                self._downloading = False
                self._models_ready = False
                self.assets_label.setText("Destek modelleri indirilemedi: " + payload)
                self._say("Model indirme hatası. İnternet bağlantısını kontrol et ve yeniden dene.")
            elif kind == "devices":
                self._listing = False
                self._fill_devices(*payload)
            elif kind == "error_devices":
                self._listing = False
                self._say("Ses cihazları listelenemedi: " + payload)
            elif kind == "convert_progress":
                self.convert_label.setText(payload)
            elif kind == "converted":
                self._busy = False
                self.model.setText(str(payload))
                self.convert_label.setText("✓ ONNX dönüştürme tamamlandı.")
                if self._pending_start:
                    self._pending_start = False
                    self.start()
            elif kind == "error_converted":
                self._busy = False
                self._pending_start = False
                self.convert_label.setText("Dönüştürme başarısız: " + payload)
                self._say("Dönüştürme başarısız: " + payload)

        self.start_button.setEnabled(not self._busy and self._models_ready and not self.bridge.running)
        self.stop_button.setEnabled(self.bridge.running)
        if self.bridge.running:
            metrics = self.bridge.snapshot_metrics()
            gui = getattr(self.window, "widgets", {})
            if isinstance(gui, dict):
                for key, value in (
                    ("infer_time", "—" if metrics["infer_ms"] is None
                     else f"{metrics['infer_ms']:.0f}"),
                    # Rust metric is *nominal model content delay*, not
                    # microphone-to-speaker latency. The ~ marker is deliberate.
                    ("delay_time", "—" if metrics["content_delay_ms"] is None
                     else f"~{metrics['content_delay_ms']:.0f}"),
                    ("gpu_usage", "—" if self._gpu.value is None
                     else f"{self._gpu.value:.0f}"),
                    ("sr_stream", "—"),
                ):
                    handle = gui.get(key)
                    if handle is not None:
                        try:
                            handle.update(value)
                        except Exception:
                            pass
            self.status_label.setText("AMD motoru çalışıyor · DirectML/Windows ML")

    def close(self):
        self._closing.set()
        if getattr(self, "_timer", None) is not None:
            self._timer.stop()
        self._gpu.close()
        self.bridge.close()


def install(window_class):
    """Patch Window only after its original definition; preserve version-matched tray."""
    if getattr(window_class, "_amd_vcrs_extension_installed", False):
        return
    init_original = window_class.__init__
    shutdown_original = window_class.shutdown
    read_original = window_class.read

    @wraps(init_original)
    def init(self, *args, **kwargs):
        init_original(self, *args, **kwargs)
        try:
            self._amd_vcrs_panel = AmdPanel(self)
        except Exception as exc:
            print("[AMD] AMD sekmesi yüklenemedi: " + str(exc), flush=True)

    @wraps(shutdown_original)
    def shutdown(self, *args, **kwargs):
        panel = getattr(self, "_amd_vcrs_panel", None)
        if panel is not None:
            panel.close()
        return shutdown_original(self, *args, **kwargs)

    @wraps(read_original)
    def read(self, *args, **kwargs):
        while True:
            event, values = read_original(self, *args, **kwargs)
            panel = getattr(self, "_amd_vcrs_panel", None)
            if panel is not None:
                if event == "start_vc":
                    if panel.bridge.running:
                        panel.error("AMD motoru zaten çalışıyor. Önce onu durdur.")
                        continue
                    if panel.is_current():
                        panel.start()
                        continue
                if event == "stop_vc" and panel.bridge.running:
                    panel.stop()
                    continue
            return event, values

    window_class.__init__ = init
    window_class.shutdown = shutdown
    window_class.read = read
    window_class._amd_vcrs_extension_installed = True
