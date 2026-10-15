"""Public Hugging Face catalog and atomic, cancellable RVC model installation."""
from __future__ import annotations
import hashlib
import json
import os
import queue
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

AUTHOR = 'ailabturkiye'
BASE = 'https://huggingface.co'
EXTENSIONS = {'.pth', '.index', '.zip', '.rar'}


class Cancelled(Exception):
    pass


def package_type(files):
    suffixes = {PurePosixPath(f['rfilename']).suffix.lower() for f in files}
    result = []
    if '.pth' in suffixes:
        result.append('PTH + INDEX' if '.index' in suffixes else 'PTH')
    result.extend(ext[1:].upper() for ext in ('.zip', '.rar') if ext in suffixes)
    return ' / '.join(result) or 'Dosya yok'


def safe_filename(name):
    path = PurePosixPath(name.replace('\\', '/'))
    if path.is_absolute() or '..' in path.parts or ':' in name:
        raise ValueError('Geçersiz paket yolu: ' + name)
    stem = re.sub(r'[^A-Za-z0-9_.-]', '_', path.stem).strip('._')[:80] or 'model'
    return stem + '_' + hashlib.sha256(name.encode()).hexdigest()[:10] + path.suffix.lower()


def matching_index(pth, indices):
    if len(indices) == 1:
        return indices[0]
    stem = re.sub(r'[^a-z0-9]', '', Path(pth).stem.lower().rsplit('_', 1)[0])
    matches = [p for p in indices if len(stem) >= 3 and stem in re.sub(r'[^a-z0-9]', '', Path(p).stem.lower())]
    return matches[0] if len(matches) == 1 else ''


class HubClient:
    def __init__(self, opener=urlopen):
        self.opener = opener

    def open(self, url):
        return self.opener(Request(url, headers={'User-Agent': 'RVC-Studio/3', 'Accept-Encoding': 'identity'}), timeout=20)

    def json(self, url):
        try:
            with self.open(url) as response:
                content = response.read(32 * 1024 * 1024 + 1)
                if len(content) > 32 * 1024 * 1024:
                    raise ValueError('Model listesi beklenenden büyük.')
                return json.loads(content), response.headers.get('Link', '')
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise ValueError('Bu modelin dosyaları herkese açık değil veya erişim onayı istiyor.') from exc
            raise ValueError(f'Hugging Face bağlantı hatası: HTTP {exc.code}') from exc

    def catalog(self, cancel):
        url = BASE + '/api/models?author=' + AUTHOR + '&limit=1000&full=true&sort=lastModified&direction=-1'
        rows = []
        seen = set()
        while url:
            if cancel.is_set():
                raise Cancelled()
            if url in seen or not url.startswith(BASE + '/api/models?'):
                raise ValueError('Geçersiz model listesi sayfalaması.')
            seen.add(url)
            batch, links = self.json(url)
            if not isinstance(batch, list):
                raise ValueError('Model listesi alınamadı.')
            rows.extend(row for row in batch if row.get('id', '').startswith(AUTHOR + '/'))
            next_page = re.search(r'<([^>]+)>;\s*rel="next"', links)
            url = next_page.group(1) if next_page else ''
        unique = {row['id']: row for row in rows}
        return list(unique.values())

    def details(self, repo):
        if not repo.startswith(AUTHOR + '/'):
            raise ValueError('Geçersiz model kaynağı.')
        data, _ = self.json(BASE + '/api/models/' + quote(repo, safe='/') + '?blobs=true')
        files = [f for f in data.get('siblings', []) if PurePosixPath(f['rfilename']).suffix.lower() in EXTENSIONS]
        return dict(data, files=files)

    def download(self, repo, revision, asset, target, cancel, progress):
        url = BASE + '/' + quote(repo, safe='/') + '/resolve/' + quote(revision, safe='') + '/' + quote(asset['rfilename'], safe='/') + '?download=true'
        with self.open(url) as response, target.open('wb') as output:
            total = int(response.headers.get('Content-Length', 0) or 0)
            declared = int(asset.get('size', asset.get('lfs', {}).get('size', 0)) or 0)
            done = 0
            checksum = hashlib.sha256()
            last = 0.
            while True:
                if cancel.is_set():
                    raise Cancelled()
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                checksum.update(chunk)
                done += len(chunk)
                if time.monotonic() - last > .15:
                    progress(done, total or declared)
                    last = time.monotonic()
            if (total and done != total) or (declared and done != declared):
                raise ValueError('İndirme eksik: ' + asset['rfilename'])
            expected_hash = asset.get('lfs', {}).get('sha256', '')
            if expected_hash and checksum.hexdigest() != expected_hash:
                raise ValueError('İndirilen dosyanın bütünlük kontrolü başarısız: ' + asset['rfilename'])
            progress(done, total or declared)


def archive_tool():
    for executable in ('7z', '7z.exe', 'unrar', 'unrar.exe'):
        found = shutil.which(executable)
        if found:
            return found
    for variable in ('ProgramFiles', 'ProgramFiles(x86)'):
        candidate = Path(os.environ.get(variable, 'C:/Program Files')) / '7-Zip' / '7z.exe'
        if candidate.is_file():
            return str(candidate)
        candidate = Path(os.environ.get(variable, 'C:/Program Files')) / 'WinRAR' / 'UnRAR.exe'
        if candidate.is_file():
            return str(candidate)
    for executable in ('bsdtar', 'tar.exe'):
        found = shutil.which(executable)
        if found:
            return found
    candidate = Path(os.environ.get('SystemRoot', 'C:/Windows')) / 'System32' / 'tar.exe'
    if candidate.is_file():
        return str(candidate)
    raise ValueError('RAR açmak için Windows tar.exe veya 7-Zip gerekli. 7-Zip kurup indirmeyi yeniden deneyebilirsin.')


def extract_rar_entry(archive, member, target, size, cancel):
    tool = archive_tool()
    if Path(tool).name.lower() in ('7z', '7z.exe'):
        command = [tool, 'x', '-so', '-y', '-spd', str(archive), member]
    elif Path(tool).name.lower() in ('unrar', 'unrar.exe'):
        command = [tool, 'p', '-inul', '-p-', '--', str(archive), member]
    else:
        command = [tool, '-xOf', str(archive), '--', member]
    # Never let archive paths become filesystem destinations. Extract selected entries to stdout.
    with target.open('wb') as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=output, stderr=errors, shell=False,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            while process.poll() is None:
                if cancel.wait(.1):
                    raise Cancelled()
                if target.stat().st_size > max(size, 1) + 1024 * 1024:
                    raise ValueError('Paket içindeki dosya beklenenden büyük.')
            if process.returncode:
                errors.seek(0)
                detail = errors.read(2000).decode('utf-8', errors='replace')
                raise ValueError('RAR açılamadı. Şifreli/çok parçalı veya bu çıkarıcıda desteklenmeyen paket olabilir. ' + detail)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
    if target.stat().st_size != size:
        raise ValueError('RAR içindeki model eksik çıkarıldı: ' + member)


def unpack_models(archive, destination, cancel, progress):
    destination.mkdir(parents=True, exist_ok=True)
    found = []
    if zipfile.is_zipfile(archive):
        reader = zipfile.ZipFile(archive)
        entries = reader.infolist()
        kind = 'ZIP'
    else:
        import rarfile
        if not rarfile.is_rarfile(archive):
            raise ValueError('Paket ZIP veya RAR olarak okunamıyor: ' + archive.name)
        reader = rarfile.RarFile(archive)
        entries = reader.infolist()
        kind = 'RAR'
    with reader:
        if len(entries) > 100000:
            raise ValueError('Paket çok fazla dosya içeriyor.')
        for entry in entries:
            if cancel.is_set():
                raise Cancelled()
            name = entry.filename
            suffix = PurePosixPath(name.replace('\\', '/')).suffix.lower()
            directory = entry.is_dir() if kind == 'ZIP' else entry.isdir()
            if suffix not in ('.pth', '.index') or directory:
                continue
            if kind == 'ZIP' and stat.S_ISLNK(entry.external_attr >> 16):
                continue
            if kind == 'RAR' and (entry.is_symlink() or entry.needs_password()):
                raise ValueError('Bu RAR dosyası şifreli veya geçersiz model girdileri içeriyor.')
            if entry.file_size > 4 * 1024 ** 3:
                raise ValueError('Paket içindeki model dosyası 4 GB sınırını aşıyor.')
            target = destination / safe_filename(name)
            if target.exists():
                raise ValueError('Paket aynı dosya adını birden fazla kez içeriyor: ' + name)
            progress('Paket açılıyor: ' + name)
            if kind == 'ZIP':
                with reader.open(entry) as source, target.open('wb') as output:
                    while True:
                        if cancel.is_set():
                            raise Cancelled()
                        chunk = source.read(256 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
            else:
                extract_rar_entry(archive, name, target, entry.file_size, cancel)
            if target.stat().st_size != entry.file_size:
                raise ValueError('Paket içindeki model eksik çıkarıldı.')
            found.append(target)
    return found


class ModelDatabase:
    def __init__(self, root=None, client=None):
        self.root = Path(root or Path(__file__).resolve().parent.parent)
        self.client = client or HubClient()
        self.messages = queue.Queue(maxsize=256)
        self.message_lock = threading.Lock()
        self.catalog_cancel = threading.Event()
        self.cancel = threading.Event()
        self.busy = False
        self.loading = False
        self.rows = []
        self.detail_generation = 0
        self.worker = None
        self.closed = False

    def post(self, kind, **data):
        if not self.closed:
            with self.message_lock:
                try:
                    self.messages.put_nowait((kind, data))
                except queue.Full:
                    try:
                        self.messages.get_nowait()
                    except queue.Empty:
                        pass
                    self.messages.put_nowait((kind, data))

    def refresh(self):
        if self.loading:
            return
        self.loading = True
        def run():
            cache = self.root / 'configs' / 'voice_database_cache.json'
            try:
                rows = self.client.catalog(self.catalog_cancel)
                if self.catalog_cancel.is_set():
                    return
                self.rows = rows
                cache.parent.mkdir(parents=True, exist_ok=True)
                temp = cache.with_suffix('.json.tmp')
                temp.write_text(json.dumps(rows, ensure_ascii=False), encoding='utf-8')
                os.replace(temp, cache)
                self.post('catalog', rows=rows, cached=False)
            except Cancelled:
                pass
            except Exception as exc:
                try:
                    rows = json.loads(cache.read_text(encoding='utf-8'))
                    if not isinstance(rows, list):
                        raise ValueError()
                    self.rows = rows
                    self.post('catalog', rows=rows, cached=True, error=str(exc))
                except Exception:
                    self.post('error', text='Model listesi alınamadı: ' + str(exc))
            finally:
                self.loading = False
        threading.Thread(target=run, name='RVC-model-catalog', daemon=True).start()

    def details(self, repo):
        self.detail_generation += 1
        generation = self.detail_generation
        def run():
            try:
                data = self.client.details(repo)
                if generation == self.detail_generation:
                    self.post('details', repo=repo, data=data)
            except Exception as exc:
                if generation == self.detail_generation:
                    self.post('detail_error', repo=repo, text=str(exc))
        threading.Thread(target=run, name='RVC-model-files', daemon=True).start()

    def download(self, detail, selected):
        if self.busy:
            raise ValueError('Bir model indiriliyor; önce tamamlanmasını bekle veya iptal et.')
        assets = [f for f in detail.get('files', []) if f['rfilename'] in selected]
        if not assets:
            raise ValueError('İndirilecek PTH/INDEX/ZIP/RAR dosyalarını seç.')
        revision = detail.get('sha', '')
        if not re.fullmatch(r'[a-fA-F0-9]{7,64}', revision):
            raise ValueError('Model sürümü doğrulanamadı; listeyi yenile.')
        self.busy = True
        self.cancel.clear()
        self.worker = threading.Thread(target=self._download, args=(detail['id'], revision, assets), name='RVC-model-download', daemon=True)
        self.worker.start()

    def _download(self, repo, revision, assets):
        stage = None
        try:
            parent = self.root / 'models' / 'downloaded'
            parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256((repo + revision + json.dumps(sorted(f['rfilename'] for f in assets))).encode()).hexdigest()[:16]
            name = re.sub(r'[^A-Za-z0-9_.-]', '_', repo.split('/')[-1])[:64]
            final = parent / (name + '_' + digest)
            manifest = final / 'rvc_manifest.json'
            if manifest.is_file():
                saved = json.loads(manifest.read_text(encoding='utf-8'))
                files = [final / p for p in saved['files']]
                if all(p.is_file() for p in files):
                    self.post('download_done', files=[str(p) for p in files], repo=repo, cached=True)
                    return
            stage = Path(tempfile.mkdtemp(prefix='.download_', dir=parent))
            models = stage / 'models'
            models.mkdir()
            results = []
            for number, asset in enumerate(assets, 1):
                if self.cancel.is_set():
                    raise Cancelled()
                asset_name = asset['rfilename']
                target = stage / safe_filename(asset_name)
                def progress(done, total):
                    self.post('progress', text=f'{number}/{len(assets)} · {asset_name}', done=done, total=total)
                self.client.download(repo, revision, asset, target, self.cancel, progress)
                suffix = target.suffix.lower()
                if suffix in ('.zip', '.rar'):
                    destination = models / hashlib.sha256(asset_name.encode()).hexdigest()[:10]
                    results.extend(unpack_models(target, destination, self.cancel,
                                                 lambda text: self.post('progress', text=text, done=0, total=0)))
                    target.unlink()
                else:
                    destination = models / target.name
                    target.rename(destination)
                    results.append(destination)
            if not any(p.suffix.lower() == '.pth' for p in results):
                raise ValueError('Seçilen dosyalarda kullanılabilir .pth modeli bulunamadı.')
            if self.cancel.is_set():
                raise Cancelled()
            relative = [str(p.relative_to(stage)) for p in results]
            (stage / 'rvc_manifest.json').write_text(json.dumps({'repo': repo, 'revision': revision, 'files': relative}, ensure_ascii=False), encoding='utf-8')
            # Existing successful imports are never overwritten by another download.
            if final.exists():
                final = parent / (final.name + '_' + str(time.time_ns()))
            stage.rename(final)
            stage = None
            self.post('download_done', files=[str(final / p) for p in relative], repo=repo, cached=False)
        except Cancelled:
            self.post('download_cancelled')
        except Exception as exc:
            self.post('download_error', text=str(exc))
        finally:
            if stage is not None:
                shutil.rmtree(stage, ignore_errors=True)
            self.busy = False

    def close(self):
        self.cancel.set()
        self.catalog_cancel.set()
        self.closed = True