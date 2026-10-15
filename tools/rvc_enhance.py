"""Optional offline speech restoration; importing this module loads no AI model."""
from __future__ import annotations
import collections
import json
import os
import queue
import subprocess
import sys
import threading
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODEL_REVISION = '4e3510ce4a8391159f665903544c5150bee7b2cb'
MODEL_HASH = 'f9d035f318de3e6d919bc70cf7ad7d32b4fe92ec5cbe0b30029a27f5db07d9d6'
MODEL_SIZE = 713176232
MODEL_NAME = 'mp_rank_00_model_states.pt'
QUALITY = {'Hızlı': 8, 'Dengeli': 32, 'Ayrıntılı': 64}


class SpeechRepair:
    def __init__(self, root=None):
        self.root = Path(root) if root else ROOT
        self.cache = self.root/'assets'/'enhance'/'model'
        self.worker = ROOT/'tools'/'rvc_enhance_worker.py'
        self._lock = threading.Lock()
        self._process = None

    def cache_status(self):
        model = self.cache/MODEL_NAME
        try:
            stamp = json.loads((self.cache/'verified.json').read_text(encoding='utf-8'))
            stat = model.stat()
            if (stat.st_size == MODEL_SIZE and stamp.get('sha256') == MODEL_HASH
                    and stamp.get('mtime_ns') == stat.st_mtime_ns):
                return 'Model indirildi · çevrimdışı kullanılabilir.'
        except (OSError, ValueError, TypeError):
            pass
        partial = model.with_suffix('.pt.part')
        try:
            size = partial.stat().st_size
            return f'İndirme yarım kaldı · {size/1048576:.0f} / 680 MB. Yeniden başlatınca devam eder.'
        except OSError:
            pass
        return 'İlk kullanımda yaklaşık 680 MB model indirilir.'

    def _job(self, request, cancel, progress):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError('Ses onarımı zaten çalışıyor.')
        jobs = self.cache/'jobs'
        request_path = jobs/(uuid.uuid4().hex+'.json')
        proc = None
        readers = []
        try:
            if cancel.is_set():
                raise InterruptedError('Ses onarımı iptal edildi.')
            jobs.mkdir(parents=True, exist_ok=True)
            request.update(cache=str(self.cache), assets=str(self.root/'assets'/'enhance'))
            request_path.write_text(json.dumps(request, ensure_ascii=False), encoding='utf-8')
            env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
            lines = queue.Queue()
            stderr = collections.deque(maxlen=20)
            proc = subprocess.Popen([sys.executable, '-u', str(self.worker), '--request', str(request_path)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, encoding='utf-8', errors='replace', env=env,
                                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            self._process = proc

            def read_stdout():
                for line in proc.stdout:
                    try:
                        event = json.loads(line)
                        if isinstance(event, dict):
                            lines.put(event)
                    except ValueError:
                        pass
            def read_stderr():
                for line in proc.stderr:
                    stderr.append(line.strip())
            readers = [threading.Thread(target=read_stdout, daemon=True),
                       threading.Thread(target=read_stderr, daemon=True)]
            for reader in readers:
                reader.start()
            result = failure = None
            while True:
                if cancel.is_set():
                    raise InterruptedError('Ses onarımı iptal edildi.')
                try:
                    event = lines.get(timeout=.05)
                except queue.Empty:
                    event = None
                if event:
                    kind = event.get('kind')
                    if kind == 'progress':
                        progress(event.get('text', 'Ses onarımı…'), int(event.get('percent', 0)))
                    elif kind == 'result':
                        result = event
                    elif kind == 'error':
                        failure = event
                if proc.poll() is not None:
                    for reader in readers:
                        reader.join(timeout=1)
                    while not lines.empty():
                        event = lines.get_nowait()
                        if event.get('kind') == 'result':
                            result = event
                        elif event.get('kind') == 'error':
                            failure = event
                    break
            if cancel.is_set():
                raise InterruptedError('Ses onarımı iptal edildi.')
            if failure or proc.returncode != 0 or not result:
                message = (failure or {}).get('message') or next((s for s in reversed(stderr) if s),
                                                                'Ses onarımı işlemi tamamlanamadı.')
                exc = RuntimeError(message)
                exc.is_oom = bool((failure or {}).get('oom'))
                raise exc
            return result
        finally:
            if proc:
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait(timeout=1)
                for reader in readers:
                    reader.join(timeout=1)
                for pipe in (proc.stdout, proc.stderr):
                    if pipe:
                        pipe.close()
            self._process = None
            request_path.unlink(missing_ok=True)
            self._lock.release()

    def prepare(self, cancel, progress):
        return self._job({'mode': 'prepare'}, cancel, progress)

    def restore_file(self, source, target, cancel, progress, quality='Dengeli', backend='Otomatik'):
        source, target = Path(source).resolve(), Path(target).resolve()
        if source == target or target.exists():
            raise ValueError('Onarım orijinal dosyanın üzerine yazamaz.')
        temp = target.parent/('.rvc-repair-'+uuid.uuid4().hex+'.wav')
        request = {'mode': 'restore', 'source': str(source), 'target': str(temp),
                   'quality': QUALITY.get(quality, 32), 'device': 'cpu' if backend == 'CPU' else 'auto'}
        try:
            try:
                result = self._job(request, cancel, progress)
            except RuntimeError as exc:
                if not getattr(exc, 'is_oom', False) or request['device'] == 'cpu':
                    raise
                progress('GPU belleği yetmedi · onarım CPU ile yeniden deneniyor…', 0)
                temp.unlink(missing_ok=True)
                request['device'] = 'cpu'
                result = self._job(request, cancel, progress)
            if cancel.is_set():
                raise InterruptedError('Ses onarımı iptal edildi.')
            if not temp.is_file():
                raise RuntimeError('Ses onarımı çıktı dosyası üretmedi.')
            os.replace(temp, target)
            return result
        finally:
            temp.unlink(missing_ok=True)
            # A cancelled subprocess cannot execute its cleanup handler.
            for extra in temp.parent.glob(temp.name+'.*'):
                extra.unlink(missing_ok=True)
