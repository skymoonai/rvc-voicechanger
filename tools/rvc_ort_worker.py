"""RVC Studio AMD ONNX Runtime / DirectML inference worker.

No vc-rs/Rust executable is called. Handles RVC ONNX + ContentVec + RMVPE,
optional FAISS index, and audio I/O. One inference thread owns all ORT
sessions; PortAudio callbacks are bounded and never do machine learning.
"""
from __future__ import annotations

import argparse
import json
import math
import queue
import sys
import threading
import time
from collections import deque
from pathlib import Path

import numpy as np


class EngineError(RuntimeError):
    pass


def emit(kind, **values):
    print(json.dumps({"kind": kind, **values}, ensure_ascii=True), flush=True)


def session(path, *, require_dml=True):
    import onnxruntime as ort
    options = ort.SessionOptions()
    options.enable_mem_pattern = False
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    try:
        sess = ort.InferenceSession(
            str(path), sess_options=options,
            providers=[("DmlExecutionProvider", {"device_id": 0}),
                       "CPUExecutionProvider"],
        )
    except Exception as exc:
        raise EngineError("ONNX DirectML oturumu acilamadi: " + str(exc)) from exc
    if require_dml and "DmlExecutionProvider" not in sess.get_providers():
        raise EngineError(
            "DmlExecutionProvider etkin degil. onnxruntime-directml kurulumunu "
            "ve Windows DirectX 12 GPU surucusunu kontrol et."
        )
    return sess


def sample_rate_from_metadata(sess):
    meta = sess.get_modelmeta().custom_metadata_map
    for key in ("samplingRate", "sampling_rate", "sample_rate"):
        if key in meta:
            try:
                value = int(meta[key])
                if value in (32000, 40000, 48000):
                    return value
            except (ValueError, TypeError):
                pass
    try:
        data = json.loads(meta.get("metadata", "{}"))
        value = int(data.get("samplingRate", data.get("sample_rate", 0)))
        if value in (32000, 40000, 48000):
            return value
    except (ValueError, TypeError, json.JSONDecodeError):
        pass
    raise EngineError(
        "ONNX modeli ornekleme hizini belirtmiyor. "
        "RVC Studio uyumlu v2/F0 donusturucusunu kullan."
    )


def resample(audio, orig, target):
    if orig == target:
        return np.asarray(audio, np.float32)
    from scipy.signal import resample_poly
    ratio = math.gcd(int(orig), int(target))
    return np.asarray(
        resample_poly(audio, int(target)//ratio, int(orig)//ratio),
        dtype=np.float32,
    )


def pitch_coarse(f0):
    f0 = np.asarray(f0, np.float32)
    mel_min = 1127 * np.log1p(50 / 700)
    mel_max = 1127 * np.log1p(1100 / 700)
    mel = 1127 * np.log1p(np.maximum(f0, 0) / 700)
    return np.asarray(
        np.clip(np.rint((mel - mel_min) * 254 / (mel_max - mel_min) + 1), 1, 255),
        dtype=np.int64,
    )


def align_frames(values, length):
    values = np.asarray(values, dtype=np.float32).reshape(-1)
    if not len(values):
        return np.zeros(length, dtype=np.float32)
    if len(values) == length:
        return values
    # RVC 10ms grid, RMVPE and ContentVec may disagree by one guard frame.
    if abs(len(values) - length) <= 3:
        return np.pad(values, (max(0, length-len(values)), 0))[-length:]
    return np.interp(
        np.linspace(0, len(values)-1, length),
        np.arange(len(values)), values,
    ).astype(np.float32)


class RvcPipeline:
    def __init__(self, model, embedder, f0_model, pitch=0.0, index=None,
                 index_rate=0.0, protect=0.5):
        self.model = session(model)
        self.embedder = session(embedder)
        self.pitch_model = session(f0_model)
        self.pitch_shift = float(pitch)
        self.sample_rate = sample_rate_from_metadata(self.model)
        self.input_specs = {entry.name: entry for entry in self.model.get_inputs()}
        self.rvc_inputs = list(self.input_specs)
        self.feature_name = next((n for n in ("feats", "phone") if n in self.input_specs), None)
        self.length_name = next((n for n in ("p_len", "phone_lengths") if n in self.input_specs), None)
        self.f0_name = next((n for n in ("pitchf", "nsff0") if n in self.input_specs), None)
        self.speaker_name = next((n for n in ("sid", "ds") if n in self.input_specs), None)
        if not all([self.feature_name, self.length_name, self.f0_name, self.speaker_name]):
            raise EngineError("RVC ONNX modelinin girdi isimleri desteklenmiyor: "
                              + ", ".join(self.rvc_inputs))
        feat_shape = self.input_specs[self.feature_name].shape
        self.channels = feat_shape[-1] if isinstance(feat_shape[-1], int) else 768
        self.embedder_input = self.embedder.get_inputs()[0].name
        outputs = self.embedder.get_outputs()
        candidates = [o for o in outputs if len(o.shape) == 3
                      and isinstance(o.shape[-1], int) and o.shape[-1] == self.channels]
        if not candidates:
            raise EngineError(
                "ContentVec cikisi RVC modelinin %s kanal beklentisine uymuyor."
                % self.channels
            )
        self.embedder_output = candidates[0].name
        pitch_inputs = {i.name for i in self.pitch_model.get_inputs()}
        if "waveform" not in pitch_inputs or "threshold" not in pitch_inputs:
            raise EngineError("RMVPE ONNX modeli waveform/threshold girdilerini sunmuyor.")
        self.index_rate = float(np.clip(index_rate, 0, 1))
        self.protect = float(np.clip(protect, 0, 0.5))
        self.index = None
        self.index_vectors = None
        if index and self.index_rate > 0:
            try:
                import faiss
                self.index = faiss.read_index(str(index))
                if self.index.d != self.channels:
                    raise EngineError("INDEX ile ONNX kanal sayisi uyusmuyor.")
                self.index_vectors = self.index.reconstruct_n(0, self.index.ntotal)
            except Exception as exc:
                raise EngineError("FAISS INDEX okunamadi: " + str(exc)) from exc
        emit("status", message="ONNX DirectML: ContentVec + RMVPE + RVC hazir",
             sample_rate=self.sample_rate,
             provider=self.model.get_providers(),
             index_enabled=self.index is not None)

    def _features(self, input_16k):
        source = np.asarray(input_16k, dtype=np.float32)[None, :]
        results = self.embedder.run([self.embedder_output],
                                    {self.embedder_input: source})
        features = np.asarray(results[0], np.float32)
        if features.ndim != 3 or features.shape[-1] != self.channels:
            raise EngineError("ContentVec tensor boyutu gecersiz.")
        return features

    def _pitch(self, input_16k):
        inputs = {"waveform": np.asarray(input_16k, np.float32)[None, :],
                  "threshold": np.array([0.03], np.float32)}
        result = self.pitch_model.run(["pitchf"], inputs)[0]
        f0 = np.maximum(np.asarray(result, np.float32).reshape(-1), 0)
        return f0 * 2.0 ** (self.pitch_shift / 12.0)

    def _index_mix(self, feats, pitch):
        if self.index is None:
            return feats
        source = np.asarray(feats[0], dtype=np.float32)
        k = min(8, self.index.ntotal)
        if k < 1:
            return feats
        dist, ids = self.index.search(source, k)
        ids = np.clip(ids, 0, self.index.ntotal - 1)
        weights = 1 / np.maximum(dist, 1e-6) ** 2
        weights /= weights.sum(axis=1, keepdims=True)
        matched = np.sum(self.index_vectors[ids] * weights[..., None], axis=1)
        matched = np.repeat(matched, 2, axis=0)
        original = np.repeat(source, 2, axis=0)
        n = original.shape[0]
        f0 = align_frames(pitch, n)
        # RVC Protect preserves unvoiced consonants from the original
        # embedding, even when the retrieved INDEX signal is different.
        protect_weight = np.where(f0 > 0, 1.0, self.protect).reshape(-1, 1)
        mixed = original * (1-self.index_rate*protect_weight) + matched * (
            self.index_rate * protect_weight
        )
        return mixed[None, ...]

    def convert(self, audio_16k, out_samples_16k):
        start = time.perf_counter()
        feats = self._features(audio_16k)
        f0 = self._pitch(audio_16k)
        if self.index is None:
            feats = np.repeat(feats, 2, axis=1)
        else:
            feats = self._index_mix(feats, f0)
        length = feats.shape[1]
        pitchf = align_frames(f0, length)
        coarse = pitch_coarse(pitchf)
        fields = {
            self.feature_name: feats.astype(np.float32, copy=False),
            self.length_name: np.asarray([length], np.int64),
            self.f0_name: pitchf[None, :],
            self.speaker_name: np.array([0], np.int64),
        }
        if "pitch" in self.input_specs:
            fields["pitch"] = coarse[None, :]
        for name in self.rvc_inputs:
            if name in fields:
                continue
            if name in ("rnd", "noise", "z"):
                dims = self.input_specs[name].shape
                ch = dims[1] if len(dims) == 3 and isinstance(dims[1], int) else 192
                fields[name] = np.random.default_rng().standard_normal(
                    (1, ch, length)
                ).astype(np.float32)
            else:
                raise EngineError("Bilinmeyen RVC ONNX girdisi: " + name)
        outs = self.model.run(None, fields)
        audio = np.asarray(outs[0], np.float32).reshape(-1)
        wanted = max(1, int(round(out_samples_16k * self.sample_rate / 16000)))
        if len(audio) < wanted:
            audio = np.pad(audio, (wanted-len(audio), 0))
        audio = audio[-wanted:]
        elapsed = (time.perf_counter()-start)*1000
        if not np.isfinite(audio).all():
            raise EngineError("ONNX modeli gecersiz ses ornekleri uretti.")
        return np.clip(audio, -1, 1), elapsed


def resolve_devices(input_name, output_name):
    import sounddevice as sd
    devices = list(sd.query_devices())
    hostapis = list(sd.query_hostapis())

    def match(name, direction):
        if not name:
            default = sd.default.device[0 if direction == "in" else 1]
            return int(default)
        candidates = [i for i, d in enumerate(devices)
                      if d["name"].casefold() == name.casefold()
                      and int(d["max_input_channels" if direction == "in"
                                else "max_output_channels"]) >= 1]
        wasapi = [i for i in candidates
                  if "wasapi" in hostapis[devices[i]["hostapi"]]["name"].lower()]
        if len(wasapi) == 1:
            return wasapi[0]
        if len(candidates) == 1:
            return candidates[0]
        raise EngineError("Ses aygiti bulunamadi veya birden fazla eslesti: " + name)

    mic = match(input_name, "in")
    output = match(output_name, "out")
    if mic == output:
        raise EngineError("Mikrofon ve cikis ayni ses aygiti: ses dongusu riski.")
    return mic, output


def convert_chunk(pipeline, buffer_48k, chunk_ms, context_ms, rate=48000,
                  passthrough=False, rms_mix_rate=0, silence_threshold=0):
    chunk_samples = rate * chunk_ms // 1000
    latest = np.asarray(buffer_48k[-chunk_samples:], np.float32)
    rms = float(np.sqrt(np.mean(latest*latest))) if len(latest) else 0
    if passthrough:
        return latest, 0., rms
    if silence_threshold > 0 and rms < silence_threshold:
        return np.zeros(chunk_samples, np.float32), 0., rms
    keep = rate * (chunk_ms + context_ms) // 1000
    window = np.asarray(buffer_48k[-keep:], np.float32)
    if len(window) < keep:
        window = np.pad(window, (keep-len(window), 0))
    input_16k = resample(window, rate, 16000)
    voice, infer = pipeline.convert(input_16k, 16000*chunk_ms//1000)
    out = resample(voice, pipeline.sample_rate, rate)
    if len(out) < chunk_samples:
        out = np.pad(out, (0, chunk_samples-len(out)))
    out = out[:chunk_samples]
    if rms_mix_rate < 1:
        target = float(np.sqrt(np.mean(out*out))) + 1e-6
        scale = (rms / target) ** (1 - rms_mix_rate)
        out = np.clip(out * np.clip(scale, .2, 5.), -1., 1.)
    return out, infer, rms


def realtime(args):
    import sounddevice as sd
    pipeline = RvcPipeline(args.model, args.embedder, args.f0_model, args.pitch_shift,
                           args.index, args.index_rate, args.protect)
    in_dev, out_dev = resolve_devices(args.input_device, args.output_device)
    rate = 48000
    chunk_samples = rate * args.chunk_ms // 1000
    max_history = rate * (args.chunk_ms + args.extra_convert_ms) // 1000
    recordings = queue.Queue(maxsize=3)
    outputs = queue.Queue(maxsize=3)
    stats = dict(input_overruns=0, output_underruns=0,
                 output_dropped_samples=0, processing_ms=0.,
                 infer_ms=0., input_rms=0., output_rms=0.)
    history = deque(maxlen=max_history)
    audio_stop = threading.Event()
    fatal_errors = []
    last_report = [time.monotonic()]

    def mic_callback(indata, frames, _time, status):
        if status:
            stats["input_overruns"] += 1
        try:
            recordings.put_nowait(np.asarray(indata[:, 0], np.float32).copy())
        except queue.Full:
            stats["input_overruns"] += 1

    def speaker_callback(outdata, frames, _time, status):
        if status:
            stats["output_underruns"] += 1
        outdata.fill(0)
        try:
            samples = outputs.get_nowait()
            length = min(len(samples), frames)
            outdata[:length, 0] = samples[:length]
        except queue.Empty:
            stats["output_underruns"] += 1
            stats["output_dropped_samples"] += frames

    def processor():
        while not audio_stop.is_set():
            try:
                chunk = recordings.get(timeout=.2)
            except queue.Empty:
                continue
            started = time.perf_counter()
            history.extend(chunk.tolist())
            try:
                produced, inference, rms = convert_chunk(
                    pipeline, np.asarray(history, np.float32),
                    args.chunk_ms, args.extra_convert_ms, rate=rate,
                    passthrough=args.passthrough,
                    rms_mix_rate=args.rms_mix_rate,
                    silence_threshold=args.silence_threshold)
                produced = np.clip(produced * args.output_gain, -1., 1.)
                stats["infer_ms"] = round(inference, 2)
                stats["processing_ms"] = round((time.perf_counter()-started)*1000, 2)
                stats["input_rms"] = rms
                stats["output_rms"] = float(np.sqrt(np.mean(produced**2)))
                try:
                    outputs.put_nowait(produced)
                except queue.Full:
                    stats["output_dropped_samples"] += len(produced)
                if time.monotonic()-last_report[0] > 1:
                    emit("metrics", **stats)
                    last_report[0] = time.monotonic()
            except Exception as exc:
                fatal_errors.append(str(exc))
                emit("error", message=str(exc))
                audio_stop.set()

    worker = threading.Thread(target=processor, name="ORT-DML-Inference", daemon=True)
    worker.start()
    try:
        with sd.InputStream(device=in_dev, samplerate=rate, channels=1, dtype="float32",
                            blocksize=chunk_samples, callback=mic_callback, latency="low"), \
             sd.OutputStream(device=out_dev, samplerate=rate, channels=1, dtype="float32",
                             blocksize=chunk_samples, callback=speaker_callback, latency="low"):
            emit("status", message="AMD ONNX Runtime DirectML mikrofon aktif")
            while not audio_stop.wait(.2):
                pass
    except KeyboardInterrupt:
        return
    finally:
        audio_stop.set()
        worker.join(timeout=3)
    if fatal_errors:
        raise EngineError("Canli DirectML islemi durdu: " + fatal_errors[-1])


def offline(args):
    """Streaming 48k WAV conversion, bounded memory for arbitrarily long audio."""
    import soundfile as sf
    pipeline = RvcPipeline(args.model, args.embedder, args.f0_model,
                           args.pitch_shift, args.index, args.index_rate, args.protect)
    chunk_samples = 48000 * args.chunk_ms // 1000
    history_len = 48000 * (args.chunk_ms + args.extra_convert_ms) // 1000
    history = deque(maxlen=history_len)
    with sf.SoundFile(args.input, "r") as source:
        if source.samplerate != 48000:
            raise EngineError(
                "WAV DirectML girdisi 48kHz olmali. Studio dosya yoneticisi "
                "kaynak sesi otomatik 48kHz'e donusturur."
            )
        total = len(source)
        with sf.SoundFile(args.output, "w", samplerate=48000,
                          channels=1, subtype="PCM_16") as target:
            completed = 0
            while True:
                data = source.read(chunk_samples, dtype="float32", always_2d=True)
                if not len(data):
                    break
                samples = np.asarray(data.mean(axis=1), np.float32)
                history.extend(samples.tolist())
                converted, elapsed, _rms = convert_chunk(
                    pipeline, np.asarray(history, np.float32),
                    args.chunk_ms, args.extra_convert_ms,
                    rms_mix_rate=args.rms_mix_rate,
                    silence_threshold=args.silence_threshold,
                )
                target.write(converted[:len(samples)])
                completed += len(samples)
                if completed % (5*chunk_samples) < chunk_samples:
                    emit("metrics", infer_ms=round(elapsed, 1),
                         completed=completed, total=total)
    emit("status", message="WAV DirectML donusumu tamamlandi",
         samples=completed)


def parse(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "wav", "doctor"))
    parser.add_argument("--model")
    parser.add_argument("--embedder")
    parser.add_argument("--f0-model")
    parser.add_argument("--input")
    parser.add_argument("--output")
    parser.add_argument("--input-device", default="")
    parser.add_argument("--output-device", default="")
    parser.add_argument("--pitch-shift", type=float, default=0.)
    parser.add_argument("--index", default="")
    parser.add_argument("--index-rate", type=float, default=0.)
    parser.add_argument("--protect", type=float, default=.5)
    parser.add_argument("--chunk-ms", type=int, default=400)
    parser.add_argument("--extra-convert-ms", type=int, default=400)
    parser.add_argument("--rms-mix-rate", type=float, default=1.)
    parser.add_argument("--silence-threshold", type=float, default=0.)
    parser.add_argument("--output-gain", type=float, default=1.)
    parser.add_argument("--passthrough", action="store_true")
    args = parser.parse_args(argv)
    if args.command != "doctor":
        for key in ("model", "embedder", "f0_model"):
            if not getattr(args, key):
                parser.error("--" + key.replace("_", "-") + " is required")
        if args.chunk_ms % 10 or not 100 <= args.chunk_ms <= 1000:
            parser.error("chunk-ms must be a multiple of 10 between 100 and 1000")
        if not 100 <= args.extra_convert_ms <= 2000:
            parser.error("extra-convert-ms must be 100..2000")
        if args.command == "wav" and (not args.input or not args.output):
            parser.error("wav requires --input and --output")
    return args


def main(argv=None):
    try:
        args = parse(argv)
        if args.command == "doctor":
            import onnxruntime as ort
            import sounddevice as sd
            emit("doctor", ort=ort.__version__,
                 providers=ort.get_available_providers(),
                 devices=[{"name": d["name"],
                           "input": d["max_input_channels"],
                           "output": d["max_output_channels"]}
                          for d in sd.query_devices()])
            if "DmlExecutionProvider" not in ort.get_available_providers():
                raise EngineError("DmlExecutionProvider bu ortamda bulunamadi")
        elif args.command == "run":
            realtime(args)
        else:
            offline(args)
        return 0
    except Exception as exc:
        emit("error", message=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
