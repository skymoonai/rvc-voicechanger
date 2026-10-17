"""AMD Edition overlays the *original* RVC Studio 1.5.2 voice screen.

DO NOT import/reuse the obsolete separate AMD Motoru Qt page. The release UI,
tray, profile library and widgets are retained and adapted to the ONNX Runtime Rust
WASAPI engine. Unsupported features are disabled rather than silently ignored.
"""
from __future__ import annotations

import math
import queue
import threading
import time
from functools import wraps
from pathlib import Path

from tools.rvc_ort_bridge import VcRsBridge, VcRsError, load_preferences, save_preferences
from tools.rvc_amd_metrics import ChildGpuMeter


def db_to_linear(decibels: float) -> float:
    return max(0.000001, min(1.0, 10 ** (float(decibels) / 20.0)))


def db_from_rms(rms: float | None) -> tuple[int, str]:
    if rms is None or not math.isfinite(rms) or rms <= 0:
        return (0, "Sessiz")
    value = max(-60.0, min(0.0, 20 * math.log10(rms)))
    return (int(round((value + 60) * 10)), f"{value:.1f} dBFS")


class AmdEdition:
    def __init__(self, window):
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtWidgets import QLabel, QPushButton, QSlider, QComboBox, QWidget
        from tools.rvc_amd_monitor import ConvertedVoiceMonitor

        self.window = window
        self.bridge = VcRsBridge()
        self._cfg = load_preferences()
        self._queue = queue.Queue()
        self._closing = threading.Event()
        self._support_paths = ("", "")
        self._assets_ready = False
        self._converting = False
        self._pending_start = False
        self._listing = False
        self._download_busy = False
        self._passthrough = False
        self._recent_error = None
        self._recording = False
        self._capture_stop = threading.Event()
        self._ab_resume = False
        self._file_resume = False
        self._log_window = getattr(window, "log_view", None)
        self._input_names = []
        self._output_names = []
        self._default_input_name = ""
        self._default_output_name = ""
        self._last_start = 0
        self._restart_serial = 0
        self._restart_pending = False
        self._quality_warning_seen = set()
        self._QLabel = QLabel

        self.monitor = ConvertedVoiceMonitor(self._status)
        self.gpu = ChildGpuMeter(lambda: self.bridge.process.pid if self.bridge.running else None)
        self.gpu.start()
        window.root.setWindowTitle("RVC Studio AMD Edition · ONNX Runtime DirectML")
        self._configure_voice_page()
        self._configure_device_page()
        self._configure_quality_page()
        self._configure_tools()
        self._timer = QTimer(window.root)
        self._timer.timeout.connect(self.tick)
        self._timer.start(200)
        self._async(self._list_devices, "devices")
        self._load_assets()

    def h(self, key):
        return getattr(self.window, "widgets", {}).get(key)

    def w(self, key):
        handle = self.h(key)
        return handle.widget if handle is not None else None

    def read_value(self, key, default=None):
        handle = self.h(key)
        try:
            return handle.get() if handle is not None else default
        except Exception:
            return default

    def set_value(self, key, value):
        handle = self.h(key)
        if handle is not None:
            handle.update(value)

    def _status(self, message):
        print("[AMD Edition] " + str(message).encode("ascii", "backslashreplace").decode("ascii"),
              flush=True)
        if hasattr(self, "status_label"):
            self.status_label.setText(str(message))

    def _hide_slider(self, key):
        widget = self.w(key)
        if widget is not None and widget.parentWidget() is not None:
            widget.parentWidget().hide()

    def _configure_voice_page(self):
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QLabel, QPushButton, QSlider, QComboBox
        # Reuse stock model/profile path and browser. Users can select ONNX and
        # supported PTH; PTH is converted offline before ONNX Runtime launches.
        model_widget = self.w("pth_path")
        if model_widget is not None:
            model_widget.setReadOnly(True)
            model_widget.setPlaceholderText("RVC model (.pth / .onnx)")
        for label in self.window.root.findChildren(QLabel):
            if label.text().strip() == "PTH model dosyası":
                label.setText("Ses modeli (.pth / .onnx)")
        # Preserve FAISS INDEX selection and its original Index Rate slider.
        # ONNX Runtime host-side index mixing is supported in this edition.
        self._hide_slider("formant")
        # Sampling-rate radio buttons share the entire input/output card;
        # hide only these controls, never their parent or device selectors.
        for key in ("sr_model", "sr_device"):
            radio = self.w(key)
            if radio is not None:
                radio.hide()
        sample_stat = self.w("sr_stream")
        if sample_stat is not None:
            sample_stat.hide()
        for label in self.window.root.findChildren(QLabel):
            if label.text().strip() == "ÖRNEKLEME":
                label.hide()
        for name in ("pm", "fcpe"):
            w = self.w(name)
            if w is not None:
                w.hide()
        self.set_value("rmvpe", True)
        # Pitch range: -24 to +24 semitones while keeping saved value.
        handle = self.h("pitch")
        if handle and handle.kind == "slider":
            value = float(handle.get())
            handle.minimum = -24
            handle.widget.setMaximum(48)
            handle.update(max(-24, min(24, value)))
        # RVC Studio already has good volume + threshold sliders; ONNX Runtime CLI
        # exposes --rms-mix-rate and --silence-threshold respectively.
        canvas = self.w("rms_mix_rate")
        parent = canvas.parentWidget() if canvas else None
        if parent is None:
            parent = self.window.root

        chunk_row = parent.parentWidget().layout() if parent.parentWidget() else None
        from tools.rvc_ort_quality import PRESETS, DEFAULT_PRESET
        self.quality_caption = QLabel("Konuşma kalitesi")
        self.quality = QComboBox()
        for code, setting in PRESETS.items():
            self.quality.addItem(setting.label, code)
        selected_quality = str(self._cfg.get("quality", DEFAULT_PRESET))
        self.quality.setCurrentIndex(max(0, self.quality.findData(selected_quality)))
        self.chunk_caption = QLabel("Ses parçası / Chunk: 400 ms")
        self.chunk = QSlider(Qt.Orientation.Horizontal)
        self.chunk.setMinimum(10)  # ORT engine requires >=100ms chunks
        self.chunk.setMaximum(100)
        self.chunk.setSingleStep(1)
        self.chunk.setPageStep(5)
        # An old experimental 200 ms setting should not silently override
        # the new *clarity-first* default. Respect the persisted chunk only
        # if the user has saved one of these explicit quality presets.
        default_chunk = PRESETS[self.quality.currentData()].chunk_ms
        saved_chunk = self._cfg.get("chunk_ms", default_chunk) if "quality" in self._cfg else default_chunk
        start_chunk = max(50, min(1000, int(saved_chunk)))
        self.chunk.setValue(start_chunk // 10)
        self.chunk_caption.setText(f"Parça / Chunk: {self.chunk.value() * 10} ms")
        self.chunk.valueChanged.connect(
            lambda value: self.chunk_caption.setText(f"Parça / Chunk: {value * 10} ms")
        )
        self.chunk.valueChanged.connect(self.schedule_restart)
        self.quality.currentIndexChanged.connect(self._quality_changed)
        if chunk_row is not None:
            chunk_row.addWidget(self.quality_caption)
            chunk_row.addWidget(self.quality)
            chunk_row.addWidget(self.chunk_caption)
            chunk_row.addWidget(self.chunk)
            explanation = QLabel(
                "Net konuşma: daha uzun bağlam, daha kısa ses geçişleri. "
                "Pelteklik varsa RNNoise'u kapalı, ses algılama eşiğini -60 dB tut."
            )
            explanation.setWordWrap(True)
            explanation.setProperty("rvc_i18n_skip", True)
            chunk_row.addWidget(explanation)
        self.protect_caption = QLabel("Ünsüz koruma: 0.50 (kapalı)")
        self.protect = QSlider(Qt.Orientation.Horizontal)
        self.protect.setRange(0, 50)
        self.protect.setValue(50)
        self.protect.valueChanged.connect(
            lambda x: self.protect_caption.setText(
                f"Ünsüz koruma: {x/100:.2f} (0.50 kapalı)"
            )
        )
        self.protect.valueChanged.connect(self.schedule_restart)
        if chunk_row is not None:
            chunk_row.addWidget(self.protect_caption)
            chunk_row.addWidget(self.protect)
        self.status_label = QLabel("ONNX Runtime DirectML modelleri hazırlanıyor…")
        self.status_label.setWordWrap(True)
        if chunk_row is not None:
            chunk_row.addWidget(self.status_label)

    def _quality_changed(self, *_args):
        from tools.rvc_ort_quality import PRESETS
        preset = PRESETS[self.quality.currentData()]
        self.chunk.setValue(preset.chunk_ms // 10)
        self.schedule_restart()

    def _configure_device_page(self):
        from PySide6.QtWidgets import QComboBox
        host = self.w("sg_hostapi")
        if host is not None:
            host.setToolTip("AMD Edition ONNX Runtime WASAPI ses arayüzünü kullanır.")
            host.setEnabled(False)
        for key in ("sg_input_device", "sg_output_device"):
            combo = self.w(key)
            if isinstance(combo, QComboBox):
                combo.clear()
                combo.addItem("Windows varsayılan aygıt", "")
        self._monitor_checkbox = self.w("monitor_enabled")
        self._monitor_target = self.w("monitor_device")
        self._monitor_volume = self.w("monitor_volume")
        if self._monitor_checkbox is not None:
            self._monitor_checkbox.toggled.connect(self.sync_monitor)
        if self._monitor_target is not None:
            self._monitor_target.currentIndexChanged.connect(self.sync_monitor)
        if self._monitor_volume is not None:
            self._monitor_volume.valueChanged.connect(self.sync_monitor)

    def _configure_quality_page(self):
        noise = getattr(self.window, "noise_panel", None)
        if noise is not None:
            if hasattr(noise, "enabled"):
                from PySide6.QtCore import QSignalBlocker
                with QSignalBlocker(noise.enabled):
                    noise.enabled.setChecked(False)
                noise.enabled.setEnabled(False)
                noise.enabled.setToolTip(
                    "RNNoise ONNX Runtime DirectML motorunda desteklenmiyor."
                )
            if hasattr(noise, "status"):
                noise.status.setText(
                    "ONNX Runtime DirectML: RNNoise desteklenmiyor; sessiz ünsüzleri korumak için kapalı."
                )
        ap = getattr(self.window, "apbwe_panel", None)
        if ap is not None:
            ap.setEnabled(False)
            ap.setToolTip("AP-BWE yeni DirectML ses işleyicisine bağlanmadı.")
        for key in ("I_noise_reduce", "O_noise_reduce"):
            widget = self.w(key)
            if widget is not None:
                widget.setEnabled(False)

    def _configure_tools(self):
        from tools.rvc_ort_tasks import install_file_conversion, install_studio_tests, install_capture
        try:
            install_file_conversion(self.window.owner.file_converter, self)
            install_studio_tests(self.window.owner.studio_tools, self)
            install_capture(self.window.owner.studio_tools, self)
        except Exception as exc:
            self._status("AMD dosya dönüştürme bağlanamadı: " + str(exc))
        # Stock Soundpad already has an independent mic lane. Under AMD it
        # must target the exact same shared WASAPI output as the Rust worker.
        try:
            from tools.rvc_amd_soundpad import install_soundpad
            install_soundpad(self.window.owner.soundpad, self)
        except Exception as exc:
            self._status("AMD Soundpad cihaz eşleştirme hatası: " + str(exc))

    def _async(self, fn, label):
        def run():
            try:
                self._queue.put((label, fn()))
            except Exception as exc:
                self._queue.put(("error_" + label, str(exc)))
        threading.Thread(target=run, name="RVC-AMD-" + label, daemon=True).start()

    @staticmethod
    def _list_devices():
        import sounddevice as sd
        devices = list(sd.query_devices())
        apis = list(sd.query_hostapis())

        def names_for(direction):
            key = "max_input_channels" if direction == 0 else "max_output_channels"
            selected = [(i, d) for i, d in enumerate(devices)
                        if d[key] and "wasapi" in apis[d["hostapi"]]["name"].lower()]
            if not selected:
                selected = [(i, d) for i, d in enumerate(devices) if d[key]]
            return list(dict.fromkeys(str(d["name"]) for _, d in selected))

        def default_for(direction):
            try:
                return str(devices[int(sd.default.device[direction])]["name"])
            except (ValueError, IndexError, TypeError):
                return ""

        return (names_for(0), names_for(1), default_for(0), default_for(1))

    def refresh_devices(self):
        if not self._listing:
            self._listing = True
            self._async(self._list_devices, "devices")

    def _load_assets(self):
        if self._download_busy:
            return
        from tools.rvc_amd_assets import ensure_models
        self._download_busy = True
        self._async(
            lambda: ensure_models(
                lambda message, fraction: self._queue.put(("asset_progress", (message, fraction))),
                self._closing
            ), "assets"
        )

    def _populate_devices(self, mic, speakers, default_input="", default_output=""):
        self._input_names, self._output_names = mic, speakers
        self._default_input_name, self._default_output_name = default_input, default_output
        if default_input and default_input.casefold() == default_output.casefold():
            self._status(
                "Uyarı: Windows varsayılan mikrofon ve ses çıkışı aynı cihaz: "
                + default_input + ". Aynı VAC hattıyla geri besleme oluşabilir."
            )
        for key, names, pref in (
            ("sg_input_device", mic, self._cfg.get("input", "")),
            ("sg_output_device", speakers, self._cfg.get("output", "")),
        ):
            w = self.w(key)
            if w is None:
                continue
            old = self.read_value(key, "")
            w.clear()
            w.addItem("Windows varsayılan aygıt", "")
            for name in names:
                w.addItem(name, name)
            wanted = str(old or pref)
            exact = w.findData(wanted)
            w.setCurrentIndex(max(0, exact))

    def browse(self):
        from PySide6.QtWidgets import QFileDialog
        chosen, _ = QFileDialog.getOpenFileName(
            self.window.root, "RVC ses modeli seç",
            "", "Ses modelleri (*.pth *.onnx);;ONNX (*.onnx);;PTH (*.pth)"
        )
        if chosen:
            self.set_value("pth_path", chosen)
            if chosen.lower().endswith(".pth"):
                self.convert_model(chosen)

    def add_portable_profile(self):
        """Store the PTH source for NVIDIA and its ONNX conversion for AMD.

        The original profile backup service continues to archive the source
        PTH. Restored AMD profiles re-export ONNX as needed, so they stay
        readable by either motor without unsafe archive format changes.
        """
        from PySide6.QtWidgets import QFileDialog, QInputDialog
        from uuid import uuid4
        source, _ = QFileDialog.getOpenFileName(
            self.window.root, "Her iki motorla uyumlu PTH model seç",
            "", "RVC model (*.pth)"
        )
        if not source:
            return
        name, accepted = QInputDialog.getText(
            self.window.root, "AMD / NVIDIA ses profili",
            "Kütüphanede görünecek ad:", text=Path(source).stem
        )
        if not accepted:
            return
        owner = self.window.owner
        if getattr(owner, "stream", None) is not None and getattr(owner.stream, "active", False):
            self._status("Profil eklemek için önce diğer ses motorunu durdur.")
            return
        was_active = self.bridge.running
        if was_active:
            self.stop()
        try:
            owner.profile_save_values(self.window._values())
            entry = {
                "id": uuid4().hex, "name": name.strip() or Path(source).stem,
                "pth_path": str(Path(source).resolve()),
                "index_path": str(self.read_value("index_path", "") or ""),
                "settings": owner.profile_capture(self.window._values()),
            }
            owner.profiles.append(entry)
            owner.active_profile_id = entry["id"]
            owner.profile_store()
            owner.profile_labels_refresh()
            owner.profile_apply(entry)
            self.convert_model(source)
            self._status("Profil kaydedildi. PTH NVIDIA için, ONNX AMD için kullanılacak.")
            if was_active:
                if Path(source).with_suffix(".onnx").is_file():
                    from PySide6.QtCore import QTimer
                    QTimer.singleShot(450, self.start)
                else:
                    self._pending_start = True
        except Exception as exc:
            self._status("Profil eklenemedi: " + str(exc))

    def convert_model(self, pth):
        if self._converting:
            return
        source = Path(pth)
        output = source.with_suffix(".onnx")
        if output.is_file():
            self._status("ONNX karşılığı bulundu: " + output.name)
            return
        from tools.rvc_amd_convert import convert_pth
        self._converting = True
        self._status("PTH → ONNX dönüşümü başladı: " + source.name)
        self._async(lambda: convert_pth(str(source),
                                        lambda x: self._queue.put(("convert_progress", x))),
                    "converted")

    def model_path(self):
        name = str(self.read_value("pth_path", "") or "").strip()
        if not name:
            raise VcRsError("Önce bir ses modeli seç.")
        path = Path(name)
        if path.suffix.lower() == ".pth":
            target = path.with_suffix(".onnx")
            if not target.is_file():
                self._pending_start = True
                self.convert_model(name)
                return None
            return str(target)
        if path.suffix.lower() == ".onnx" and path.is_file():
            return str(path)
        raise VcRsError("RVC v2/F0 PTH veya geçerli bir ONNX dosyası seç.")

    def _start_args(self, model):
        from tools.rvc_ort_quality import (quality_settings, detection_threshold,
                                           check_audio_feedback)
        noise = getattr(self.window, "noise_panel", None)
        rnnoise = bool(noise.enabled.isChecked()) if noise else False
        mode = "rnnoise" if rnnoise else "off"
        quality = quality_settings(self.quality.currentData(), self.chunk.value() * 10)
        input_device = str(self.read_value("sg_input_device", "") or "")
        output_device = str(self.read_value("sg_output_device", "") or "")
        check_audio_feedback(input_device, output_device,
                             self._default_input_name, self._default_output_name)
        return dict(
            model=model, embedder=self._support_paths[0], f0=self._support_paths[1],
            input_device=input_device,
            output_device=output_device,
            provider="DirectML", **quality,
            pitch=float(self.read_value("pitch", 0)),
            rms_mix_rate=float(self.read_value("rms_mix_rate", 0)),
            silence_threshold=detection_threshold(self.read_value("threhold", -60)),
            denoiser=mode,
            index=str(self.read_value("index_path", "") or ""),
            index_rate=float(self.read_value("index_rate", 0) or 0),
            protect=self.protect.value() / 100.,
            passthrough=bool(self.read_value("im", False)),
            output_gain=0.0 if self.window.owner.studio_tools.muted else 1.0,
        )

    def start(self):
        if self.bridge.running or self._converting:
            return
        if not self._assets_ready:
            self._status("ContentVec/RMVPE hazırlanıyor; tamamlandığında başlat.")
            return
        stream = getattr(self.window.owner, "stream", None)
        if stream is not None and getattr(stream, "active", False):
            self._status("AMD için önce eski Python canlı ses akışını durdur.")
            return
        try:
            model = self.model_path()
            if model is None:
                return
            opts = self._start_args(model)
            self.bridge.start(**opts)
            self._last_start = time.monotonic()
            self._cfg.update({
                "model": str(self.read_value("pth_path", "")),
                "chunk_ms": str(opts["chunk_ms"]), "pitch": str(opts["pitch"]),
                "input": opts["input_device"], "output": opts["output_device"],
                "provider": "DirectML",
                "quality": self.quality.currentData(),
                "rnnoise": "1" if opts["denoiser"] == "rnnoise" else "0",
            })
            save_preferences(self._cfg)
            self._status("AMD ONNX Runtime DirectML motoru başlatıldı.")
            self.sync_monitor()
        except Exception as exc:
            self._status("AMD ses dönüşümü başlatılamadı: " + str(exc))

    def stop(self):
        self._pending_start = False
        self.monitor.stop()
        self.bridge.stop()
        self._status("AMD ses dönüşümü durduruldu.")

    def set_passthrough(self, enabled: bool):
        if self._passthrough == enabled:
            return
        self._passthrough = enabled
        if self.bridge.running:
            self._status("Mikrofon modu değişti; ONNX Runtime motoru yeniden başlatılıyor.")
            self.schedule_restart()

    def schedule_restart(self, *_args):
        """CLI flags are process settings; update via a debounced restart."""
        if not self.bridge.running and not self._restart_pending:
            return
        from PySide6.QtCore import QTimer
        self._restart_serial += 1
        token = self._restart_serial
        if self.bridge.running:
            self.monitor.stop()
            self.bridge.stop()
        self._restart_pending = True

        def settle(attempt=0):
            if token != self._restart_serial or self._closing.is_set():
                return
            if self.bridge.running and attempt < 12:
                QTimer.singleShot(150, lambda: settle(attempt+1))
                return
            self._restart_pending = False
            if self.bridge.running:
                self._status("Ayar değişikliği bekliyor: ONNX Runtime motoru kapanamadı.")
                return
            self.start()
        QTimer.singleShot(450, settle)

    def sync_monitor(self, *_args):
        if not self.bridge.running:
            self.monitor.stop()
            return
        enabled = bool(self.read_value("monitor_enabled", False))
        output = str(self.read_value("sg_output_device", "") or "")
        target = str(self.read_value("monitor_device", "") or "")
        gain = float(self.read_value("monitor_volume", 100)) / 100.0
        self.monitor.configure(enabled, output, target, gain)

    def _meters(self, metrics):
        meters = getattr(self.window, "_meters", None)
        if not isinstance(meters, dict):
            return
        for key, metric in (("input", "input_rms"), ("output", "output_rms")):
            if key not in meters:
                continue
            bar, label = meters[key]
            value, caption = db_from_rms(metrics.get(metric))
            bar.setValue(value)
            label.setText(caption)

    def paint(self):
        if not self.bridge.running:
            return
        metrics = self.bridge.snapshot_metrics()
        from tools.rvc_ort_quality import likely_quality_problems
        denoiser = "rnnoise" if (
            getattr(self.window, "noise_panel", None) is not None
            and self.window.noise_panel.enabled.isChecked()
        ) else "off"
        problems = likely_quality_problems(
            metrics, self.chunk.value() * 10,
            denoiser, float(self.read_value("threhold", -60))
        )
        for problem in problems:
            if problem not in self._quality_warning_seen:
                self._quality_warning_seen.add(problem)
                self._status("Kalite uyarısı: " + problem)
        for key, text in (
            ("infer_time", "—" if metrics.get("infer_ms") is None
             else f"{metrics['infer_ms']:.0f}"),
            # This is NOT true mic -> headphone latency.
            ("delay_time", "—" if metrics.get("content_delay_ms") is None
             else f"~{metrics['content_delay_ms']:.0f}"),
            ("gpu_usage", "—" if self.gpu.value is None else f"{self.gpu.value:.0f}"),
            ("sr_stream", "—"),
        ):
            self.set_value(key, text)
        self._meters(metrics)

    def tick(self):
        for _ in range(40):
            try:
                kind, payload = self._queue.get_nowait()
            except queue.Empty:
                break
            if kind == "devices":
                self._listing = False
                self._populate_devices(*payload)
            elif kind == "error_devices":
                self._listing = False
                self._status("AMD WASAPI cihaz listesi alınamadı: " + payload)
            elif kind == "asset_progress":
                message, progress = payload
                self._status(message + f" · %{int(progress * 100)}")
            elif kind == "assets":
                self._download_busy = False
                self._assets_ready = True
                self._support_paths = tuple(str(x) for x in payload)
                self._status("ContentVec ve RMVPE hazır. Ses modelini seçip başlat.")
            elif kind == "error_assets":
                self._download_busy = False
                self._assets_ready = False
                self._status("Destek modelleri indirilemedi: " + str(payload))
            elif kind == "convert_progress":
                self._status(str(payload))
            elif kind == "converted":
                self._converting = False
                self._status("ONNX modeli hazır: " + str(payload))
                if self._pending_start:
                    self._pending_start = False
                    self.start()
            elif kind == "error_converted":
                self._converting = False
                self._pending_start = False
                self._status("ONNX dönüştürme hatası: " + str(payload))
            elif kind == "test_done":
                raw, output, settings, resume = payload
                tools = self.window.owner.studio_tools
                tools.clip = {
                    "raw": raw, "output": output, "rate": 48000,
                    "settings": settings,
                }
                tools.results.clear()
                tools.record_state = "AMD kısa ses testi hazır. ▶ Dinle ile sonucu duyabilirsin."
                tools.ab_state = "Yeni AMD kaydı hazır; A/B ayarlarını kaydet."
                self._status("AMD ses testi dönüştürüldü.")
                if resume and not self._closing.is_set():
                    self.start()
            elif kind == "test_error":
                self.window.owner.studio_tools.record_state = "AMD ses testi hatası: " + payload
                self._status("AMD ses testi hatası: " + payload)
            elif kind == "record_warning":
                self._status("AMD mikrofon kaydı: " + payload)
        if self._ab_resume and not self.window.owner.studio_tools.busy:
            self._ab_resume = False
            self.start()
        if self.bridge.process and not self.bridge.running and not self.bridge._stop_requested:
            code = self.bridge.process.poll()
            if code != self._recent_error:
                self._recent_error = code
                self._status(f"AMD motoru durdu (kod {code}). Günlük sekmesindeki hata satırını incele.")
        self.paint()

    def close(self):
        self._closing.set()
        self.monitor.stop()
        self.gpu.close()
        self.bridge.close()
        if getattr(self, "_timer", None) is not None:
            self._timer.stop()


def install(window_class):
    """Preserve original tray/UI. AMD engine becomes default; no new AMD tab."""
    if getattr(window_class, "_rvc_amd_edition_installed", False):
        return
    # Shared NVIDIA/AMD PTH-based profiles should retain +/-24 semitone
    # settings and stay valid through export/restore.
    from tools import rvc_profile_backup
    rvc_profile_backup.RANGES["pitch"] = (-24, 24)
    old_init = window_class.__init__
    old_read = window_class.read
    old_shutdown = window_class.shutdown
    old_tick = getattr(window_class, "_studio_tick", None)
    old_import = getattr(window_class, "_import_database_model", None)
    old_studio_event = getattr(window_class, "_studio_event", None)
    old_browse = getattr(window_class, "_file_browse", None)
    old_request_voice = getattr(window_class, "_request_voice", None)

    @wraps(old_init)
    def init(self, *args, **kwargs):
        old_init(self, *args, **kwargs)
        try:
            self._amd_edition = AmdEdition(self)
        except Exception as exc:
            # The stock app may remain open, but do not silently use CUDA/CPU.
            print("[AMD Edition] Başlangıç hatası: " + repr(exc).encode("ascii", "backslashreplace").decode("ascii"))
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.warning(self.root, "AMD Edition",
                                "AMD motoru kurulamadı. Günlük sekmesini kontrol et. "
                                "Eski motoru çalıştırmak yerine uygulamayı onar.")

    @wraps(old_read)
    def read(self, *args, **kwargs):
        while True:
            event, values = old_read(self, *args, **kwargs)
            edition = getattr(self, "_amd_edition", None)
            if edition is None:
                # AMD-specific release must not launch unmodified PyTorch engine.
                if event == "start_vc":
                    continue
                return event, values
            if event == "_browse_pth":
                edition.browse()
                continue
            if event == "_browse_index":
                from PySide6.QtWidgets import QFileDialog
                selected, _ = QFileDialog.getOpenFileName(
                    self.root, "FAISS INDEX modeli", "", "Index (*.index)"
                )
                if selected:
                    edition.set_value("index_path", selected)
                    edition.schedule_restart()
                continue
            if event == "reload_devices":
                edition.refresh_devices()
                continue
            if event in ("sg_input_device", "sg_output_device", "pitch",
                         "rms_mix_rate", "threhold"):
                if not edition._listing:
                    edition.schedule_restart()
                continue
            if event == "profile_add":
                edition.add_portable_profile()
                continue
            if event == "profile_select" and edition.bridge.running:
                edition.stop()
                from PySide6.QtCore import QTimer
                QTimer.singleShot(500, edition.start)
                return event, values
            if event == "start_vc":
                edition.start()
                continue
            if event == "stop_vc":
                edition.stop()
                continue
            if event in ("im", "vc"):
                edition.set_passthrough(bool(values.get("im", False)))
                continue
            if event in ("index_rate", "formant", "pm", "fcpe"):
                continue
            return event, values

    @wraps(old_shutdown)
    def shutdown(self, *args, **kwargs):
        edition = getattr(self, "_amd_edition", None)
        if edition is not None:
            edition.close()
        return old_shutdown(self, *args, **kwargs)

    if old_tick:
        @wraps(old_tick)
        def tick(self, *args, **kwargs):
            result = old_tick(self, *args, **kwargs)
            edition = getattr(self, "_amd_edition", None)
            if edition:
                edition.paint()
                record_btn = getattr(self, "record_button", None)
                if record_btn is not None and edition._recording:
                    record_btn.setText("■  AMD Kaydını Bitir")
            return result
        window_class._studio_tick = tick

    if old_import:
        @wraps(old_import)
        def import_model(self, repo, files):
            result = old_import(self, repo, files)
            edition = getattr(self, "_amd_edition", None)
            if edition:
                models = [str(x) for x in files if str(x).lower().endswith(".pth")]
                if models:
                    edition.convert_model(models[0])
            return result
        window_class._import_database_model = import_model

    if old_browse is not None:
        @wraps(old_browse)
        def file_browse(self, event, *args, **kwargs):
            edition = getattr(self, "_amd_edition", None)
            if edition is not None and event == "_browse_pth":
                edition.browse()
                return
            if edition is not None and event == "_browse_index":
                return old_browse(self, event, *args, **kwargs)
            return old_browse(self, event, *args, **kwargs)
        window_class._file_browse = file_browse

    if old_request_voice is not None:
        @wraps(old_request_voice)
        def request_voice(self, mode, *args, **kwargs):
            result = old_request_voice(self, mode, *args, **kwargs)
            edition = getattr(self, "_amd_edition", None)
            if edition is not None:
                edition.set_passthrough(mode == "im")
            return result
        window_class._request_voice = request_voice

    if old_studio_event is not None:
        @wraps(old_studio_event)
        def studio_event(self, event, *args, **kwargs):
            edition = getattr(self, "_amd_edition", None)
            if edition is not None:
                if event == "studio_record":
                    self.owner.studio_tools.amd_record(
                        float(self.widgets["studio_seconds"].get()), self._values()
                    )
                    return
                if event == "studio_clear_cache":
                    if edition.bridge.running:
                        edition.schedule_restart()
                        text = "AMD tampon sıfırlanıyor · ses motoru yeniden başlatılıyor."
                    else:
                        text = "AMD motoru duruyor; temizlenecek aktif tampon yok."
                    self.clear_cache_status.setText(text)
                    return
                if event == "studio_mute":
                    tools = self.owner.studio_tools
                    tools.muted = not tools.muted
                    if edition.bridge.running:
                        edition.schedule_restart()
                    return
            return old_studio_event(self, event, *args, **kwargs)
        window_class._studio_event = studio_event

    window_class.__init__ = init
    window_class.read = read
    window_class.shutdown = shutdown
    window_class._rvc_amd_edition_installed = True
