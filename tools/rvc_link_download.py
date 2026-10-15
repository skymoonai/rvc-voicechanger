"""yt-dlp audio import. Spotify resolves public metadata to selectable YouTube matches."""
from __future__ import annotations
import json
import os
import queue
import re
import shutil
import threading
import uuid
from pathlib import Path
from urllib.parse import urlparse
from tools.rvc_audio_files import import_audio


def classify_url(value):
    value = value.strip()
    if re.fullmatch(r'spotify:track:[A-Za-z0-9]+',value):
        value = 'https://open.spotify.com/track/'+value.rsplit(':',1)[1]
    if not re.match(r'^[a-zA-Z]+://',value):
        value = 'https://'+value
    parsed = urlparse(value)
    host = (parsed.hostname or '').lower().rstrip('.')
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None,443):
        raise ValueError('Geçerli bir HTTPS YouTube, SoundCloud veya Spotify bağlantısı yapıştır.')
    for name,domains in {'youtube':('youtube.com','youtu.be'), 'soundcloud':('soundcloud.com',),
                         'spotify':('open.spotify.com','spotify.link')}.items():
        if any(host == d or host.endswith('.'+d) for d in domains):
            return name,value
    raise ValueError('YouTube, SoundCloud veya Spotify bağlantısı kullan.')


class LinkDownloader:
    def __init__(self,pad):
        self.pad = pad
        self.path = pad.root/'configs'/'soundpad_links.json'
        self.url = ''
        try:
            self.url = str(json.loads(self.path.read_text(encoding='utf-8')).get('url',''))
        except (OSError,ValueError,TypeError):
            pass
        self.busy = False
        self.status = 'YouTube / SoundCloud bağlantısı veya Spotify parça bağlantısı yapıştır.'
        self.progress = 0
        self.results = []
        self.messages = queue.Queue()
        self.cancel = threading.Event()
        self.thread = None

    def _options(self,folder=None):
        def hook(data):
            if self.cancel.is_set():
                raise InterruptedError('İndirme iptal edildi.')
            if data.get('status')=='downloading':
                total = data.get('total_bytes') or data.get('total_bytes_estimate') or 0
                downloaded = data.get('downloaded_bytes',0)
                self.messages.put(('progress',min(99,round(downloaded/total*100)) if total else 0))
        service = self
        class Logger:
            def debug(self,message):
                pass
            def warning(self,message):
                service.messages.put(('status',str(message)))
            def error(self,message):
                pass
        # One native audio stream: PyAV decodes it, without an FFmpeg subprocess.
        options = {'format':'bestaudio[protocol=https]/bestaudio[protocol=http]/bestaudio/best','fixup':'never','noplaylist':True,'quiet':True,'no_warnings':False,
                   'socket_timeout':12,'retries':2,'fragment_retries':2,'concurrent_fragment_downloads':1,
                   'progress_hooks':[hook],'logger':Logger(),'cachedir':str(self.pad.root/'configs'/'yt_dlp_cache'),
                   'windowsfilenames':True,'overwrites':False,'restrictfilenames':True,
                   'compat_opts':{'no-certifi'}}
        deno = self.pad.root/'assets'/'downloader'/('deno.exe' if os.name=='nt' else 'deno')
        if deno.is_file():
            options['js_runtimes'] = {'deno':{'path':str(deno)}}
        if folder:
            options['outtmpl'] = str(folder/'source.%(ext)s')
        return options

    def _spotify_query(self,url):
        import requests
        session = requests.Session()
        if urlparse(url).hostname == 'spotify.link':
            response = session.get(url,timeout=(8,20),allow_redirects=True)
            response.raise_for_status()
            platform,url = classify_url(response.url)
            if platform!='spotify' or urlparse(url).hostname!='open.spotify.com':
                raise ValueError('Spotify kısa bağlantısı çözülemedi; open.spotify.com parça bağlantısı kullan.')
        match = re.search(r'/(?:intl-[a-z-]+/)?track/([A-Za-z0-9]+)',urlparse(url).path)
        if not match:
            raise ValueError('Spotify için tek bir parça bağlantısı kullan. Albüm ve çalma listesi bağlantıları bu sürümde desteklenmiyor.')
        identifier = match.group(1)
        canonical = 'https://open.spotify.com/track/'+identifier
        response = session.get('https://open.spotify.com/oembed',params={'url':canonical},timeout=(8,20))
        response.raise_for_status()
        title = str(response.json().get('title','')).strip()
        if not title:
            raise ValueError('Spotify parça adı okunamadı.')
        artist = ''
        # Public embed metadata supplies the artist when available; no account or keys.
        try:
            embed = session.get('https://open.spotify.com/embed/track/'+identifier,timeout=(8,15))
            embed.raise_for_status()
            data = re.search(r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',embed.text,re.S)
            if data:
                entity = json.loads(data.group(1)).get('props',{}).get('pageProps',{}).get('state',{}).get('data',{}).get('entity',{})
                title = str(entity.get('title') or title)
                artist = str(entity.get('subtitle') or '')
                if not artist:
                    artist = ', '.join(str(a.get('name','')) for a in entity.get('artists',[]) if isinstance(a,dict))
        except (requests.RequestException,ValueError,KeyError,TypeError):
            pass
        return (artist+' '+title).strip()

    def inspect(self,url):
        self._start('inspect',url)

    def download(self,url):
        platform,url = classify_url(url)
        if platform=='spotify':
            self.inspect(url)
        else:
            self._start('download',url)

    def _start(self,job,url):
        from tools.rvc_audio_files import atomic_json
        if self.busy:
            raise RuntimeError('Önce mevcut indirme işlemini bitir veya iptal et.')
        platform,url = classify_url(url)
        self.url = url
        atomic_json(self.path,{'url':url})
        self.busy = True
        self.cancel.clear()
        self.progress = 0
        self.status = 'Bağlantı inceleniyor…' if job=='inspect' else 'Ses indiriliyor…'
        self.thread = threading.Thread(target=self._run,args=(job,platform,url),name='Soundpad-link-import',daemon=True)
        self.thread.start()

    def _run(self,job,platform,url):
        folder = self.pad.root/'recordings'/'link_temp'/uuid.uuid4().hex
        identifier = None
        try:
            import yt_dlp
            if self.cancel.is_set():
                raise InterruptedError('İndirme iptal edildi.')
            if job=='inspect':
                options = self._options()
                if platform=='spotify':
                    query = self._spotify_query(url)
                    self.messages.put(('status','Spotify: '+query+' · YouTube eşleşmeleri aranıyor…'))
                    options['extract_flat'] = True
                    options['noplaylist'] = False
                    with yt_dlp.YoutubeDL(options) as downloader:
                        info = downloader.extract_info('ytsearch5:'+query,download=False)
                    entries = list(info.get('entries') or [])
                    source = 'YouTube eşleşmesi'
                else:
                    with yt_dlp.YoutubeDL(options) as downloader:
                        info = downloader.extract_info(url,download=False)
                    if info.get('_type') in ('playlist','multi_video'):
                        raise ValueError('Tek bir video veya ses parçasının bağlantısını kullan.')
                    entries,source = [info], 'YouTube' if platform=='youtube' else 'SoundCloud'
                results = []
                for entry in entries:
                    if entry:
                        link = entry.get('webpage_url') or entry.get('url')
                        if source=='YouTube eşleşmesi':
                            link = 'https://www.youtube.com/watch?v='+entry['id']
                        results.append({'title':entry.get('title','Ses'),'url':link or url,'duration':entry.get('duration') or 0,
                                        'channel':entry.get('channel') or entry.get('uploader') or '', 'source':source})
                if not results:
                    raise ValueError('İndirilebilir eşleşme bulunamadı.')
                self.messages.put(('results',results))
            else:
                folder.mkdir(parents=True,exist_ok=True)
                with yt_dlp.YoutubeDL(self._options(folder)) as downloader:
                    info = downloader.extract_info(url,download=True)
                    if info.get('_type') in ('playlist','multi_video'):
                        raise ValueError('Tek bir video veya ses parçasının bağlantısını kullan.')
                    files = [Path(item['filepath']) for item in info.get('requested_downloads',[]) if item.get('filepath')]
                    source = next((p for p in files if p.is_file()),None)
                    if source is None:
                        guessed = Path(downloader.prepare_filename(info))
                        source = guessed if guessed.is_file() else None
                if source is None:
                    raise RuntimeError('İndirilen ses dosyası bulunamadı.')
                with source.open('rb') as check:
                    header = check.read(256).lstrip().lower()
                if header.startswith((b'<html',b'<!doctype html')):
                    raise RuntimeError('Platform ses yerine hata sayfası döndürdü. Bu ağ veya bağlantı için erişim engellenmiş olabilir.')
                if self.cancel.is_set():
                    raise InterruptedError('İndirme iptal edildi.')
                self.messages.put(('status','İndirildi · Soundpad için ses hazırlanıyor…'))
                identifier = uuid.uuid4().hex
                meta = import_audio(source,self.pad.folder/(identifier+'.f32'),self.cancel)
                entry = self.pad.valid_entry(dict(meta,id=identifier,name=str(info.get('title') or 'İndirilen ses'),
                                                  source='',url=url,start=0,end=meta['frames']))
                self.pad.messages.put(('imported',entry))
                self.messages.put(('progress',100))
                self.messages.put(('status','İndirildi ve Soundpad’e eklendi: '+entry['name']))
        except Exception as exc:
            message = str(exc)
            if isinstance(exc,ModuleNotFoundError):
                message = 'İndirme kitaplığı eksik. Soundpad güncelleyicisini çalıştır veya uygulamanın ortamına yt-dlp ve yt-dlp-ejs kur.'
            elif self.cancel.is_set():
                message = 'İndirme iptal edildi.'
            elif 'Sign in' in message or '403' in message or 'bot' in message.lower():
                message += ' · Platform bu bağlantıyı veya ağı engelliyor olabilir; farklı bir bağlantı dene.'
            self.messages.put(('status',message))
        finally:
            # Only the unique task-owned temporary download is removed.
            shutil.rmtree(folder,ignore_errors=True)
            self.messages.put(('done',None))

    def poll(self):
        changed = False
        for _ in range(50):
            try:
                kind,value = self.messages.get_nowait()
            except queue.Empty:
                break
            if kind=='status':
                self.status = value
            elif kind=='progress':
                self.progress = value
            elif kind=='results':
                self.results = value
                self.status = 'İndirilecek kaynağı seç. Spotify eşleşmesi farklı bir kayıt olabilir; başlığı kontrol et.' if value[0]['source']=='YouTube eşleşmesi' else 'Bağlantı hazır; indirip Soundpad’e ekleyebilirsin.'
                changed = True
            elif kind=='done':
                self.busy = False
        return changed

    def close(self):
        self.cancel.set()
        if self.thread:
            self.thread.join(timeout=2)
