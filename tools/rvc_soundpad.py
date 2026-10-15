"""Persistent sound library and independent mic/headphone playback lanes.

Decoding, disk reads, device changes and JSON writes happen outside callbacks.
Callbacks consume an immutable prefetch window and never wait for a UI lock.
"""
from __future__ import annotations
import copy
import json
import os
import queue
import re
import threading
import tempfile
import time
import uuid
from pathlib import Path
import numpy as np
import sounddevice as sd
from tools.rvc_audio_files import RATE, atomic_json, envelope, import_audio, export_clip
from tools.rvc_audio_quality import pcm_view, prepare_window, PeakProtector

ROOT = Path(__file__).resolve().parent.parent
ROUTES = {'mic': 'Yalnız mikrofon', 'head': 'Yalnız kulaklık', 'both': 'Mikrofon + kulaklık'}


class Soundpad:
    def __init__(self, owner, root=None):
        self.owner = owner
        self.root = Path(root) if root else ROOT
        self.path = self.root/'configs'/'soundpad.json'
        self.folder = self.root/'assets'/'soundpad'
        self.entries = []
        self.settings = {'master': 100, 'duck': False, 'duck_db': -12, 'selected': '', 'search': '', 'protect_peaks': True}
        self.voice = None
        self.lock = threading.Lock()
        self.jobs, self.messages = queue.Queue(), queue.Queue()
        self.cancel = threading.Event()
        self.closed = threading.Event()
        self.prefetch_event = threading.Event()
        self.formats = {'mic': (RATE, 2), 'head': (RATE, 2)}
        self.protectors = {'mic': PeakProtector(), 'head': PeakProtector()}
        self.device_cache = {}
        self.streams = {'mic': None, 'head': None}
        self.stream_keys = {}
        self.retry_at = {}
        self.status = 'Dosya ekle veya ses dosyalarını buraya sürükle.'
        self.importing = 0
        self.dirty = False
        self.error = ''
        self._device_transition = False
        self.alias_output = False
        self.duck_gain = 1.0
        self.editor_clipboard = None
        self._editor_workspace = None
        self._edit_workers = set()
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.settings.update({k:v for k,v in data.get('settings', {}).items() if k in self.settings})
            for original in data.get('entries', []):
                entry = self.valid_entry(original)
                if entry and not any(e['id'] == entry['id'] for e in self.entries):
                    self.entries.append(entry)
        except (OSError, ValueError, TypeError, KeyError):
            self.status = 'Soundpad hazır. Önceki kitaplık dosyası okunamadı; ses dosyaları korunuyor.'
        from tools.rvc_link_download import LinkDownloader
        self.downloader = LinkDownloader(self)
        self.worker = threading.Thread(target=self._jobs, name='Soundpad-files', daemon=True)
        self.prefetch = threading.Thread(target=self._prefetch, name='Soundpad-prefetch', daemon=True)
        self.worker.start()
        self.prefetch.start()

    @staticmethod
    def valid_entry(original):
        if not isinstance(original, dict) or not re.fullmatch('[0-9a-f]{32}', str(original.get('id', ''))):
            return None
        entry = {'name': 'Ses', 'source': '', 'url': '', 'frames': 0, 'peak': 0, 'start': 0, 'end': 0,
                 'gain_db': 0, 'fade_in': .005, 'fade_out': .01, 'route': 'both', 'loop': False,
                 'hotkey': '', 'keycode': None, 'cursor': 0, 'channels': 1, 'quality_version': 1,
                 'pcm_id': '', 'original_audio': None}
        entry.update({k: v for k, v in original.items() if k in entry or k == 'id'})
        entry['frames'] = max(0, int(entry['frames']))
        if entry['pcm_id'] and not re.fullmatch('[0-9a-f]{32}', str(entry['pcm_id'])):
            return None
        origin = entry['original_audio']
        if origin is not None:
            if not isinstance(origin, dict) or not re.fullmatch('[0-9a-f]{32}', str(origin.get('pcm_id', ''))):
                entry['original_audio'] = None
            else:
                cleaned = Soundpad.valid_entry(dict(Soundpad.audio_snapshot(origin), id=entry['id'], original_audio=None))
                entry['original_audio'] = Soundpad.audio_snapshot(cleaned) if cleaned and cleaned['frames']>0 else None
        entry['channels'] = 2 if int(entry['channels']) == 2 else 1
        entry['start'] = min(max(0, int(entry['start'])), max(0, entry['frames']-1))
        entry['end'] = min(entry['frames'], max(entry['start']+1, int(entry['end'])))
        entry['cursor'] = min(max(0, float(entry['cursor'])), (entry['end']-entry['start'])/RATE)
        entry['gain_db'] = min(18, max(-60, float(entry['gain_db'])))
        for key in ('fade_in', 'fade_out'):
            entry[key] = min(10, max(0, float(entry[key])))
        if entry['route'] not in ROUTES:
            entry['route'] = 'both'
        return entry

    def pcm_path(self, entry):
        preview = entry.get('_preview_path')
        if preview:
            path = Path(preview).resolve()
            if self._editor_workspace is None or not path.is_relative_to(Path(self._editor_workspace.name).resolve()):
                raise ValueError('Düzenleme önizlemesinin yolu geçersiz.')
            return path
        return self.folder/((entry.get('pcm_id') or entry['id'])+'.f32')

    @staticmethod
    def audio_snapshot(entry):
        fields = ('frames','peak','channels','quality_version','start','end','gain_db','fade_in','fade_out')
        result = {key:entry[key] for key in fields if key in entry}
        result['pcm_id'] = entry.get('pcm_id') or entry.get('id', '')
        return result

    def editor_root(self):
        if self._editor_workspace is None:
            self._editor_workspace = tempfile.TemporaryDirectory(prefix='rvc-soundpad-edit-',ignore_cleanup_errors=True)
        return Path(self._editor_workspace.name)

    def release_editor_cache(self):
        if self._edit_workers or self._editor_workspace is None:
            return
        keep = Path(self.editor_clipboard.args[0]) if self.editor_clipboard is not None else None
        paths = [path for path in Path(self._editor_workspace.name).glob('*.f32') if path != keep]
        if paths:
            self.jobs.put(('editor_cleanup', paths))

    def _used_audio_ids(self):
        used = set()
        for entry in self.entries:
            used.add(entry.get('pcm_id') or entry['id'])
            if entry.get('original_audio'):
                used.add(entry['original_audio']['pcm_id'])
        return used

    def accept_edit(self, identifier, audio_id, meta, values):
        """Commit a worker-rendered immutable copy; retain the first source copy."""
        if not re.fullmatch('[0-9a-f]{32}',audio_id):
            raise ValueError('Düzenlenmiş ses kimliği geçersiz.')
        path = self.folder/(audio_id+'.f32')
        if not path.is_file() or path.stat().st_size != meta['frames']*meta['channels']*4:
            raise ValueError('Düzenlenmiş ses dosyası eksik.')
        original = self.get(identifier)
        if not original:
            raise ValueError('Düzenlenen klip kitaplıkta bulunamadı.')
        previous = original.get('pcm_id') or original['id']
        origin = original.get('original_audio') or self.audio_snapshot(original)
        changes = dict(meta)
        changes.update(values)
        changes.update(pcm_id=audio_id,original_audio=origin,start=0,end=meta['frames'],cursor=0)
        self.update(identifier,**changes)
        if previous not in self._used_audio_ids():
            self.jobs.put(('remove',previous))

    def accept_new_clip(self, audio_id, meta, name, source, route='both'):
        path = self.folder/(audio_id+'.f32')
        if not re.fullmatch('[0-9a-f]{32}',audio_id) or not path.is_file() or path.stat().st_size != meta['frames']*meta['channels']*4:
            raise ValueError('Yeni klibin ses dosyası eksik.')
        entry = self.valid_entry(dict(meta,id=audio_id,name=name,source=source,start=0,end=meta['frames'],
                                     route=route,fade_in=0,fade_out=0))
        if entry is None or self.get(audio_id):
            raise ValueError('Yeni klip kimliği geçersiz.')
        atomic_json(self.path,{'version':1,'settings':self.settings,'entries':[*self.entries,entry]})
        self.entries.append(entry)
        return entry

    def get(self, identifier):
        return next((e for e in self.entries if e['id'] == identifier), None)

    def save(self):
        atomic_json(self.path, {'version': 1, 'settings': self.settings, 'entries': self.entries})
        self.dirty = False

    def import_files(self, paths):
        for path in paths:
            if Path(path).is_file():
                self.importing += 1
                self.jobs.put(('import', str(path)))

    def export(self, identifier, target):
        entry = self.get(identifier)
        if not entry:
            return
        if Path(target).resolve() == Path(entry['source']).resolve():
            raise ValueError('Orijinal dosyayı korumak için farklı bir dosya adı seç.')
        self.jobs.put(('export', (dict(copy.deepcopy(entry), protect_peaks=self.settings['protect_peaks']), str(target))))
        self.status = 'Düzenlenmiş WAV dışa aktarılıyor…'

    def _jobs(self):
        while not self.closed.is_set():
            try:
                job, value = self.jobs.get()
            except queue.Empty:
                continue
            if job == 'close':
                break
            if job == 'import':
                identifier = uuid.uuid4().hex
                path = self.folder/(identifier+'.f32')
                try:
                    self.messages.put(('status', 'İçe aktarılıyor: '+Path(value).name))
                    meta = import_audio(value, path, self.cancel)
                    entry = self.valid_entry(dict(meta, id=identifier, name=Path(value).stem,
                                                  source=value, start=0, end=meta['frames']))
                    self.messages.put(('imported', entry))
                except Exception as exc:
                    self.messages.put(('import_error', Path(value).name+': '+str(exc)))
            elif job == 'remove':
                for attempt in range(20):
                    try:
                        (self.folder/(value+'.f32')).unlink(missing_ok=True)
                        (self.folder/(value+'.peaks.npy')).unlink(missing_ok=True)
                        break
                    except OSError:
                        time.sleep(.05)
            elif job == 'editor_cleanup':
                for path in value:
                    try:
                        path.unlink(missing_ok=True)
                        path.with_suffix('.peaks.npy').unlink(missing_ok=True)
                    except OSError:
                        pass
            elif job == 'export':
                entry, target = value
                try:
                    export_clip(entry, self.pcm_path(entry), target, self.cancel)
                    self.messages.put(('status', 'WAV kaydedildi: '+target))
                except Exception as exc:
                    self.messages.put(('status', 'Dışa aktarma hatası: '+str(exc)))

    def poll(self):
        changed = False
        self.downloader.poll()
        for _ in range(40):
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'imported':
                self.entries.append(value)
                self.importing = max(0, self.importing-1)
                self.status = value['name']+' eklendi.'
                if not self.settings['selected']:
                    self.settings['selected'] = value['id']
                changed = self.dirty = True
            elif kind == 'select':
                self.settings['selected'] = value
                changed = self.dirty = True
            elif kind == 'import_error':
                self.importing = max(0, self.importing-1)
                self.status = 'Dosya eklenemedi: '+value
            else:
                self.status = value
        with self.lock:
            voice = self.voice
            if voice and not voice['paused'] and voice['ended'] >= set(voice['positions']):
                entry = self.get(voice['entry']['id'])
                if entry:
                    entry['cursor'] = 0
                self.voice = None
                self.prefetch_event.set()
                self.status = 'Oynatma tamamlandı.'
                self.dirty = True
        if self.error:
            self.status, self.error = self.error, ''
        self.sync_devices()
        if self.dirty:
            try:
                self.save()
            except OSError as exc:
                self.status = 'Soundpad ayarları kaydedilemedi: '+str(exc)
        return changed

    def bindings(self, candidate=None):
        bindings, codes, labels = {}, {}, {}
        for entry in self.entries:
            item = candidate if candidate and candidate['id'] == entry['id'] else entry
            action = 'soundpad:'+item['id']
            bindings[action] = item['hotkey']
            labels[action] = 'Soundpad · '+item['name']
            if item['keycode']:
                codes[action] = item['keycode']
        return bindings, codes, labels

    def update(self, identifier, **changes):
        original = self.get(identifier)
        if not original:
            return
        entry = self.valid_entry(dict(original, **changes))
        if not entry:
            raise ValueError('Geçersiz klip.')
        atomic_json(self.path, {'version': 1, 'settings': self.settings,
                               'entries': [entry if e is original else e for e in self.entries]})
        original.clear()
        original.update(entry)
        with self.lock:
            voice = self.voice
            if voice and voice['entry']['id'] == identifier:
                position = self._position(voice)
                position = min(entry['end']-1, max(entry['start'], position))
                voice['entry'] = copy.deepcopy(entry)
                voice['positions'] = {lane: position for lane in self._lanes(entry['route'])}
                voice['ended'].clear()
                voice['caches'] = {}
                voice['loop_caches'] = {}
                voice['revision'] += 1
        self.prefetch_event.set()
        self.dirty = False

    def remove(self, identifier):
        entry = self.get(identifier)
        audio_ids = {entry.get('pcm_id') or entry['id']} if entry else {identifier}
        if entry and entry.get('original_audio'):
            audio_ids.add(entry['original_audio']['pcm_id'])
        with self.lock:
            if self.voice and self.voice['entry']['id'] == identifier:
                self.voice = None
                self.prefetch_event.set()
        self.entries = [e for e in self.entries if e['id'] != identifier]
        if self.settings['selected'] == identifier:
            self.settings['selected'] = self.entries[0]['id'] if self.entries else ''
        # Only the managed copy is removed, after the prefetch worker releases it.
        for audio_id in audio_ids-self._used_audio_ids():
            self.jobs.put(('remove',audio_id))
        self.save()

    @staticmethod
    def _lanes(route):
        return ('mic', 'head') if route == 'both' else (route,)

    @staticmethod
    def _position(voice):
        return voice['positions'].get('mic', next(iter(voice['positions'].values())))

    def play(self, identifier, restart=False):
        entry = self.get(identifier)
        if not entry or not self.pcm_path(entry).is_file():
            raise RuntimeError('Klip dosyası bulunamadı. Ses dosyasını tekrar ekle.')
        with self.lock:
            if self.voice and self.voice['entry']['id'] == identifier and not restart:
                self.voice['paused'] = False
                self.prefetch_event.set()
                return
            self._remember_locked()
            start = entry['start'] if restart else min(entry['end']-1, entry['start']+entry['cursor']*RATE)
            self.voice = {'entry': copy.deepcopy(entry), 'paused': False, 'positions':
                          {lane: start for lane in self._lanes(entry['route'])}, 'ended': set(), 'caches': {}, 'loop_caches': {}, 'revision': 0}
        self.settings['selected'] = identifier
        self.messages.put(('select',identifier))
        self.status = entry['name']+' · '+ROUTES[entry['route']]
        self.dirty = True
        self.retry_at.clear()
        for protector in self.protectors.values():
            protector.reset()
        self.prefetch_event.set()

    def audition(self, entry):
        preview = entry.get('_preview_path')
        entry = self.valid_entry(entry)
        if entry is None:
            raise ValueError('Önizleme klibi geçersiz.')
        if preview:
            entry['_preview_path'] = preview
            self.pcm_path(entry)
        with self.lock:
            self._remember_locked()
            self.voice = {'entry': copy.deepcopy(entry), 'paused': False,
                          'positions': {'head': entry['start']}, 'ended': set(), 'caches': {}, 'loop_caches': {}, 'revision': 0, 'preview': True}
        self.retry_at.clear()
        self.prefetch_event.set()
        self.status = 'Düzenleme önizlemesi · yalnız kulaklık'

    def stop_preview(self):
        with self.lock:
            if self.voice and self.voice.get('preview'):
                self.voice = None
                self.prefetch_event.set()

    def toggle(self, identifier):
        with self.lock:
            same = self.voice and self.voice['entry']['id'] == identifier
            if same:
                self.voice['paused'] = not self.voice['paused']
                self._remember_locked()
        if not same:
            self.play(identifier)
        self.prefetch_event.set()

    def pause(self):
        with self.lock:
            if self.voice:
                self.voice['paused'] = not self.voice['paused']
                self._remember_locked()
                self.status = 'Duraklatıldı.' if self.voice['paused'] else 'Oynatma devam ediyor.'
        self.prefetch_event.set()

    def _remember_locked(self):
        if self.voice:
            entry = self.get(self.voice['entry']['id'])
            if entry and not self.voice.get('preview'):
                entry['cursor'] = (self._position(self.voice)-entry['start'])/RATE
                self.dirty = True

    def stop(self):
        with self.lock:
            if self.voice:
                entry = self.get(self.voice['entry']['id'])
                if entry:
                    entry['cursor'] = 0
            self.voice = None
        self.status = 'Soundpad durduruldu.'
        self.dirty = True
        self.prefetch_event.set()

    def seek(self, seconds):
        with self.lock:
            if self.voice:
                entry = self.voice['entry']
                pos = min(entry['end']-1, max(entry['start'], entry['start']+float(seconds)*RATE))
                self.voice['positions'] = {lane:pos for lane in self.voice['positions']}
                self.voice['ended'].clear()
                self.voice['caches'] = {}
                self.voice['revision'] += 1
                self._remember_locked()
        self.prefetch_event.set()

    def state(self):
        with self.lock:
            if not self.voice:
                return None
            voice = self.voice
            entry = voice['entry']
            return {'id': entry['id'], 'paused': voice['paused'], 'position':
                    (self._position(voice)-entry['start'])/RATE, 'duration': (entry['end']-entry['start'])/RATE}

    def _set_format(self, lane, rate, channels):
        value = (int(rate), int(channels))
        if self.formats.get(lane) != value:
            self.formats[lane] = value
            self.prefetch_event.set()

    def _prefetch(self):
        mapped, mapped_id = None, None
        try:
            while not self.closed.is_set():
                with self.lock:
                    voice = self.voice
                    active = voice is not None and not voice['paused']
                    requests = []
                    if active:
                        entry, revision = voice['entry'], voice['revision']
                        for lane, pos in voice['positions'].items():
                            if lane in voice['ended']:
                                continue
                            rate, channels = self.formats[lane]
                            key = (lane, rate, channels)
                            at = max(0, round((pos-entry['start'])*rate/RATE))
                            total = int(np.ceil((entry['end']-entry['start'])*rate/RATE))
                            cache = voice['caches'].get(key)
                            if cache is None or not (cache[0] <= at and cache[0]+len(cache[1]) >= min(total, at+rate*2)):
                                requests.append((key, max(0, at-rate//10), False))
                            if entry['loop'] and key not in voice['loop_caches']:
                                requests.append((key, 0, True))
                if not active and mapped is not None:
                    mapped._mmap.close()
                    mapped, mapped_id = None, None
                for key, first, loop_start in requests:
                    try:
                        identifier = (str(self.pcm_path(entry)), entry.get('channels', 1))
                        if identifier != mapped_id:
                            if mapped is not None:
                                mapped._mmap.close()
                            mapped = pcm_view(self.pcm_path(entry), entry.get('channels', 1))
                            mapped_id = identifier
                        if entry['end'] > len(mapped):
                            raise ValueError('Klip PCM dosyası eksik veya bozuk.')
                        lane, rate, channels = key
                        cache = prepare_window(mapped, entry, first, rate*6, rate, channels, lane)
                        with self.lock:
                            if self.voice is voice and voice['entry'] is entry and voice['revision'] == revision:
                                target = voice['loop_caches'] if loop_start else voice['caches']
                                target[key] = cache
                    except Exception as exc:
                        self.error = 'Soundpad: '+str(exc)
                        with self.lock:
                            if self.voice is voice:
                                voice['paused'] = True
                # Idle workers do not poll. Active playback needs only a 10 Hz
                # check: every prepared window includes two seconds of runway.
                self.prefetch_event.wait(.1 if active else None)
                self.prefetch_event.clear()
        finally:
            if mapped is not None:
                mapped._mmap.close()

    def render(self, out, lane, rate):
        """Mix prepared slices. Resampling, fades and clip gain are precomputed."""
        channels = out.shape[1] if out.ndim == 2 else 1
        rate = int(rate)
        if not self.lock.acquire(blocking=False):
            return False
        try:
            voice = self.voice
            if not voice or voice['paused'] or lane not in voice['positions'] or lane in voice['ended']:
                return False
            key = (lane, rate, channels)
            cache = voice['caches'].get(key)
            if cache is None:
                self._set_format(lane, rate, channels)
                return False
            entry = voice['entry']
            total = int(np.ceil((entry['end']-entry['start'])*rate/RATE))
            at = max(0, round((voice['positions'][lane]-entry['start'])*rate/RATE))
            if entry['loop']:
                at %= total
            wanted = len(out) if entry['loop'] else min(len(out), total-at)
            if wanted <= 0:
                voice['ended'].add(lane)
                return False
            muted = lane == 'mic' and bool(getattr(self.owner.studio_tools, 'muted', False))
            master = float(self.settings['master']) / 100
            done = 0
            # Very short loops use a bounded gather, avoiding a Python loop per
            # source sample even when the user selects a one-sample clip.
            beginning = voice['loop_caches'].get(key)
            if entry['loop'] and total < wanted and beginning and len(beginning[1]) >= total:
                samples = beginning[1][(np.arange(wanted, dtype=np.int64)+at) % total]
                if not muted:
                    out[:wanted] += (samples[:, 0] if out.ndim == 1 else samples) * master
                done = wanted
            else:
                while done < wanted:
                    current = (at+done) % total if entry['loop'] else at+done
                    start, values = cache
                    if not start <= current < start+len(values):
                        if beginning and beginning[0] <= current < beginning[0]+len(beginning[1]):
                            start, values = beginning
                        else:
                            break
                    count = min(wanted-done, total-current, start+len(values)-current)
                    samples = values[current-start:current-start+count]
                    if not muted:
                        target = out[done:done+count]
                        values_out = samples[:, 0] if out.ndim == 1 else samples
                        if master == 1:
                            np.add(target, values_out, out=target)
                        else:
                            target += values_out * master
                    done += count
            if done:
                next_frame = at+done
                if entry['loop']:
                    next_frame %= total
                elif next_frame >= total:
                    voice['ended'].add(lane)
                voice['positions'][lane] = min(entry['end'], entry['start']+next_frame*RATE/rate)
            return done > 0
        finally:
            self.lock.release()

    def protect_output(self, out, lane, rate):
        if self.settings['protect_peaks']:
            self.protectors[lane].process(out, rate)
        else:
            np.clip(out, -1, 1, out=out)

    def render_main(self,out,rate):
        voice = self.voice
        duck = (self.settings['duck'] and voice is not None and not voice['paused']
                and 'mic' in voice['positions'] and 'mic' not in voice['ended'])
        target = 10**(float(self.settings['duck_db'])/20) if duck else 1.0
        if target != self.duck_gain:
            ramp = min(len(out),max(1,int(rate*.01)))
            curve = np.full(len(out),target,np.float32)
            curve[:ramp] = np.linspace(self.duck_gain,target,ramp,dtype=np.float32)
            out *= curve[:,None] if out.ndim == 2 else curve
            self.duck_gain = target
        elif target != 1:
            out *= target
        if self.alias_output:
            voice = self.voice
            lane = 'mic' if voice and 'mic' in voice['positions'] else 'head'
            self.render(out,lane,rate)
            if self.lock.acquire(blocking=False):
                try:
                    if self.voice is voice and voice and 'mic' in voice['positions'] and 'head' in voice['positions']:
                        voice['positions']['head'] = voice['positions']['mic']
                        if 'mic' in voice['ended']:
                            voice['ended'].add('head')
                finally:
                    self.lock.release()
        else:
            self.render(out,'mic',rate)

    def _output_device(self, lane):
        if lane == 'head':
            monitor = self.owner.output_monitor
            return monitor.devices.get(monitor.selected)
        window = getattr(self.owner, 'window', None)
        label = window.widgets['sg_output_device'].get() if window else getattr(self.owner.gui_config, 'sg_output_device', '')
        outputs = getattr(self.owner, 'output_devices', []) or []
        indices = getattr(self.owner, 'output_devices_indices', []) or []
        selected = indices[outputs.index(label)] if label in outputs else sd.default.device[1]
        if isinstance(selected, (int,np.integer)):
            return int(selected)
        # RVC stores friendly names when PortAudio's device dict has no 'index'.
        # Resolve the name within the selected host API rather than using a
        # potentially ambiguous string across MME / DirectSound / WASAPI.
        host = window.widgets['sg_hostapi'].get() if window else getattr(self.owner.gui_config,'sg_hostapi','')
        cache_key = (str(selected), host)
        if cache_key in self.device_cache:
            return self.device_cache[cache_key]
        apis = sd.query_hostapis()
        matches = [(i,d) for i,d in enumerate(sd.query_devices()) if d['max_output_channels'] > 0 and d['name']==str(selected)]
        index = next((i for i,d in matches if apis[d['hostapi']]['name']==host), matches[0][0] if matches else None)
        self.device_cache[cache_key] = index
        return index

    def sync_devices(self):
        if self.closed.is_set() or self._device_transition:
            return
        with self.lock:
            voice = self.voice
            needed = set(voice['positions'])-voice['ended'] if voice and not voice['paused'] else set()
        main = getattr(self.owner, 'stream', None)
        main_active = main is not None and main.active
        if not needed:
            for lane in ('mic', 'head'):
                self._close_stream(lane)
            self.alias_output = False
            return
        mic_index = main.device[1] if main_active else self._output_device('mic')
        head_index = self._output_device('head')
        self.alias_output = mic_index == head_index and head_index is not None
        if main_active:
            rate = int(round(main.samplerate))
            channels = main.channels[1] if isinstance(main.channels, tuple) else main.channels
            self._set_format('mic', rate, channels)
            if self.alias_output:
                self._set_format('head', rate, channels)
        if self.alias_output and 'head' in needed and (main_active or 'mic' in needed):
            needed.discard('head')
            if not main_active:
                needed.add('mic')
        if main_active:
            needed.discard('mic')
        for lane in ('mic', 'head'):
            index = self._output_device(lane) if lane in needed else None
            if lane not in needed:
                self._close_stream(lane)
                continue
            if index is None or index < 0:
                self.error = 'Soundpad: '+('Kulaklık' if lane == 'head' else 'Mikrofon çıkış')+' cihazını seç.'
                continue
            if self.streams[lane] is not None and self.stream_keys.get(lane) == index:
                continue
            if time.monotonic() < self.retry_at.get(lane, 0):
                continue
            self._close_stream(lane)
            try:
                info = sd.query_devices(index)
                rate = int(info['default_samplerate'])
                channels = min(2, info['max_output_channels'])
                def callback(out, frames, times, status, target=lane, sample_rate=rate):
                    out.fill(0)
                    try:
                        self.render_main(out,sample_rate) if target=='mic' else self.render(out,target,sample_rate)
                        self.protect_output(out, target, sample_rate)
                    except Exception as exc:
                        out.fill(0)
                        self.error = 'Soundpad çıkışı: '+str(exc)
                        raise sd.CallbackAbort
                self._set_format(lane, rate, channels)
                self.protectors[lane].reset()
                stream = sd.OutputStream(device=index, channels=channels, samplerate=rate,
                                         dtype='float32', blocksize=0, latency='low', callback=callback)
                try:
                    stream.start()
                except Exception:
                    stream.close()
                    raise
                self.streams[lane], self.stream_keys[lane] = stream, index
            except Exception as exc:
                self.error = 'Soundpad '+ROUTES[lane]+' açılamadı: '+str(exc)
                self.retry_at[lane] = time.monotonic()+3

    def _close_stream(self, lane):
        stream, self.streams[lane] = self.streams[lane], None
        self.stream_keys.pop(lane, None)
        if stream:
            try:
                stream.abort()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    def before_main_start(self):
        self._close_stream('mic')
        rate = int(getattr(self.owner.gui_config, 'samplerate', RATE))
        channels = int(getattr(self.owner.gui_config, 'channels', 2))
        self._set_format('mic', rate, channels)

    def before_device_refresh(self):
        self._device_transition = True
        self.device_cache.clear()
        self._close_stream('mic')
        self._close_stream('head')

    def after_device_refresh(self):
        self._device_transition = False
        self.retry_at.clear()

    def close(self):
        for worker in tuple(self._edit_workers):
            worker.close()
        with self.lock:
            self._remember_locked()
            self.voice = None
        self.downloader.close()
        self.cancel.set()
        self.closed.set()
        self.jobs.put(('close', None))
        self.prefetch_event.set()
        for lane in self.streams:
            self._close_stream(lane)
        self.worker.join(timeout=2)
        self.prefetch.join(timeout=1)
        if self._editor_workspace is not None:
            self._editor_workspace.cleanup()
        # Commit a completed import even if the user closed before the next UI tick.
        while True:
            try:
                kind, value = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'imported':
                self.entries.append(value)
        self.save()
