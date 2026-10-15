"""Soundpad preparation outside audio callbacks; bounded, phase-aligned windows."""
from __future__ import annotations
import math
import numpy as np
from scipy.signal import resample_poly

RATE = 48000


def pcm_view(path, channels=1):
    channels = int(channels)
    if channels not in (1, 2):
        raise ValueError('Klip kanal sayısı geçersiz.')
    raw = np.memmap(path, dtype='<f4', mode='r')
    if raw.size % channels:
        raw._mmap.close()
        raise ValueError('Klip PCM dosyası eksik veya bozuk.')
    return raw.reshape(-1, channels)


def prepare_window(pcm, entry, first, count, rate, channels, lane):
    """Render on one output sample grid, with filter context across windows.

    Keeping source starts on the rational resampler grid prevents seams between
    separately prepared windows. Memory is bounded by count, not clip duration.
    """
    rate, channels = int(rate), int(channels)
    total = int(math.ceil((entry['end'] - entry['start']) * rate / RATE))
    first, stop = max(0, int(first)), min(total, int(first) + int(count))
    if stop <= first:
        return first, np.empty((0, channels), np.float32)
    divisor = math.gcd(RATE, rate)
    up, down = rate // divisor, RATE // divisor
    guard = int(math.ceil(32 * max(1, down / up)))
    left = max(0, (int(first * down / up) - guard) // down * down)
    right = min(entry['end'] - entry['start'], int(math.ceil(stop * down / up)) + guard)
    raw = np.array(pcm[entry['start'] + left:entry['start'] + right], dtype=np.float32, copy=True)
    if lane == 'mic' or channels == 1:
        if raw.shape[1] > 1:
            raw = raw.mean(axis=1, keepdims=True, dtype=np.float32)
    elif raw.shape[1] == 1:
        raw = np.repeat(raw, channels, axis=1)
    if rate != RATE:
        raw = resample_poly(raw, up, down, axis=0, window=('kaiser', 8.6)).astype(np.float32, copy=False)
    offset = first - left * up // down
    values = np.ascontiguousarray(raw[offset:offset + stop - first])
    if len(values) != stop - first:
        raise ValueError('Klip örnekleme tamponu eksik.')
    positions = entry['start'] + np.arange(first, stop, dtype=np.float64) * RATE / rate
    from tools.rvc_audio_files import envelope
    gain = envelope(positions, entry['start'], entry['end'], entry['fade_in'] * RATE, entry['fade_out'] * RATE)
    values *= (gain * np.float32(10 ** (entry['gain_db'] / 20)))[:, None]
    values.flags.writeable = False
    return first, values


class PeakProtector:
    """Block peak attenuation, immediate attack and 100 ms release; no lookahead.

    This prevents hard clipping, but is not a mastering/true-peak limiter.
    """
    def __init__(self):
        self.gain = 1.0

    def reset(self):
        self.gain = 1.0

    def process(self, out, rate):
        peak = max(float(np.max(out, initial=0)), -float(np.min(out, initial=0)))
        target = min(1.0, .98 / peak) if peak > 0 else 1.0
        release = -math.expm1(-len(out) / (max(1, rate) * .1))
        self.gain = min(target, self.gain + (1.0 - self.gain) * release)
        if self.gain < .999999:
            out *= self.gain


def tensor_rms(audio, frame_length, hop_length):
    """librosa.feature.rms's default zero padding, on the audio tensor's device."""
    import torch
    import torch.nn.functional as F
    squared = audio.float().reshape(1, 1, -1).square()
    result = F.avg_pool1d(squared, int(frame_length), int(hop_length),
                          padding=int(frame_length) // 2, count_include_pad=True)
    return torch.sqrt(result[0].clamp_min_(0))
