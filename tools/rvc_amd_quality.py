"""Quality-first, evidence-based vc-rs parameters for RVC Studio AMD Edition.

vc-rs v0.5.3 defaults to 85ms crossfade, 100ms context and 500ms chunk.
The AMD Edition preview had hardcoded 200ms chunks with *those unchanged*
crossfade/context settings; high overlap and very short context can mask
fast consonants or sound unnaturally smoothed.

This module changes only the Rust CLI flags; it does not modify the user's
ONNX voice model or the original RVC Studio NVIDIA engine.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class QualityPreset:
    label: str
    chunk_ms: int
    crossfade_ms: int
    extra_convert_ms: int


PRESETS = {
    "netlik": QualityPreset("Net konuşma (önerilen)", 400, 40, 400),
    "dengeli": QualityPreset("Dengeli", 300, 40, 250),
    "dusuk_gecikme": QualityPreset("Düşük gecikme (deneysel)", 200, 30, 150),
}
DEFAULT_PRESET = "netlik"


def quality_settings(preset: str, chunk_ms: int | None = None) -> dict:
    if preset not in PRESETS:
        raise ValueError("Bilinmeyen AMD kalite profili: " + str(preset))
    config = PRESETS[preset]
    chunk = config.chunk_ms if chunk_ms is None else int(chunk_ms)
    if chunk < 50 or chunk > 1000 or chunk % 10 != 0:
        raise ValueError("Chunk 50–1000 ms aralığında 10'un katı olmalı.")
    # 85ms / 200ms (= 42.5%) was far too much *requested* blending for
    # low-latency use; keep consonant transients more local in time.
    crossfade = min(config.crossfade_ms, max(20, chunk // 5))
    return dict(chunk_ms=chunk,
                crossfade_ms=crossfade,
                extra_convert_ms=config.extra_convert_ms)


def detection_threshold(db: float) -> float:
    """Translate stock UI dB threshold, with 'minimum' meaning disabled.

    Unlike the original Python callback's per-100ms gating, vc-rs uses its
    threshold on each complete chunk; a raised threshold can hide low-energy
    consonants. Do not automatically gate audio at the stock -60 dB minimum.
    """
    value = float(db)
    if not -60 <= value <= 0:
        raise ValueError("Ses algılama eşiği -60...0 dBFS olmalı.")
    if value <= -60:
        return 0.0
    return 10 ** (value / 20.0)


def likely_quality_problems(metrics: dict, chunk_ms: int, denoiser: str,
                            threshold_db: float) -> list[str]:
    notes = []
    if denoiser == "rnnoise":
        notes.append("RNNoise bazı sessiz ünsüzleri bastırabilir; kapatarak karşılaştır.")
    if threshold_db > -45:
        notes.append("Yüksek sessizlik eşiği konuşmadaki yumuşak harfleri kesebilir.")
    processing = metrics.get("processing_ms")
    if processing is not None and processing >= chunk_ms * 0.9:
        notes.append("Rust ses çıkarımı chunk süresine yetişemiyor; GPU/CPU yükünü ve chunk süresini kontrol et.")
    for metric, label in (
        ("input_overruns", "Mikrofon tampon taşması"),
        ("output_underruns", "Çıkış tampon boşalması"),
        ("output_dropped_samples", "Ses örneği kaybı"),
    ):
        if (metrics.get(metric) or 0) > 0:
            notes.append(label + " algılandı; ses bozulabilir.")
    return notes
