"""Native RNNoise, 48 kHz / 480-sample frames, with continuous rate conversion."""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import numpy as np
from tools.rvc_audio_files import atomic_json

ROOT = Path(__file__).resolve().parent.parent


class LinearResampler:
    """Retains interpolation phase and one sample across callback boundaries."""
    def __init__(self, source, target):
        self.ratio = float(source) / target
        self.pending = np.empty(0, np.float32)
        self.phase = 0.0

    def push(self, block):
        if self.ratio == 1:
            return np.asarray(block, dtype=np.float32)
        self.pending = np.concatenate((self.pending, block))
        n = max(0, int(np.ceil((len(self.pending)-1-self.phase) / self.ratio)))
        if not n:
            return np.empty(0, np.float32)
        positions = self.phase + np.arange(n)*self.ratio
        low = positions.astype(np.int64)
        values = self.pending[low] + (self.pending[low+1]-self.pending[low])*(positions-low)
        advance = self.phase + n*self.ratio
        consumed = min(int(advance), len(self.pending)-1)
        self.phase = advance-consumed
        self.pending = self.pending[consumed:]
        return values.astype(np.float32)


class NativeRNNoise:
    def __init__(self, path=None):
        path = Path(path) if path else ROOT/'assets'/'rnnoise'/('rnnoise.dll' if os.name == 'nt' else 'librnnoise.so')
        if not path.is_file():
            raise RuntimeError('RNNoise kitaplığı eksik: ' + str(path))
        try:
            self.lib = ctypes.CDLL(str(path))
        except OSError as exc:
            raise RuntimeError('RNNoise yüklenemedi. Windows için Microsoft Visual C++ 2015–2022 x64 çalışma zamanı gerekli. ' + str(exc)) from exc
        lib = self.lib
        lib.rnnoise_create.argtypes = [ctypes.c_void_p]
        lib.rnnoise_create.restype = ctypes.c_void_p
        lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
        lib.rnnoise_destroy.restype = None
        lib.rnnoise_get_frame_size.argtypes = []
        lib.rnnoise_get_frame_size.restype = ctypes.c_int
        lib.rnnoise_process_frame.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
        lib.rnnoise_process_frame.restype = ctypes.c_float
        if lib.rnnoise_get_frame_size() != 480:
            raise RuntimeError('Uyumsuz RNNoise kare uzunluğu.')
        self.state = lib.rnnoise_create(None)
        if not self.state:
            raise MemoryError('RNNoise durumu oluşturulamadı.')

    def process(self, block):
        values = np.ascontiguousarray(np.clip(block, -1, 1)*32768, dtype=np.float32)
        ptr = values.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
        vad = float(self.lib.rnnoise_process_frame(self.state, ptr, ptr))
        values /= 32768
        return values, vad

    def close(self):
        if self.state:
            self.lib.rnnoise_destroy(self.state)
            self.state = None

    def __del__(self):
        if getattr(self, 'state', None):
            self.close()


class NoiseFilter:
    def __init__(self, path=None, native_path=None):
        self.path = Path(path) if path else ROOT/'configs'/'rnnoise.json'
        self.native_path = native_path
        self.settings = {'enabled': False, 'strength': 100, 'gate': False, 'threshold': 20, 'hold_ms': 300}
        try:
            loaded = json.loads(self.path.read_text(encoding='utf-8'))
            self.settings.update({k: loaded[k] for k in self.settings if k in loaded})
        except (OSError, ValueError, TypeError):
            pass
        self.revision = 0
        self.active_revision = -1
        self.rate = 0
        self.native = None
        self.speech = 0.0
        self.error = ''
        self.reduction_db = 0.0

    def configure(self, **settings):
        candidate = dict(self.settings, **{k:v for k,v in settings.items() if k in self.settings})
        candidate['strength'] = min(100, max(0, int(candidate['strength'])))
        candidate['threshold'] = min(90, max(1, int(candidate['threshold'])))
        candidate['hold_ms'] = min(2000, max(50, int(candidate['hold_ms'])))
        if candidate['enabled'] and not self.settings['enabled']:
            # Validate on the UI thread. The callback owns its own processing state.
            check = NativeRNNoise(self.native_path)
            check.close()
        atomic_json(self.path, candidate)
        reset = bool(candidate['enabled']) != bool(self.settings['enabled']) or bool(self.error)
        self.settings = candidate
        self.error = ''
        if reset:
            self.revision += 1

    def reset(self):
        self.revision += 1  # Reset at the next callback, never destroy a state in use.

    def _reset_audio(self, rate):
        self.close()
        self.native = NativeRNNoise(self.native_path)
        self.rate = int(rate)
        self.to48 = LinearResampler(rate, 48000)
        self.from48 = LinearResampler(48000, rate)
        self.pending = np.empty(0, np.float32)
        # Fixed reservoir removes variable frame waiting and rate-conversion jitter.
        self.latency_frames = int(np.ceil(.021*rate))
        self.wet = np.zeros(self.latency_frames, np.float32)
        self.dry = np.zeros(self.latency_frames + int(round(.01*rate)), np.float32)
        self.hold = 0
        self.gate_gain = 1.0
        self.active_revision = self.revision

    def process(self, input_audio, rate):
        settings = self.settings
        if not settings['enabled'] or self.error:
            return input_audio
        try:
            if self.native is None or self.rate != int(rate) or self.active_revision != self.revision:
                self._reset_audio(rate)
            mono = np.mean(input_audio, axis=1, dtype=np.float32) if input_audio.ndim == 2 else input_audio
            self.pending = np.concatenate((self.pending, self.to48.push(mono)))
            results = []
            count = len(self.pending)//480
            for pos in range(count):
                cleaned, self.speech = self.native.process(self.pending[pos*480:(pos+1)*480])
                if self.speech >= settings['threshold']/100:
                    self.hold = int(settings['hold_ms'])//10
                else:
                    self.hold = max(0, self.hold-1)
                target = 1.0 if not settings['gate'] or self.hold else 0.0
                # A 10 ms ramp prevents discontinuities at speech-gate transitions.
                cleaned *= np.linspace(self.gate_gain, target, 480, dtype=np.float32)
                self.gate_gain = target
                results.append(cleaned)
            self.pending = self.pending[count*480:]
            if results:
                self.wet = np.concatenate((self.wet, self.from48.push(np.concatenate(results))))
            n = len(mono)
            self.dry = np.concatenate((self.dry, mono))
            if len(self.wet) < n:
                raise RuntimeError('RNNoise örnek tamponu yetersiz.')
            wet, dry = self.wet[:n], self.dry[:n]
            self.wet, self.dry = self.wet[n:], self.dry[n:]
            mix = settings['strength']/100
            result = (wet*mix + dry*(1-mix)).astype(np.float32)
            power = float(np.mean(mono**2))
            self.reduction_db = max(0, min(60, 10*np.log10((power+1e-12)/(float(np.mean(result**2))+1e-12))))
            return np.repeat(result[:, None], input_audio.shape[1], axis=1) if input_audio.ndim == 2 else result
        except Exception as exc:
            # A failed noise filter must not interrupt the microphone / RVC stream.
            self.error = str(exc)
            print('RNNoise: ' + self.error, flush=True)
            return input_audio

    def close(self):
        if self.native is not None:
            self.native.close()
            self.native = None
