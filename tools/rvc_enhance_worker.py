"""Isolated, cancellable Resemble Enhance inference with bounded audio memory."""
from __future__ import annotations
import argparse
import gc
import hashlib
import json
import logging
import math
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools.rvc_enhance import MODEL_HASH, MODEL_NAME, MODEL_REVISION, MODEL_SIZE


def emit(kind, **values):
    print(json.dumps(dict(kind=kind, **values), ensure_ascii=False), flush=True)


def progress(text, percent=0):
    emit('progress', text=text, percent=int(max(0, min(100, percent))))


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024*1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_model(cache, opener=None):
    from filelock import FileLock
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    progress('Onarım modeli hazırlanıyor…')
    # OS locks are released even when a cancelled worker is terminated.
    with FileLock(str(cache/'download.lock'), timeout=120):
        return _ensure_model_locked(cache, opener)


def _ensure_model_locked(cache, opener=None):
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    model = cache/MODEL_NAME
    progress('Onarım modeli doğrulanıyor…')
    if model.is_file() and model.stat().st_size == MODEL_SIZE and sha256(model) == MODEL_HASH:
        pass
    else:
        partial = model.with_suffix('.pt.part')
        url = (f'https://huggingface.co/ResembleAI/resemble-enhance/resolve/{MODEL_REVISION}/'
               'enhancer_stage2/ds/G/default/'+MODEL_NAME+'?download=true')
        open_url = opener or urllib.request.urlopen
        last_error = None
        for attempt in range(3):
            offset = partial.stat().st_size if partial.exists() else 0
            if offset == MODEL_SIZE:
                break
            if offset > MODEL_SIZE:
                partial.unlink()
                offset = 0
            headers = {'User-Agent': 'RVC-Studio/1.2.1', 'Accept-Encoding': 'identity'}
            if offset:
                headers['Range'] = f'bytes={offset}-'
            try:
                with open_url(urllib.request.Request(url, headers=headers), timeout=20) as response:
                    status = getattr(response, 'status', 200)
                    content_range = response.headers.get('Content-Range', '')
                    if status == 206:
                        if not content_range.startswith(f'bytes {offset}-'):
                            raise RuntimeError('Model sunucusu beklenmeyen indirme aralığı gönderdi.')
                    elif status == 200:
                        offset = 0
                    else:
                        raise RuntimeError(f'Model sunucusu HTTP {status} yanıtı verdi.')
                    if 'text/html' in response.headers.get('Content-Type', '').lower():
                        raise RuntimeError('Model indirme bağlantısı ses modeli yerine web sayfası döndürdü.')
                    progress(f'Onarım modeli indiriliyor · {offset/1048576:.0f} / 680 MB', offset*100/MODEL_SIZE)
                    last_report = 0
                    with partial.open('ab' if offset else 'wb') as out:
                        while chunk := response.read(1024*1024):
                            out.write(chunk)
                            offset += len(chunk)
                            if offset > MODEL_SIZE:
                                raise RuntimeError('İndirilen model beklenen boyuttan büyük.')
                            now = time.monotonic()
                            if now-last_report > .2:
                                progress(f'Onarım modeli indiriliyor · {offset/1048576:.0f} / 680 MB', offset*100/MODEL_SIZE)
                                last_report = now
                if offset == MODEL_SIZE:
                    last_error = None
                    break
                last_error = RuntimeError('Model indirmesi yarım kaldı.')
            except (OSError, urllib.error.URLError) as exc:
                last_error = exc
                progress(f'Model indirmesi yeniden deneniyor · {attempt+1}/3…', offset*100/MODEL_SIZE)
        if last_error:
            raise RuntimeError('Onarım modeli indirilemedi. İnternet bağlantısını kontrol et. '+str(last_error))
        progress('İndirilen modelin bütünlüğü doğrulanıyor…', 99)
        if not partial.is_file() or partial.stat().st_size != MODEL_SIZE or sha256(partial) != MODEL_HASH:
            partial.unlink(missing_ok=True)
            raise RuntimeError('Onarım modelinin bütünlük kontrolü başarısız; bozuk model kullanılmadı.')
        os.replace(partial, model)
    stamp = {'sha256': MODEL_HASH, 'revision': MODEL_REVISION, 'mtime_ns': model.stat().st_mtime_ns}
    temp = cache/'verified.json.tmp'
    temp.write_text(json.dumps(stamp), encoding='utf-8')
    os.replace(temp, cache/'verified.json')
    return model


def load_restorer(model_path, assets, device):
    import torch
    from tools.resemble_runtime.enhancer.enhancer import Enhancer
    from tools.resemble_runtime.enhancer.hparams import HParams
    from torch.nn.utils.parametrize import remove_parametrizations
    params = json.loads((Path(assets)/'hparams.json').read_text(encoding='utf-8'))
    restorer = Enhancer(HParams(**params))
    # Only the immutable official checkpoint, SHA-256 verified by ensure_model,
    # is accepted here. DeepSpeed saves extra non-tensor metadata in this file.
    state = torch.load(model_path, map_location='cpu', weights_only=False)['module']
    restorer.load_state_dict(state, strict=True)
    del state
    restorer.eval().to(device)
    for module in restorer.modules():
        if hasattr(module, 'parametrizations') and 'weight' in module.parametrizations:
            remove_parametrizations(module, 'weight', leave_parametrized=True)
    return restorer


def restore_audio(source, target, restorer, device, nfe=32, reporter=progress):
    import numpy as np
    import soundfile as sf
    import torch
    import torch.nn.functional as F
    from tools.rvc_audio_files import decode_blocks
    rate = 44100
    source, target = Path(source), Path(target)
    if source.resolve() == target.resolve() or target.exists():
        raise ValueError('Onarım çıktı yolu orijinal ses dosyası olamaz.')
    raw = Path(str(target)+'.pcm')
    wet_file = Path(str(target)+'.float.wav')
    total = written = 0
    input_energy = wet_energy = peak = 0.0
    try:
        reporter('RVC çıktısı ses onarımına hazırlanıyor…', 0)
        with raw.open('wb') as out:
            for samples in decode_blocks(source, rate=rate):
                if not np.isfinite(samples).all():
                    raise RuntimeError('Onarılacak seste geçersiz örnek var.')
                samples.astype('<f4', copy=False).tofile(out)
                total += len(samples)
                input_energy += float(np.dot(samples.astype(np.float64), samples.astype(np.float64)))
        if not total:
            raise ValueError('Onarılacak ses dosyası boş.')
        # 3 s blocks and 250 ms overlap keep long files off RAM and GPU memory.
        chunk_size, overlap = 3*rate, rate//4
        hop = chunk_size-overlap
        restorer.configurate_(nfe=int(nfe), solver='midpoint', lambd=.1, tau=.5)
        held = np.empty(0, np.float32)

        def write_block(output, block):
            nonlocal written, wet_energy, peak
            if not len(block):
                return
            if not np.isfinite(block).all():
                raise RuntimeError('Ses onarımı geçersiz örnek üretti.')
            block = block[:total-written]
            output.write(block)
            written += len(block)
            wet_energy += float(np.dot(block.astype(np.float64), block.astype(np.float64)))
            peak = max(peak, float(np.max(np.abs(block), initial=0)))

        with raw.open('rb') as input_file, sf.SoundFile(wet_file, 'w', samplerate=rate,
                                                       channels=1, subtype='FLOAT') as out:
            with torch.inference_mode():
                for position in range(0, total, hop):
                    input_file.seek(position*4)
                    block = np.fromfile(input_file, dtype='<f4', count=min(chunk_size, total-position))
                    original_size = len(block)
                    reporter(f'Deneysel ses onarımı · {device.upper()} · {position/rate:.1f} / {total/rate:.1f} sn',
                             100*position/max(1, total))
                    abs_max = float(np.max(np.abs(block), initial=0))
                    if abs_max < 1e-6:
                        wet = block.copy()
                    else:
                        tensor = torch.from_numpy(block.copy()).to(device)
                        tensor = tensor/max(abs_max, 1e-7)
                        tensor = F.pad(tensor, (0, max(441, 2048-len(tensor))))
                        result = restorer(tensor[None])[0]
                        wet = (result[:original_size].cpu().numpy()*abs_max).astype(np.float32)
                        if len(wet) != original_size:
                            raise RuntimeError('Ses onarımı beklenenden kısa çıktı üretti.')
                        del tensor, result
                    blend = min(len(held), len(wet))
                    if blend:
                        fade = np.linspace(0, 1, blend, dtype=np.float32)
                        write_block(out, held[:blend]*(1-fade)+wet[:blend]*fade)
                    wet = wet[blend:]
                    last = position+chunk_size >= total
                    if last:
                        write_block(out, wet)
                        held = np.empty(0, np.float32)
                        break
                    tail = min(overlap, len(wet))
                    write_block(out, wet[:-tail] if tail else wet)
                    held = wet[-tail:].copy() if tail else np.empty(0, np.float32)
                if len(held):
                    write_block(out, held)
        if written != total:
            raise RuntimeError('Ses onarımı çıktı süresini koruyamadı.')
        # Match input RMS for fair A/B listening, then apply a peak ceiling.
        gain = min(4.0, max(.25, math.sqrt(input_energy/max(wet_energy, 1e-20)))) if input_energy else 1.0
        if peak:
            gain = min(gain, .99/peak)
        reporter('Onarılan ses kaydediliyor…', 99)
        with sf.SoundFile(wet_file) as wet, sf.SoundFile(target, 'w', samplerate=rate,
                                                       channels=1, subtype='PCM_16', format='WAV') as out:
            for block in wet.blocks(blocksize=65536, dtype='float32'):
                out.write(block*gain)
        reporter('Deneysel ses onarımı tamamlandı.', 100)
        return {'frames': total, 'rate': rate, 'device': device, 'gain': gain}
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        raw.unlink(missing_ok=True)
        wet_file.unlink(missing_ok=True)


def run(request):
    model_path = ensure_model(Path(request['cache']))
    if request['mode'] == 'prepare':
        emit('result', message='Onarım modeli hazır.')
        return
    import torch
    torch.set_num_threads(max(1, min(8, os.cpu_count() or 1)))
    device = 'cuda' if request.get('device') != 'cpu' and torch.cuda.is_available() else 'cpu'
    progress(f'Onarım modeli yükleniyor · {device.upper()}…', 0)
    restorer = load_restorer(model_path, Path(request['assets']), device)
    try:
        result = restore_audio(request['source'], request['target'], restorer, device, request.get('quality', 32))
        emit('result', **result)
    finally:
        del restorer
        gc.collect()
        if device == 'cuda':
            torch.cuda.empty_cache()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--request', required=True)
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    try:
        run(json.loads(Path(args.request).read_text(encoding='utf-8')))
    except Exception as exc:
        import traceback
        traceback.print_exc(file=sys.stderr)
        emit('error', message=str(exc), oom='out of memory' in str(exc).lower())
        raise SystemExit(1)
