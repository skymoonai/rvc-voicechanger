"""RVC Studio AMD Edition file conversion and short-test/A-B using vc-rs WAV.

Offline conversion delegates to vc-rs (same ONNX model, RMVPE, DirectML).
The existing RVC Studio queue, repair pipeline, A/B playback and export UI
remain intact. WAV conversion never starts the old PyTorch RVC inference path.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import time
import threading
from pathlib import Path
from types import MethodType

from tools.rvc_amd_bridge import find_executable, resolve_voice_model, CREATION_FLAGS
from tools.rvc_amd_convert import convert_pth


def onnx_model(pth_or_onnx: str) -> Path:
    path = Path(str(pth_or_onnx or "")).expanduser()
    if path.suffix.lower() == ".pth" and path.is_file():
        target = path.with_suffix(".onnx")
        if not target.exists():
            convert_pth(str(path))
        return resolve_voice_model(str(target))
    return resolve_voice_model(str(path))


def make_command(edition, source: Path, target: Path, model: Path, settings: dict, *, rnnoise=False):
    if not edition._assets_ready:
        raise RuntimeError("ContentVec ve RMVPE henüz hazır değil.")
    exe = find_executable()
    command = [
        str(exe), "wav",
        "--model", str(model),
        "--embedder", edition._support_paths[0],
        "--f0-model", edition._support_paths[1],
        "--provider", "windowsml-directml",
        "--input", str(source),
        "--output", str(target),
        "--pitch-shift", str(float(settings.get("pitch", 0))),
        "--rms-mix-rate", str(max(0., min(1., float(settings.get("rms_mix_rate", 0))))),
        "--chunk-ms", str(max(50, min(1000, int(settings.get("amd_chunk_ms", 200))))),
        "--silence-threshold", str(max(0.000001, min(1., 10 ** (float(settings.get("threhold", -60)) / 20)))),
    ]
    if rnnoise:
        command.extend(["--denoiser", "rnnoise"])
    return command


def wav_convert(edition, input_path: Path, output_path: Path, settings: dict,
                *, cancel=None, rnnoise=False):
    """Convert input WAV or MP3 to vc-rs WAV via bounded decode, atomic output."""
    import soundfile as sf
    from tools.rvc_audio_files import decode_blocks

    model = onnx_model(settings["pth_path"])
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="rvcstudio-amd-wav-") as temp_dir:
        temporary = Path(temp_dir)
        source = temporary / "source.wav"
        target = temporary / "converted.wav"
        with sf.SoundFile(source, "w", samplerate=48000, channels=1, format="WAV", subtype="PCM_16") as audio:
            for block in decode_blocks(input_path, rate=48000, cancel=cancel, channels=1):
                audio.write(block)
        command = make_command(edition, source, target, model, settings, rnnoise=rnnoise)
        process = subprocess.Popen(
            command, cwd=str(find_executable().parent),
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            creationflags=CREATION_FLAGS,
        )
        logs = []
        # communicate() is used from the existing file-conversion worker,
        # not from the Qt GUI or audio callback.
        try:
            while process.poll() is None:
                if cancel is not None and cancel.is_set():
                    process.terminate()
                    raise InterruptedError("Dosya dönüşümü iptal edildi.")
                time.sleep(.1)
            data = process.communicate(timeout=5)[0] or ""
            logs = data.splitlines()[-8:]
            if process.returncode:
                raise RuntimeError("vc-rs WAV hatası: " + " | ".join(logs)[-1000:])
            if not target.is_file() or not target.stat().st_size:
                raise RuntimeError("vc-rs WAV çıktısı oluşmadı.")
            os.replace(target, output_path)
        finally:
            if process.poll() is None:
                process.terminate()
            if logs:
                try:
                    print("[AMD WAV] " + " | ".join(logs).encode("ascii", "replace").decode("ascii"))
                except OSError:
                    pass


def install_file_conversion(file_converter, edition):
    """Reuse the stock queue/cancellation/status/optional post-repair."""
    def convert_one(self, source, target, settings, options):
        values = dict(settings)
        values["amd_chunk_ms"] = edition.chunk.value() * 10
        wav_convert(edition, source, target, values,
                    cancel=self.cancel, rnnoise=bool(options.get("rnnoise")))

    original_start = file_converter.start
    original_poll = file_converter.poll

    def start(values):
        edition._file_resume = edition.bridge.running
        if edition.bridge.running:
            edition.stop()
        try:
            return original_start(values)
        except Exception:
            if edition._file_resume:
                edition.start()
                edition._file_resume = False
            raise

    def poll():
        changed, resume = original_poll()
        if (getattr(edition, "_file_resume", False) and not file_converter.busy):
            edition._file_resume = False
            if not file_converter.cancel.is_set():
                edition.start()
        return changed, False  # old Python stream must not resume

    file_converter._convert_one = MethodType(convert_one, file_converter)
    file_converter.start = start
    file_converter.poll = poll


def array_convert(edition, raw, rate: int, settings: dict):
    """Render A/B and test captures using the same Rust ONNX pipeline."""
    import soundfile as sf
    import numpy as np
    from scipy.signal import resample_poly
    import math
    with tempfile.TemporaryDirectory(prefix="rvcstudio-amd-ab-") as temp:
        source = Path(temp) / "input.wav"
        output = Path(temp) / "result.wav"
        audio = np.asarray(raw, dtype=np.float32)
        if rate != 48000:
            gcd = math.gcd(rate, 48000)
            audio = resample_poly(audio, 48000 // gcd, rate // gcd)
        sf.write(source, audio, 48000, subtype="PCM_16")
        props = dict(settings, amd_chunk_ms=edition.chunk.value() * 10)
        model = onnx_model(settings.get("pth_path", ""))
        command = make_command(edition, source, output, model, props)
        result = subprocess.run(command, cwd=str(find_executable().parent),
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=180, creationflags=CREATION_FLAGS)
        if result.returncode or not output.is_file():
            raise RuntimeError("AMD karşılaştırma: " + (result.stderr or result.stdout)[-700:])
        converted, actual_rate = sf.read(output, dtype="float32", always_2d=False)
        if getattr(converted, "ndim", 1) != 1:
            converted = converted.mean(axis=1)
        if actual_rate != rate:
            gcd = math.gcd(actual_rate, rate)
            converted = resample_poly(converted, rate // gcd, actual_rate // gcd)
        return np.asarray(converted, dtype=np.float32)


def install_studio_tests(studio_tools, edition):
    """Replace only the RVC render function, leave saved A/B snapshots intact."""
    def render(self, raw, input_rate, settings):
        rendered = array_convert(edition, raw, input_rate, settings)
        return {"audio": rendered, "rate": input_rate, "settings": settings}
    studio_tools._render = MethodType(render, studio_tools)
    old_compare = studio_tools.compare

    def compare():
        edition._ab_resume = edition.bridge.running
        if edition.bridge.running:
            edition.stop()
        return old_compare()
    studio_tools.compare = compare

def install_capture(studio_tools, edition):
    """Capture actual mic input for short tests without a Python RVC stream.

    Converted test is created by Rust 'vc-rs wav'; this source capture is NOT
    taken from the legacy muted Python callback.
    """
    def toggle(seconds, settings):
        if getattr(edition, "_recording", False):
            edition._capture_stop.set()
            return
        if not edition.bridge.running:
            studio_tools.record_state = "Önce AMD ses dönüşümünü başlat."
            return
        if not edition._assets_ready:
            studio_tools.record_state = "Önce ContentVec/RMVPE hazırlansın."
            return
        seconds = max(1, min(30, float(seconds)))
        edition._capture_stop = threading.Event()
        edition._recording = True
        studio_tools.record_state = "AMD test kaydı alınıyor…"
        studio_tools.results = {}
        studio_tools.clip = None

        def work():
            import sounddevice as sd
            import numpy as np
            chunks = []
            try:
                device_name = str(edition.read_value("sg_input_device", "") or "")
                devices = sd.query_devices()
                options = [i for i, item in enumerate(devices)
                           if item["max_input_channels"] > 0 and
                           str(item["name"]).casefold() == device_name.casefold()]
                chosen = options[0] if len(options) == 1 else None
                with sd.InputStream(device=chosen, samplerate=48000, channels=1,
                                    dtype="float32", blocksize=1024, latency="low") as stream:
                    total = 0
                    limit = int(seconds * 48000)
                    while total < limit and not edition._capture_stop.is_set():
                        block, overrun = stream.read(min(1024, limit - total))
                        if overrun:
                            edition._queue.put(("record_warning", "Kayıtta mikrofon tamponu taştı."))
                        chunks.append(block[:, 0].copy())
                        total += len(block)
                if total < 4800:
                    raise RuntimeError("Kayıt çok kısa.")
                raw = np.concatenate(chunks).astype(np.float32, copy=False)
                # Model conversion competes with a live Rust stream on GPU.
                # Stop it; do not restart until output conversion completes.
                resume = edition.bridge.running
                if resume:
                    edition.bridge.stop()
                settings = dict(settings)
                output = array_convert(edition, raw, 48000, settings)
                edition._queue.put(("test_done", (raw, output, settings, resume)))
            except Exception as exc:
                edition._queue.put(("test_error", str(exc)))
            finally:
                edition._recording = False
        edition._async(work, "test_capture")

    studio_tools.amd_record = toggle
