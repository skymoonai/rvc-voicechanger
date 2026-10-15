"""Experimental vc-rs Windows ML companion engine for RVC Studio.

This is an explicit opt-in sidecar, not a replacement for the PyTorch engine.
vc-rs owns WASAPI input/output, ONNX inference and its realtime worker.
No model code, audio processing or blocking subprocess IO runs in Qt callbacks.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

from tools.rvc_user_state import data_root

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_FILE = data_root() / "amd_vcrs_preview.json"
PROVIDERS = {"DirectML": "windowsml-directml", "MIGraphX (deneysel)": "windowsml-migraphx"}
CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class VcRsError(RuntimeError):
    """Expected sidecar or configuration failure."""


def find_executable() -> Path:
    override = os.environ.get("RVC_STUDIO_VCRS_EXE", "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate
        raise VcRsError("RVC_STUDIO_VCRS_EXE dosyası bulunamadı: " + str(candidate))
    home = ROOT / "engines" / "vc-rs"
    preferred = home / "vc-rs.exe"
    if preferred.is_file():
        return preferred
    matches = sorted(home.glob("*/vc-rs.exe")) if home.is_dir() else []
    if matches:
        return matches[0]
    raise VcRsError(
        "vc-rs.exe bulunamadı. Önce scripts/install_vcrs_amd.ps1 dosyasını çalıştır. "
        "Kurulum mevcut RVC Studio Python ortamını değiştirmez."
    )


def resolve_voice_model(model_path: str) -> Path:
    if not model_path.strip():
        raise VcRsError("ONNX modeli seç. PTH kullanıyorsan önce vc-rs GUI ile ONNX'e dönüştür.")
    path = Path(model_path.strip()).expanduser()
    if path.suffix.lower() == ".pth":
        path = path.with_suffix(".onnx")
        if not path.is_file():
            raise VcRsError(
                "Bu PTH modelinin yanında ONNX karşılığı yok: " + str(path)
                + "\nvc-rs GUI içindeki PTH -> ONNX dönüştürücüsünü kullan (RVC v2/F0)."
            )
    if path.suffix.lower() != ".onnx" or not path.is_file():
        raise VcRsError("Geçerli bir .onnx model dosyası seç: " + str(path))
    return path.resolve()


def resolve_support_model(value: str, filename: str) -> Path:
    """Prefer explicit paths; never download copyrighted weights silently."""
    value = (value or "").strip()
    if value:
        candidate = Path(value).expanduser()
        if candidate.is_file() and candidate.suffix.lower() == ".onnx":
            return candidate.resolve()
        raise VcRsError(filename + " bulunamadı: " + str(candidate))
    home = find_executable().parent
    from tools.rvc_amd_assets import BASE, MODELS, verified
    preferred = BASE / filename
    if filename in MODELS and verified(preferred, MODELS[filename]):
        return preferred.resolve()
    roots = (home / "assets", home, ROOT / "assets" / "vcrs")
    for folder in roots:
        candidate = folder / filename
        if candidate.is_file():
            return candidate.resolve()
    # vc-rs GUI stores downloaded models in an app-owned folder.
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        base = Path(local) / "vc-rs" / "models"
        if base.is_dir():
            for candidate in base.rglob(filename):
                if candidate.is_file():
                    return candidate.resolve()
    raise VcRsError(
        filename + " bulunamadı. vc-rs GUI ile destek modellerini indir "
        "veya AMD sekmesinden bu ONNX dosyasının yolunu seç."
    )


def load_preferences() -> dict:
    try:
        value = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def save_preferences(data: dict) -> None:
    """Only store user-controlled model paths/settings, never raw audio."""
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    allowed = ("model", "embedder", "f0", "input", "output", "provider", "chunk_ms", "pitch")
    payload = {key: str(data.get(key, "")) for key in allowed}
    temporary = SETTINGS_FILE.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, SETTINGS_FILE)


class VcRsBridge:
    """Non-blocking lifecycle wrapper for vc-rs.exe run."""

    def __init__(self):
        self.process: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._messages = deque(maxlen=100)
        self._lock = threading.Lock()
        self._stop_requested = False
        self.metrics = {"infer_ms": None, "processing_ms": None, "content_delay_ms": None}
        self._metrics_lock = threading.Lock()

    def _log(self, line: str) -> None:
        message = line.rstrip()[:1200]
        if "infer=" in message and "content_delay_ms=" in message:
            self._parse_metrics(message)
            return
        with self._lock:
            self._messages.append(message)
        if message:
            try:
                print("[AMD] " + message, flush=True)
            except UnicodeEncodeError:
                # Fresh Windows VMs often default to a legacy console codepage.
                encoding = getattr(sys.stdout, "encoding", None) or "ascii"
                safe = ("[AMD] " + message).encode(encoding, errors="replace").decode(encoding)
                print(safe, flush=True)

    def _parse_metrics(self, message: str) -> None:
        def take(key):
            match = re.search(r"(?<!\w)" + re.escape(key) + r"=([0-9.]+)", message)
            return float(match.group(1)) if match else None
        inference = take("infer")
        processing = take("processing")
        delay = take("content_delay_ms")
        with self._metrics_lock:
            self.metrics = {
                "infer_ms": inference / 1000.0 if inference is not None else None,
                "processing_ms": processing / 1000.0 if processing is not None else None,
                "content_delay_ms": delay,
            }

    def snapshot_metrics(self) -> dict:
        with self._metrics_lock:
            return dict(self.metrics)

    def log_tail(self, lines: int = 12) -> str:
        with self._lock:
            return "\n".join(list(self._messages)[-lines:])

    def _drain(self, stream) -> None:
        try:
            for line in stream:
                self._log(line)
        except (OSError, ValueError) as exc:
            self._log("vc-rs log okuma hatası: " + str(exc))
        finally:
            try:
                stream.close()
            except OSError:
                pass

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def start(
        self, *, model: str, embedder: str, f0: str,
        input_device: str = "", output_device: str = "",
        provider: str = "DirectML", chunk_ms: int = 500, pitch: float = 0.0,
        rms_mix_rate: float = 0.0, silence_threshold: float = 0.0001,
        denoiser: str = "off", passthrough: bool = False,
        input_gain: float = 1.0, output_gain: float = 1.0,
        extra_convert_ms: int = 100, speaker_id: int = 0
    ) -> None:
        if sys.platform != "win32":
            raise VcRsError("AMD vc-rs motoru şu anda yalnızca Windows x64 içindir.")
        if self.running:
            raise VcRsError("AMD ses motoru zaten çalışıyor.")
        if provider not in PROVIDERS:
            raise VcRsError("Bilinmeyen GPU motoru: " + provider)
        if denoiser not in ("off", "noise-gate", "rnnoise"):
            raise VcRsError("Bu Rust sürümünde gürültü filtresi desteklenmiyor: " + denoiser)
        if not (0.0 <= rms_mix_rate <= 1.0) or not (0 <= silence_threshold <= 1):
            raise VcRsError("Ses yüksekliği/eşik ayarları geçersiz.")
        if not (0.0 <= input_gain <= 8.0 and 0.0 <= output_gain <= 8.0):
            raise VcRsError("Giriş/çıkış ses düzeyi geçersiz.")
        if not (0 <= speaker_id <= 1024 and 20 <= extra_convert_ms <= 2000):
            raise VcRsError("Ses modeli/ek bağlam değeri geçersiz.")
        if provider == "MIGraphX (deneysel)":
            raise VcRsError(
                "MIGraphX için AMD Windows ML sağlayıcısı doğrulanamadı. "
                "Bu sürümde güvenli DirectML sağlayıcısını seç."
            )
        if not isinstance(chunk_ms, int) or not 20 <= chunk_ms <= 2000 or chunk_ms % 10:
            raise VcRsError("Chunk süresi 20–2000 ms aralığında 10'un katı olmalı.")
        exe = find_executable()
        voice = resolve_voice_model(model)
        contentvec = resolve_support_model(embedder, "content_vec_500.onnx")
        rmvpe = resolve_support_model(f0, "rmvpe.onnx")
        cmd = [
            str(exe), "run", "--provider", PROVIDERS[provider],
            "--model", str(voice), "--embedder", str(contentvec),
            "--f0-model", str(rmvpe), "--audio-backend", "wasapi",
            "--chunk-ms", str(chunk_ms), "--pitch-shift", str(float(pitch)),
            "--rms-mix-rate", str(float(rms_mix_rate)),
            "--silence-threshold", str(float(silence_threshold)),
            "--input-gain", str(float(input_gain)),
            "--output-gain", str(float(output_gain)),
            "--extra-convert-ms", str(int(extra_convert_ms)),
            "--speaker-id", str(int(speaker_id)),
            "--denoiser", denoiser,
        ]
        if passthrough:
            cmd.append("--passthrough")
        if input_device.strip():
            cmd.extend(["--input", input_device.strip()])
        if output_device.strip():
            cmd.extend(["--output", output_device.strip()])
        self._stop_requested = False
        with self._metrics_lock:
            self.metrics = {"infer_ms": None, "processing_ms": None, "content_delay_ms": None}
        self._log("Başlatılıyor: " + PROVIDERS[provider] + " / " + voice.name)
        try:
            self.process = subprocess.Popen(
                cmd, cwd=str(exe.parent), stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1, creationflags=CREATION_FLAGS,
                env=dict(os.environ, RUST_LOG="info"),
            )
        except (OSError, ValueError) as exc:
            self.process = None
            raise VcRsError("vc-rs başlatılamadı: " + str(exc)) from exc
        assert self.process.stdout is not None
        self._reader = threading.Thread(
            target=self._drain, args=(self.process.stdout,), daemon=True,
            name="RVCStudioAMD-logs"
        )
        self._reader.start()

    def stop(self) -> None:
        self._stop_requested = True
        if self.running:
            try:
                self.process.terminate()  # no blocking wait in Qt thread
            except OSError as exc:
                self._log("vc-rs durdurma hatası: " + str(exc))

    def close(self) -> None:
        self.stop()

    def status(self) -> tuple[str, str]:
        if self.process is None:
            return ("Durduruldu", self.log_tail())
        code = self.process.poll()
        if code is None:
            return ("Çalışıyor (AMD ONNX deneme motoru)", self.log_tail())
        if self._stop_requested:
            return ("Durduruldu", self.log_tail())
        return (f"Motor sonlandı (kod: {code}). Günlüğü kontrol et.", self.log_tail())

    def list_devices_async(self) -> None:
        exe = find_executable()

        def inspect():
            try:
                done = subprocess.run(
                    [str(exe), "devices", "--audio-backend", "wasapi"],
                    cwd=str(exe.parent), capture_output=True, text=True,
                    encoding="utf-8", errors="replace", timeout=15,
                    creationflags=CREATION_FLAGS, check=False,
                )
                self._log("WASAPI aygıtları:\n" + (done.stdout or done.stderr or "Çıktı yok")[:6000])
            except (OSError, subprocess.TimeoutExpired) as exc:
                self._log("Aygıt sorgulama hatası: " + str(exc))
        threading.Thread(target=inspect, name="RVCStudioAMD-devices", daemon=True).start()

    def open_converter(self) -> None:
        exe = find_executable()
        gui = exe.with_name("vc-gui.exe")
        if not gui.is_file():
            raise VcRsError("vc-gui.exe bulunamadı; vc-rs Windows ML GUI+CLI paketini kur.")
        subprocess.Popen([str(gui)], cwd=str(gui.parent),
                         stdin=subprocess.DEVNULL, creationflags=CREATION_FLAGS)
