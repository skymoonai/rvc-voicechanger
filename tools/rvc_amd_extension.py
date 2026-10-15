"""RVC Studio AMD vc-rs extension for the installed UI.

No replacement of the installed Window implementation is required. Call
install(Window) after the installed tools.rvc_modern_qt module has finished
defining Window. The original constructor finishes first, including the tray
and the application's own startup/shutdown machinery.

The tab owns a separate vc-rs WASAPI subprocess. This does not graft vc-rs
into RVC Studio's existing audio DSP, mixer, virtual-mic route or Soundpad.
"""
from __future__ import annotations

from functools import wraps


class AmdPanel:
    def __init__(self, window):
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtWidgets import (
            QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
            QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
            QTabWidget, QVBoxLayout, QWidget
        )
        from tools.rvc_amd_bridge import VcRsBridge, load_preferences

        self.window = window
        self.bridge = VcRsBridge()
        self._last_log = ""
        self._error_shown_for_exit = None
        self._timer = None
        self._buttons = []
        self._QMessageBox = QMessageBox
        self._QFileDialog = QFileDialog

        root = getattr(window, "root", None)
        if root is None:
            raise RuntimeError("RVC Studio ana penceresi bulunamadı.")

        tabs = getattr(window, "tabs", None)
        if not isinstance(tabs, QTabWidget):
            # New UI versions may not expose tabs by attribute. Search the
            # window tree rather than guessing a class private field name.
            candidates = root.findChildren(QTabWidget)
            tabs = next((candidate for candidate in candidates
                         if candidate.count() >= 4), None)
        if tabs is None:
            raise RuntimeError("RVC Studio sekme alanı bulunamadı.")

        cfg = load_preferences()
        scrolling = QScrollArea()
        scrolling.setWidgetResizable(True)
        scrolling.setProperty("studio_tab_id", "amd")
        scrolling.setProperty("rvc_tab_title", "AMD Motoru")
        page = QWidget()
        page.setObjectName("root")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        heading = QLabel("AMD Motoru — Rust / ONNX (Deneysel)")
        heading.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(heading)
        warning = QLabel(
            "Bu mod RVC Studio'nun ana Python ses motorundan BAĞIMSIZ çalışır. "
            "RVC v2/F0 ONNX modellerini destekler. .index, formant, "
            "Soundpad yönlendirme, AP-BWE ve A/B karşılaştırması bu motorda "
            "henüz bağlı değildir. AMD GPU hızı garanti edilmez."
        )
        warning.setWordWrap(True)
        layout.addWidget(warning)

        def line_field(title, key, placeholder, choose=False):
            layout.addWidget(QLabel(title))
            row = QHBoxLayout()
            entry = QLineEdit(str(cfg.get(key, "")))
            entry.setPlaceholderText(placeholder)
            row.addWidget(entry, 1)
            if choose:
                button = QPushButton("Gözat")
                button.clicked.connect(lambda _=False, field=entry: self.browse_onnx(field))
                row.addWidget(button)
            layout.addLayout(row)
            return entry

        self.model = line_field("RVC ses modeli (.onnx)", "model", "voice.onnx", True)
        convert = QPushButton("PTH → ONNX dönüştürücüyü aç (vc-rs GUI)")
        convert.clicked.connect(self.open_converter)
        layout.addWidget(convert)
        self.embedder = line_field(
            "ContentVec .onnx (boşsa otomatik aranır)", "embedder",
            "content_vec_500.onnx", True
        )
        self.f0 = line_field("RMVPE .onnx (boşsa otomatik aranır)", "f0",
                             "rmvpe.onnx", True)

        layout.addWidget(QLabel("GPU backend"))
        self.provider = QComboBox()
        self.provider.addItems(["DirectML", "MIGraphX (deneysel)"])
        self.provider.setCurrentText(str(cfg.get("provider", "DirectML")))
        layout.addWidget(self.provider)

        layout.addWidget(QLabel("Chunk süresi (ms): düşük = düşük gecikme, daha yüksek kesilme riski"))
        self.chunk = QComboBox()
        self.chunk.addItems(["100", "200", "250", "500", "750", "1000"])
        self.chunk.setCurrentText(str(cfg.get("chunk_ms", "500")))
        layout.addWidget(self.chunk)

        self.input_device = line_field("vc-rs WASAPI giriş (boşsa varsayılan)",
                                       "input", "Mikrofon / VAC giriş adı")
        self.output_device = line_field("vc-rs WASAPI çıkış (boşsa varsayılan)",
                                        "output", "Kulaklık / sanal kablo çıkış adı")

        find_devices = QPushButton("Ses aygıtlarını günlükte listele")
        find_devices.clicked.connect(self.find_devices)
        layout.addWidget(find_devices)

        row = QHBoxLayout()
        start = QPushButton("▶ AMD Ses Motorunu Başlat")
        start.setObjectName("primary")
        stop = QPushButton("■ AMD Ses Motorunu Durdur")
        stop.setObjectName("stop")
        start.clicked.connect(self.start)
        stop.clicked.connect(self.stop)
        row.addWidget(start)
        row.addWidget(stop)
        layout.addLayout(row)

        self.status_label = QLabel("AMD motoru durduruldu.")
        layout.addWidget(self.status_label)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(170)
        self.log.setPlaceholderText("Rust motorunun tanılama ve hata mesajları burada görünür.")
        layout.addWidget(self.log, 1)
        layout.addWidget(QLabel(
            "Pitch, Ses Dönüşümü sekmesindeki değerden başlatma sırasında alınır. "
            "Değişiklikler için Rust motorunu yeniden başlat."
        ))

        scrolling.setWidget(page)
        tabs.addTab(scrolling, "AMD Motoru")
        if hasattr(tabs, "tabBar"):
            bar = tabs.tabBar()
            bar.setTabData(tabs.indexOf(scrolling), "amd")
        self.tab = scrolling
        self._timer = QTimer(root)
        self._timer.timeout.connect(self.tick)
        self._timer.start(200)

        # Use the current UI's localization system if present; do not require
        # obsolete Window methods/fields or modify the tray.
        try:
            from tools.rvc_i18n import localize_tree
            localize_tree(scrolling)
        except (ImportError, AttributeError):
            pass

    def browse_onnx(self, target):
        path, _ = self._QFileDialog.getOpenFileName(
            getattr(self.window, "root", None), "ONNX dosyası seç",
            "", "ONNX (*.onnx);;RVC model (*.pth);;Tüm dosyalar (*)"
        )
        if path:
            target.setText(path)

    def open_converter(self):
        try:
            self.bridge.open_converter()
        except Exception as exc:
            self.error(str(exc))

    def find_devices(self):
        try:
            self.bridge.list_devices_async()
        except Exception as exc:
            self.error(str(exc))

    def error(self, message):
        self._QMessageBox.critical(getattr(self.window, "root", None),
                                   "AMD Motoru", str(message))

    def start(self):
        if self.bridge.running:
            self.error("AMD motoru zaten çalışıyor.")
            return
        stream = getattr(self.window.owner, "stream", None)
        if stream is not None and bool(getattr(stream, "active", False)):
            self.error("Önce normal RVC Studio ses motorunu durdur.")
            return

        from tools.rvc_amd_bridge import save_preferences
        config = {
            "model": self.model.text(),
            "embedder": self.embedder.text(),
            "f0": self.f0.text(),
            "input": self.input_device.text(),
            "output": self.output_device.text(),
            "provider": self.provider.currentText(),
            "chunk_ms": self.chunk.currentText(),
        }
        try:
            raw_pitch = 0.0
            widgets = getattr(self.window, "widgets", {})
            handle = widgets.get("pitch") if isinstance(widgets, dict) else None
            if handle is not None and callable(getattr(handle, "get", None)):
                raw_pitch = float(handle.get())
            self.bridge.start(
                model=config["model"], embedder=config["embedder"],
                f0=config["f0"], input_device=config["input"],
                output_device=config["output"], provider=config["provider"],
                chunk_ms=int(config["chunk_ms"]), pitch=raw_pitch,
            )
            self._error_shown_for_exit = None
            try:
                save_preferences(config)
            except OSError as exc:
                self.bridge._log("Ayarlar kaydedilemedi: " + str(exc))
        except (OSError, RuntimeError, ValueError) as exc:
            self.error(str(exc))
        self.tick()

    def stop(self):
        self.bridge.stop()
        self.tick()

    def tick(self):
        status, messages = self.bridge.status()
        self.status_label.setText(status)
        if messages != self._last_log:
            self.log.setPlainText(messages)
            self.log.verticalScrollBar().setValue(
                self.log.verticalScrollBar().maximum())
            self._last_log = messages

    def close(self):
        if self._timer is not None:
            self._timer.stop()
        self.bridge.close()


def install(window_class):
    """Install after the version-matched Window class is fully defined.

    Safe to call twice. Wrap, do not replace, the installed window constructor,
    read loop, or shutdown logic. No old AMD branch UI source is copied.
    """
    if getattr(window_class, "_amd_vcrs_extension_installed", False):
        return
    orig_init = window_class.__init__
    orig_shutdown = window_class.shutdown
    orig_read = window_class.read

    @wraps(orig_init)
    def init(self, *args, **kwargs):
        orig_init(self, *args, **kwargs)
        try:
            self._amd_vcrs_panel = AmdPanel(self)
        except Exception as exc:
            # An optional experimental extension must never prevent the
            # official RVC Studio window/tray from opening.
            print("AMD extension could not create its tab: " + str(exc), flush=True)

    @wraps(orig_shutdown)
    def shutdown(self, *args, **kwargs):
        panel = getattr(self, "_amd_vcrs_panel", None)
        if panel is not None:
            panel.close()
        return orig_shutdown(self, *args, **kwargs)

    @wraps(orig_read)
    def read(self, *args, **kwargs):
        while True:
            event, values = orig_read(self, *args, **kwargs)
            panel = getattr(self, "_amd_vcrs_panel", None)
            if event == "start_vc" and panel is not None and panel.bridge.running:
                panel.error("Önce AMD ses motorunu durdur; iki motor aynı anda çalışamaz.")
                continue
            return event, values

    window_class.__init__ = init
    window_class.shutdown = shutdown
    window_class.read = read
    window_class._amd_vcrs_extension_installed = True
