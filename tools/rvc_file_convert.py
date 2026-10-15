"""Cancellable file conversion using the application's real RVC DSP in bounded chunks."""
from __future__ import annotations
import copy
import gc
import json
import os
import queue
import threading
import uuid
from pathlib import Path
import numpy as np
from tools.rvc_audio_files import atomic_json, decode_blocks
from tools.rvc_enhance import SpeechRepair, QUALITY

ROOT = Path(__file__).resolve().parent.parent


class FileConverter:
    def __init__(self, owner, root=None):
        self.owner = owner
        self.root = Path(root) if root else ROOT
        self.path = self.root/'configs'/'file_conversion.json'
        self.settings = {'output': str(self.root/'recordings'/'converted'), 'rnnoise': False,
                         'enhance': False, 'enhance_quality': 'Dengeli'}
        self.items = []
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.settings.update({k:v for k,v in data.get('settings', {}).items() if k in self.settings})
            self.items = [dict(path=str(item['path']), state='Hazır', result=str(item.get('result', '')),
                               original=str(item.get('original', '')))
                          for item in data.get('items', []) if isinstance(item, dict) and 'path' in item]
        except (OSError, ValueError, TypeError):
            pass
        self.settings['enhance'] = self.settings['enhance'] is True
        if self.settings['enhance_quality'] not in QUALITY:
            self.settings['enhance_quality'] = 'Dengeli'
        self.repair = SpeechRepair(self.root)
        self.preparing = False
        self.busy = False
        self.cancel = threading.Event()
        self.messages = queue.Queue()
        self.thread = None
        self.status = 'Ses modelini seç, dosyaları ekle ve dönüştür.'
        self.progress = 0

    def save(self):
        atomic_json(self.path, {'settings': self.settings, 'items': self.items})

    def _repair_progress(self, text, percent):
        self.messages.put(('phase', (str(text), max(0, min(100, int(percent))))))

    def prepare_repair(self):
        if self.busy:
            raise RuntimeError('Önce devam eden işlemin bitmesini bekle.')
        self.cancel.clear()
        self.busy = self.preparing = True
        self.progress = 0
        self.status = 'Deneysel ses onarımı modeli hazırlanıyor…'
        def run():
            try:
                self.repair.prepare(self.cancel, self._repair_progress)
                self.messages.put(('prepared', 'Model indirildi · deneysel ses onarımı hazır.'))
            except InterruptedError:
                self.messages.put(('prepared', 'Model indirmesi iptal edildi. Tekrar başlatınca devam eder.'))
            except Exception as exc:
                self.messages.put(('prepared', 'Model hazırlanamadı: '+str(exc)))
        self.thread = threading.Thread(target=run, name='RVC-repair-prepare', daemon=True)
        self.thread.start()

    def add(self, paths):
        if self.busy:
            raise RuntimeError('Yeni dosyaları mevcut dönüşüm bitince ekleyebilirsin.')
        existing = {str(Path(item['path']).resolve()) for item in self.items}
        for path in paths:
            path = str(Path(path).resolve())
            if Path(path).is_file() and path not in existing:
                self.items.append({'path':path, 'state':'Hazır', 'result':'', 'original':''})
                existing.add(path)
        self.save()

    def start(self, values):
        if self.busy or self.owner.studio_tools.busy:
            raise RuntimeError('Önce devam eden ses dönüşümünün bitmesini bekle.')
        if not self.items:
            raise ValueError('Önce ses dosyası ekle.')
        if not Path(values.get('pth_path', '')).is_file():
            raise ValueError('Önce kullanılacak PTH ses modelini seç.')
        output = Path(self.settings['output'])
        output.mkdir(parents=True, exist_ok=True)
        self.save()
        self.cancel.clear()
        main = getattr(self.owner, 'stream', None)
        resume = main is not None and main.active
        self.owner.studio_tools.stop_play(restart_monitor=False)
        if resume:
            self.owner.stop_stream()
        self.busy = True
        self.progress = 0
        self.status = 'Ses modeli hazırlanıyor…'
        self.thread = threading.Thread(target=self._run, args=(copy.deepcopy(values), copy.deepcopy(self.settings), resume),
                                       name='RVC-file-conversion', daemon=True)
        self.thread.start()

    def _clone(self, settings):
        from tools.rvc_studio_tools import SETTINGS, NullWindow
        from tools.rvc_runtime_backend import apply_backend
        clone = object.__new__(type(self.owner))
        clone.config = copy.copy(self.owner.config)
        apply_backend(clone.config, settings.get('compute_backend', 'Otomatik'))
        clone.gui_config = type(self.owner.gui_config)()
        for key in SETTINGS:
            if key in settings and key not in ('pm','rmvpe','fcpe','sr_model','sr_device'):
                setattr(clone.gui_config, 'crossfade_time' if key == 'crossfade_length' else key, settings[key])
        clone.gui_config.f0method = next((k for k in ('pm','rmvpe','fcpe') if settings.get(k)), 'rmvpe')
        clone.gui_config.sr_type = 'sr_model'
        clone.get_device_samplerate = lambda: 48000
        clone.get_device_channels = lambda: 1
        # Offline DSP does not negotiate or open a hardware audio device.
        clone.choose_stream_samplerate = lambda preferred, channels: int(preferred)
        clone.start_stream = lambda: None
        clone.function = 'vc'
        clone.stream = None
        clone.window = NullWindow()
        type(self.owner)._studio_start_vc(clone)
        return clone

    def _convert_one(self, source, target, settings, options):
        import soundfile as sf
        from tools.rvc_noise import NoiseFilter
        clone = noise = None
        temp = target.parent/('.rvc-output-'+uuid.uuid4().hex+'.wav')
        decoded = target.parent/('.rvc-'+uuid.uuid4().hex+'.pcm')
        try:
            clone = self._clone(settings)
            rate, step = int(clone.gui_config.samplerate), int(clone.block_frame)
            frames = 0
            with decoded.open('wb') as raw:
                for block in decode_blocks(source, rate=rate, cancel=self.cancel):
                    block.astype('<f4', copy=False).tofile(raw)
                    frames += len(block)
            if not frames:
                raise ValueError('Dosyada kullanılabilir ses yok.')
            if options['rnnoise']:
                noise = NoiseFilter()
                noise.settings = dict(self.owner.noise_filter.settings, enabled=True)
                # Validate before writing a result; no silently unfiltered export.
                noise._reset_audio(rate)
            lead = int(clone.crossfade_frame+clone.sola_search_frame)
            if noise:
                lead += noise.latency_frames+int(round(.01*rate))
            total = frames+lead
            skipped, written = 0, 0
            with decoded.open('rb') as raw, sf.SoundFile(temp, 'w', samplerate=rate, channels=1,
                                                         subtype='PCM_16', format='WAV') as output:
                for position in range(0, total+step, step):
                    if self.cancel.is_set():
                        raise InterruptedError('Dönüşüm iptal edildi.')
                    chunk = np.zeros((step,1), np.float32)
                    block = np.fromfile(raw, dtype='<f4', count=step)
                    chunk[:len(block),0] = block
                    if noise:
                        chunk = noise.process(chunk, rate)
                        if noise.error:
                            raise RuntimeError(noise.error)
                    result = np.zeros_like(chunk)
                    type(self.owner)._studio_audio(clone, chunk, result, step, None, None)
                    if not np.isfinite(result).all():
                        raise RuntimeError('Ses modeli geçersiz örnek üretti.')
                    offset = min(step, max(0, lead-skipped))
                    skipped += step
                    count = min(step-offset, frames-written)
                    if count > 0:
                        output.write(np.clip(result[offset:offset+count,0], -1, 1))
                        written += count
                    self.messages.put(('file_progress', min(100, round(100*position/max(1,total)))))
                    if written == frames:
                        break
            # Target names are unique and input files are never overwritten.
            os.replace(temp, target)
        finally:
            if noise:
                noise.close()
            if clone:
                clone.__dict__.clear()
            temp.unlink(missing_ok=True)
            decoded.unlink(missing_ok=True)
            gc.collect()

    def _run(self, settings, options, resume):
        failures = warnings = 0
        try:
            folder = Path(options['output'])
            for index, item in enumerate(self.items):
                if self.cancel.is_set():
                    break
                source = Path(item['path'])
                target = folder/(source.stem+'_RVC.wav')
                suffix = 2
                while target.exists() or target.resolve() == source.resolve():
                    target = folder/(source.stem+f'_RVC_{suffix}.wav')
                    suffix += 1
                self.messages.put(('begin', index))
                converted = False
                try:
                    self._convert_one(source, target, settings, options)
                    converted = True
                    result, original, state = str(target), '', 'Tamamlandı'
                    if options.get('enhance', False):
                        original = str(target)
                        repaired = folder/(target.stem+'_Onarilmis.wav')
                        suffix = 2
                        while repaired.exists() or repaired.resolve() == source.resolve():
                            repaired = folder/(target.stem+f'_Onarilmis_{suffix}.wav')
                            suffix += 1
                        self._repair_progress('Deneysel ses onarımı başlıyor…', 0)
                        try:
                            self.repair.restore_file(target, repaired, self.cancel, self._repair_progress,
                                                     options.get('enhance_quality', 'Dengeli'),
                                                     settings.get('compute_backend', 'Otomatik'))
                            result, state = str(repaired), 'Tamamlandı · onarıldı'
                        except InterruptedError:
                            self.messages.put(('result', (index, result, 'Onarım iptal edildi · RVC kaydı hazır', original)))
                            break
                        except Exception as exc:
                            import traceback
                            traceback.print_exc()
                            warnings += 1
                            state = 'Onarım uygulanamadı · RVC kaydı hazır: '+str(exc)
                    self.messages.put(('result', (index, result, state, original)))
                except InterruptedError:
                    self.messages.put(('result', (index, str(target) if converted else '', 'İptal edildi',
                                                  str(target) if converted else '')))
                    break
                except Exception as exc:
                    import traceback
                    traceback.print_exc()
                    failures += 1
                    self.messages.put(('result', (index, '', 'Hata: '+str(exc), '')))
        finally:
            self.messages.put(('done', (resume, failures, warnings)))

    def poll(self):
        changed, resume = False, False
        for _ in range(50):
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'begin':
                self.items[value]['state'] = 'Dönüştürülüyor'
                self.status = f'{value+1}/{len(self.items)} · '+Path(self.items[value]['path']).name
                self.progress = 0
                changed = True
            elif kind == 'file_progress':
                self.progress = value
            elif kind == 'phase':
                self.status, self.progress = value
            elif kind == 'result':
                index, target, state, original = value
                self.items[index]['state'], self.items[index]['result'] = state, target
                self.items[index]['original'] = original
                changed = True
            elif kind == 'prepared':
                self.busy = self.preparing = False
                self.status = value
                self.progress = 100 if not self.cancel.is_set() and value.startswith('Model indirildi') else 0
                changed = True
            elif kind == 'done':
                self.busy = False
                self.status = 'Dönüşüm iptal edildi.' if self.cancel.is_set() else 'Dönüşüm kuyruğu tamamlandı.'
                resume, failures, warnings = value
                if not self.cancel.is_set() and (failures or warnings):
                    self.status = f'Dönüşüm tamamlandı · {failures} hata, {warnings} onarım uyarısı. Dosya satırlarını kontrol et.'
                if not self.cancel.is_set() and not failures:
                    self.progress = 100
                self.save()
                changed = True
        return changed, resume

    def close(self):
        self.cancel.set()
        if self.thread:
            self.thread.join(timeout=2)
        self.save()
