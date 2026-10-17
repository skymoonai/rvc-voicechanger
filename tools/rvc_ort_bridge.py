"""RVC Studio AMD ONNX Runtime DirectML subprocess controller.

Replaces the Rust vc-rs audio engine, keeps the original Qt edition overlay.
Python inference runs in an isolated .venv-ort-dml to avoid altering installed
NVIDIA/CUDA/Torch packages.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

from tools.rvc_user_state import data_root

ROOT = Path(__file__).resolve().parents[1]
SETTINGS_FILE = data_root() / "amd_ort_directml.json"
WIN_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


class OrtError(RuntimeError):
    pass


def python_executable() -> Path:
    path = ROOT / ".venv-ort-dml" / "Scripts" / "python.exe"
    if not path.is_file():
        raise OrtError(
            "ONNX Runtime DirectML Python kurulumu yok: " + str(path)
            + ". scripts/install_amd_ort_directml.ps1 ile kur."
        )
    return path


def worker_file() -> Path:
    path = ROOT / "tools" / "rvc_ort_worker.py"
    if not path.is_file():
        raise OrtError("ONNX Runtime ses islemcisi bulunamadi: " + str(path))
    return path


def load_preferences():
    try:
        obj = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except (ValueError, OSError):
        return {}


def save_preferences(data):
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    allowed = ("model", "embedder", "f0", "input", "output", "provider",
               "chunk_ms", "pitch", "quality", "rnnoise")
    path = SETTINGS_FILE.with_suffix(".tmp")
    path.write_text(json.dumps({k: str(data.get(k, "")) for k in allowed},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(path, SETTINGS_FILE)


def resolve_voice_model(model):
    path = Path(str(model or "")).expanduser()
    if path.suffix.lower() == ".pth":
        path = path.with_suffix(".onnx")
    if not path.is_file() or path.suffix.lower() != ".onnx":
        raise OrtError("ONNX modeli bulunamadi: " + str(path))
    return path.resolve()


def resolve_support_model(value, filename):
    value = str(value or "")
    path = Path(value).expanduser() if value else None
    if path and path.is_file():
        return path.resolve()
    from tools.rvc_amd_assets import BASE, MODELS, verified
    path = BASE / filename
    if verified(path, MODELS[filename]):
        return path.resolve()
    raise OrtError(filename + " eksik veya SHA256 dogrulanmadi: " + str(path))


def command_prefix():
    return [str(python_executable()), "-u", str(worker_file())]


def build_common(model, embedder, f0, pitch, chunk_ms, extra_convert_ms,
                 rms_mix_rate, silence_threshold, output_gain,
                 index="", index_rate=0., protect=.5):
    return command_prefix() + [
        "--model", str(resolve_voice_model(model)),
        "--embedder", str(resolve_support_model(embedder, "content_vec_500.onnx")),
        "--f0-model", str(resolve_support_model(f0, "rmvpe.onnx")),
        "--pitch-shift", str(float(pitch)),
        "--chunk-ms", str(int(chunk_ms)),
        "--extra-convert-ms", str(int(extra_convert_ms)),
        "--rms-mix-rate", str(float(rms_mix_rate)),
        "--silence-threshold", str(float(silence_threshold)),
        "--output-gain", str(float(output_gain)),
        "--index", str(index or ""),
        "--index-rate", str(float(index_rate)),
        "--protect", str(float(protect)),
    ]


class OrtBridge:
    def __init__(self):
        self.process = None
        self._stop_requested = True
        self._logs = deque(maxlen=150)
        self._lock = threading.Lock()
        self.metrics = {"infer_ms": None, "processing_ms": None,
                        "content_delay_ms": None, "input_rms": None,
                        "output_rms": None, "input_overruns": 0,
                        "output_underruns": 0, "output_dropped_samples": 0}

    @property
    def running(self):
        return self.process is not None and self.process.poll() is None

    def _drain(self, stream):
        for line in iter(stream.readline, ""):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                kind = obj.get("kind")
                if kind == "metrics":
                    with self._lock:
                        for key in self.metrics:
                            if key in obj:
                                self.metrics[key] = obj[key]
                    continue
                if kind == "error":
                    text = "ONNX DirectML HATA: " + str(obj.get("message", ""))
                else:
                    text = str(obj.get("message", line))
            except (ValueError, AttributeError):
                text = line
            with self._lock:
                self._logs.append(text)
            print("[AMD ORT] " + text.encode("ascii", "backslashreplace").decode("ascii"),
                  flush=True)

    def snapshot_metrics(self):
        with self._lock:
            return dict(self.metrics)

    def log_tail(self, lines=12):
        with self._lock:
            return "\n".join(list(self._logs)[-lines:])

    def start(self, *, model, embedder, f0, input_device="", output_device="",
              provider="DirectML", chunk_ms=400, pitch=0., rms_mix_rate=1.,
              silence_threshold=0., denoiser="off", passthrough=False,
              output_gain=1., extra_convert_ms=400, crossfade_ms=40,
              index="", index_rate=0., protect=.5, **_ignored):
        if provider != "DirectML":
            raise OrtError("Bu surum yalniz ONNX Runtime DirectML motorunu destekler.")
        if denoiser != "off":
            raise OrtError("RNNoise ONNX motorunda henuz uygulanmiyor. Ses temizlemeyi kapat.")
        if self.running:
            return
        argv = build_common(
            model, embedder, f0, pitch, chunk_ms, extra_convert_ms,
            rms_mix_rate, silence_threshold, output_gain,
            index=index, index_rate=index_rate, protect=protect,
        )
        argv.insert(3, "run")
        argv += ["--input-device", str(input_device),
                 "--output-device", str(output_device)]
        if passthrough:
            argv.append("--passthrough")
        with self._lock:
            self._logs.clear()
        self._stop_requested = False
        self.process = subprocess.Popen(
            argv, cwd=str(ROOT), stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1, encoding="utf-8", errors="replace",
            creationflags=WIN_FLAGS,
        )
        threading.Thread(target=self._drain, args=(self.process.stdout,),
                         daemon=True, name="RVCStudio-ORT-Logs").start()

    def stop(self):
        self._stop_requested = True
        old, self.process = self.process, None
        if old and old.poll() is None:
            old.terminate()
            try:
                old.wait(timeout=4)
            except subprocess.TimeoutExpired:
                old.kill()
                old.wait(timeout=2)

    def close(self):
        self.stop()

    def status(self):
        if self.running:
            return "Calisiyor (ONNX Runtime DirectML)", self.log_tail()
        return "Durdu (ONNX Runtime DirectML)", self.log_tail()


# Reuse the interface name required by the existing beta 1.5.2 Qt patch.
VcRsBridge = OrtBridge
VcRsError = OrtError
