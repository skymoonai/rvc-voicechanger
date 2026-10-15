"""Chunked, local audio decoding. Neither FFmpeg.exe nor a cloud service is used."""
from __future__ import annotations
import json
import os
import uuid
from pathlib import Path
import numpy as np

RATE = 48000
AUDIO_FILTER = 'Ses dosyaları (*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.opus *.aiff *.wma);;Tüm dosyalar (*)'


def atomic_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def decode_blocks(path, rate=RATE, cancel=None, channels=1):
    """Mono by default; channels=None preserves up to two source channels."""
    import av
    with av.open(str(path)) as container:
        streams = container.streams.audio
        if not streams:
            raise ValueError('Bu dosyada ses akışı bulunamadı.')
        stream = streams[0]
        resampler = None
        for frame in container.decode(stream):
            if cancel is not None and cancel.is_set():
                raise InterruptedError('İşlem iptal edildi.')
            # Discontinuous timestamps must not insert gaps into an imported clip.
            frame.pts = None
            if resampler is None:
                count = min(2, frame.layout.nb_channels) if channels is None else int(channels)
                resampler = av.AudioResampler(format='fltp', layout='stereo' if count == 2 else 'mono', rate=int(rate))
            for converted in resampler.resample(frame):
                values = np.nan_to_num(converted.to_ndarray().T, copy=False).astype(np.float32, copy=False)
                yield values[:, 0] if channels == 1 else values
        if resampler is not None:
            for converted in resampler.resample(None):
                values = np.nan_to_num(converted.to_ndarray().T, copy=False).astype(np.float32, copy=False)
                yield values[:, 0] if channels == 1 else values


def import_audio(path, destination, cancel=None):
    """Canonical PCM and compact waveform peaks, built without loading a whole song."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_suffix('.part')
    peaks, frames, peak = [], 0, 0.0
    pending = None
    try:
        with temp.open('wb') as out:
            channels = 1
            for block in decode_blocks(path, cancel=cancel, channels=None):
                channels = block.shape[1]
                block.astype('<f4', copy=False).tofile(out)
                frames += len(block)
                peak = max(peak, float(np.max(np.abs(block), initial=0)))
                pending = block if pending is None or not len(pending) else np.concatenate((pending, block), axis=0)
                # 10 ms per bin; a one-hour clip uses about 2.9 MB for its waveform.
                count = len(pending) // 480
                if count:
                    bins = pending[:count*480].reshape(count, 480*channels)
                    peaks.append(np.stack((bins.min(axis=1), bins.max(axis=1)), axis=1))
                    pending = pending[count*480:]
        if not frames:
            raise ValueError('Ses dosyası boş.')
        if len(pending):
            peaks.append(np.array([[pending.min(), pending.max()]], np.float32))
        os.replace(temp, destination)
        peak_path = destination.with_suffix('.peaks.npy')
        np.save(peak_path, np.concatenate(peaks).astype(np.float32))
        return {'frames': frames, 'peak': peak, 'rate': RATE, 'channels': channels, 'quality_version': 2}
    except Exception:
        temp.unlink(missing_ok=True)
        destination.unlink(missing_ok=True)
        destination.with_suffix('.peaks.npy').unlink(missing_ok=True)
        raise


def envelope(positions, start, end, fade_in, fade_out):
    gain = np.ones(len(positions), np.float32)
    if fade_in > 0:
        gain *= np.clip((positions-start) / fade_in, 0, 1)
    if fade_out > 0:
        gain *= np.clip((end-1-positions) / fade_out, 0, 1)
    return gain


def export_clip(entry, pcm_path, target, cancel=None):
    import soundfile as sf
    from tools.rvc_audio_quality import pcm_view
    pcm = pcm_view(pcm_path, entry.get('channels', 1))
    start, end = int(entry['start']), int(entry['end'])
    target = Path(target)
    temp = target.parent/('.rvc-export-'+uuid.uuid4().hex+'.wav')
    try:
        clip_gain = 10 ** (float(entry['gain_db']) / 20)
        safety_gain = 1.0
        if entry.get('protect_peaks', True):
            peak = 0.0
            for pos in range(start, end, RATE):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError('Dışa aktarma iptal edildi.')
                stop = min(end, pos+RATE)
                fade = envelope(np.arange(pos, stop), start, end,
                                float(entry.get('fade_in', 0))*RATE, float(entry.get('fade_out', 0))*RATE)
                values = pcm[pos:stop] * (fade * clip_gain)[:, None]
                peak = max(peak, float(np.max(np.abs(values), initial=0)))
            safety_gain = min(1.0, .98/peak) if peak else 1.0
        with sf.SoundFile(temp, 'w', samplerate=RATE, channels=pcm.shape[1], subtype='PCM_24', format='WAV') as out:
            for pos in range(start, end, RATE):
                if cancel is not None and cancel.is_set():
                    raise InterruptedError('Dışa aktarma iptal edildi.')
                stop = min(end, pos+RATE)
                values = np.array(pcm[pos:stop], copy=True)
                values *= clip_gain * safety_gain
                values *= envelope(np.arange(pos, stop), start, end,
                                   float(entry.get('fade_in', 0))*RATE, float(entry.get('fade_out', 0))*RATE)[:, None]
                np.clip(values, -1, 1, out=values)
                out.write(values)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
        pcm._mmap.close()
