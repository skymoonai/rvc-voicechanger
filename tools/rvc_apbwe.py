"""Persistent, optional realtime AP-BWE with background preparation and bypass."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import threading
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
MODEL_SIZE = 119097961
MODEL_HASH = '00950c7782a076e218f9c5a9bbc3b59a5beb0c4617b62ccea836df5f616a63dd'
MODEL_URL = ('https://huggingface.co/rsxdalv/AP-BWE/resolve/'
             'fa3f46d233cbc1d75cec9321188f86db627ba239/weights/24kto48k/g_24kto48k.zip')


def digest(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):
            result.update(block)
    return result.hexdigest()


def ensure_model(folder, cancel, progress):
    """A pinned public mirror; validate before torch.load, resume interrupted bytes."""
    folder = Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    target = folder/'g_24kto48k.pt'
    from filelock import FileLock, Timeout
    lock = FileLock(str(folder/'download.lock'))
    while True:
        if cancel.is_set():
            raise InterruptedError('Model hazırlığı iptal edildi.')
        try:
            lock.acquire(timeout=.2)
            break
        except Timeout:
            progress('Diğer model hazırlığı bekleniyor...')
    try:
        if target.is_file() and target.stat().st_size == MODEL_SIZE and digest(target) == MODEL_HASH:
            return target
        partial = folder/'g_24kto48k.pt.part'
        offset = partial.stat().st_size if partial.exists() else 0
        if offset >= MODEL_SIZE:
            if offset == MODEL_SIZE and digest(partial) == MODEL_HASH:
                os.replace(partial,target)
                return target
            partial.unlink()
            offset = 0
        headers = {'User-Agent':'RVC-Studio-APBWE/1.2.2'}
        if offset:
            headers['Range'] = f'bytes={offset}-'
        with urllib.request.urlopen(urllib.request.Request(MODEL_URL,headers=headers),timeout=20) as response:
            if offset and response.status != 206:
                offset = 0
            if offset and not response.headers.get('Content-Range','').startswith(f'bytes {offset}-'):
                raise RuntimeError('Model indirme devam bilgisi geçersiz.')
            with partial.open('ab' if offset else 'wb') as output:
                count = offset
                for data in iter(lambda:response.read(256*1024),b''):
                    if cancel.is_set():
                        raise InterruptedError('Model hazırlığı iptal edildi.')
                    count += len(data)
                    if count > MODEL_SIZE:
                        raise RuntimeError('Model indirme boyutu geçersiz.')
                    output.write(data)
                    progress(f'Model indiriliyor: %{count*100//MODEL_SIZE} · 114 MB')
        if partial.stat().st_size != MODEL_SIZE or digest(partial) != MODEL_HASH:
            partial.unlink(missing_ok=True)
            raise RuntimeError('AP-BWE modeli doğrulanamadı. Yeniden hazırlamayı dene.')
        os.replace(partial,target)
        return target
    finally:
        lock.release()


class RealtimeAPBWE:
    def __init__(self, owner=None, root=None):
        self.owner = owner
        self.root = Path(root) if root is not None else ROOT
        self.path = self.root/'configs'/'apbwe.json'
        self.settings = {'enabled':False,'strength':50}
        try:
            saved = json.loads(self.path.read_text(encoding='utf-8'))
            self.settings['enabled'] = saved.get('enabled') is True
            self.settings['strength'] = min(100,max(0,int(saved.get('strength',50))))
        except (OSError,ValueError,TypeError,OverflowError):
            pass
        self.runtime = None
        self.error = ''
        self.status = 'Kapalı. İlk hazırlıkta 114 MB model indirilir.'
        if self.settings['enabled']:
            self.status = 'Model bekliyor. Sesi başlat veya Modeli Hazırla’ya bas.'
        self.worker = None
        self.retired = []
        self.cancel = threading.Event()
        self.closed = False
        self.suspended = False
        self.last_ms = self.average_ms = 0.0
        self.overruns = 0
        self.generation = 0
        self._applied_generation = -1
        self._requested_device = 'cpu'

    @property
    def busy(self):
        return self.worker is not None and self.worker.is_alive()

    def preferred_device(self):
        import torch
        window = getattr(self.owner,'window',None)
        if window is not None and window._values().get('compute_backend') == 'CPU':
            return 'cpu'
        config = getattr(self.owner,'config',None)
        selected = str(getattr(config,'device','cpu'))
        return selected if selected.startswith('cuda') and torch.cuda.is_available() else 'cpu'

    def configure(self, *, enabled=None, strength=None):
        new = dict(self.settings)
        if enabled is not None:
            new['enabled'] = bool(enabled)
        if strength is not None:
            new['strength'] = min(100,max(0,int(strength)))
        self.path.parent.mkdir(parents=True,exist_ok=True)
        temporary = self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(new,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temporary,self.path)
        previous = self.settings
        self.settings = new
        if new['enabled'] != previous['enabled']:
            self.reset()
        if new['enabled'] and not previous['enabled']:
            self.prepare()

    def reset(self):
        # Audio callback owns its tensors; UI only requests a boundary reset.
        self.generation += 1
        self.suspended = False
        self.overruns = 0
        self.last_ms = self.average_ms = 0.0

    def prepare(self, device=None):
        device = device or self.preferred_device()
        self._requested_device = device
        if self.closed or self.busy:
            return
        if self.runtime is not None and getattr(self.runtime,'broken',False):
            self.runtime.close()
            self.runtime = None
        if self.runtime is not None and str(self.runtime.device) == device:
            self.error = ''
            self.reset()
            self.status = 'Model hazır · '+device.upper()
            return
        self.cancel.clear()
        self.error = ''
        self.status = 'Model hazırlanıyor; normal RVC sesi devam eder.'
        def work():
            try:
                path = ensure_model(self.root/'assets'/'apbwe'/'model',self.cancel,
                                    lambda message:setattr(self,'status',message))
                if self.cancel.is_set():
                    raise InterruptedError('Model hazırlığı iptal edildi.')
                self.status = 'Model yükleniyor ve ilk çıkarım hazırlanıyor...'
                from tools.rvc_apbwe_runtime import APBWERuntime
                from tools.rvc_apbwe_cpu import CPUAPBWERuntime
                try:
                    if device == 'cpu':
                        runtime = CPUAPBWERuntime(path,self.root/'assets'/'apbwe'/'config.json',cancel=self.cancel)
                    else:
                        runtime = APBWERuntime(path,self.root/'assets'/'apbwe'/'config.json',device)
                except Exception:
                    if not device.startswith('cuda'):
                        raise
                    self.status = 'GPU hazırlığı olmadı; CPU deneniyor...'
                    runtime = CPUAPBWERuntime(path,self.root/'assets'/'apbwe'/'config.json',cancel=self.cancel)
                if not self.closed and not self.cancel.is_set():
                    previous = self.runtime
                    self.runtime = runtime
                    if previous is not None and hasattr(previous,'close'):
                        self.retired.append(previous)
                    self.reset()
                    self.status = 'Model hazır · '+str(runtime.device).upper()
                elif hasattr(runtime,'close'):
                    runtime.close()
            except InterruptedError as exc:
                self.status = str(exc)+' Normal RVC sesi korunuyor.'
            except Exception as exc:
                self.error = str(exc)
                self.status = 'Hazırlık başarısız; normal RVC sesi korunuyor.'
        self.worker = threading.Thread(target=work,name='RVC-APBWE-Prepare',daemon=True)
        self.worker.start()

    def bind(self):
        self.reset()
        if self.settings['enabled']:
            self.prepare()

    def process(self, audio, rate, block_frames, *, deadline=None):
        runtime = self.runtime
        if self.generation != self._applied_generation:
            if runtime is not None:
                runtime.reset()
            self._applied_generation = self.generation
        if self.closed or not self.settings['enabled'] or self.settings['strength'] <= 0 or runtime is None or self.suspended:
            return audio
        # Avoid worsening an already late callback; there is never a job queue.
        if deadline is not None and self.average_ms > 0 and time.perf_counter()+self.average_ms*.0013 > deadline:
            self.overruns += 1
            if self.overruns >= 3:
                self.suspended = True
                self.status = 'İşlem bütçesi aşıldı; normal RVC kullanılıyor. Modeli Hazırla ile yeniden dene.'
            runtime.reset()
            return audio
        try:
            start = time.perf_counter()
            out = runtime.process(audio,int(rate),int(block_frames),self.settings['strength']/100,deadline=deadline)
            self.last_ms = (time.perf_counter()-start)*1000
            self.average_ms = self.last_ms if self.average_ms <= 0 else .8*self.average_ms+.2*self.last_ms
            late = self.last_ms > block_frames/rate*700 or (deadline is not None and time.perf_counter()>deadline)
            self.overruns = self.overruns+1 if late else 0
            if self.overruns >= 3:
                self.suspended = True
                self.status = 'AP-BWE yetişemedi; normal RVC kullanılıyor. Blok süresini artırıp yeniden dene.'
                return audio
            self.status = f'Canlı · {str(runtime.device).upper()} · son {self.last_ms:.1f} ms / ortalama {self.average_ms:.1f} ms'
            # RVC's SOLA modifies the returned tensor in place outside inference_mode.
            if getattr(out,'is_inference',lambda:False)():
                out = out.clone()
            return out
        except Exception as exc:
            self.suspended = True
            self.error = str(exc)
            self.status = 'AP-BWE durduruldu; normal RVC sesi devam ediyor.'
            return audio

    def close(self):
        self.closed = True
        self.cancel.set()
        runtime = self.runtime
        if runtime is not None and hasattr(runtime,'close'):
            runtime.close()
        for previous in self.retired:
            previous.close()
