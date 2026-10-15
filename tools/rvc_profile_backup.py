"""Portable model profiles, local durable copies, bounded streaming archive I/O."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import os
import queue
import re
import shutil
import stat
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path
from tools.rvc_user_state import APP_VERSION, data_root, atomic_json, read_json, now

CHUNK=1024*1024
LIMIT=32*1024**3
FILE_PATTERN=re.compile(r'models/[a-f0-9]{64}\.(pth|index)\Z')
RANGES={'pitch':(-16,16),'formant':(-2,2),'index_rate':(0,1),'rms_mix_rate':(0,1),
        'threhold':(-60,0),'block_time':(.02,1.5),'crossfade_length':(.01,.15),'extra_time':(.05,5)}

def valid_settings(raw):
    if not isinstance(raw,dict):raise ValueError('Profil ayarları geçersiz.')
    result={}
    for name,(low,high) in RANGES.items():
        if name in raw:
            value=raw[name]
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not low<=value<=high:
                raise ValueError('Profil ayarı aralık dışında: '+name)
            result[name]=value
    for name in ('I_noise_reduce','O_noise_reduce'):
        if name in raw:
            if not isinstance(raw[name],bool):raise ValueError('Profil anahtarı geçersiz: '+name)
            result[name]=raw[name]
    for name,allowed in (('f0method',('pm','rmvpe','fcpe')),('sr_type',('sr_model','sr_device'))):
        if name in raw:
            if raw[name] not in allowed:raise ValueError('Profil seçeneği geçersiz: '+name)
            result[name]=raw[name]
    result.setdefault('pitch',0)
    return result

def valid_profile(raw,*,local=True):
    if not isinstance(raw,dict):raise ValueError('Profil kaydı geçersiz.')
    identifier=str(raw.get('id',''))
    if not re.fullmatch('[a-f0-9]{32}',identifier):identifier=uuid.uuid4().hex
    result={'id':identifier,'name':str(raw.get('name') or 'Model')[:200],
            'settings':valid_settings(raw.get('settings') or {}),'pth_path':'','index_path':''}
    for kind,extension in (('pth','.pth'),('index','.index')):
        value=raw.get(kind+'_path','')
        if not isinstance(value,str) or len(value)>8192:raise ValueError('Model dosya yolu geçersiz.')
        if value and Path(value).suffix.casefold()!=extension:raise ValueError('Model dosya türü geçersiz.')
        if local:result[kind+'_path']=value
    if local:
        for key in ('managed_models','runtime_models'):
            if isinstance(raw.get(key),dict):result[key]={k:str(v) for k,v in raw[key].items() if k in ('pth','index') and isinstance(v,str)}
    if local and not result['index_path'] and not result.get('managed_models',{}).get('index'):result['settings']['index_rate']=0.
    return result

def load_database(app_root):
    target=data_root()/'profiles.json'
    source=target if target.exists() else Path(app_root)/'configs'/'model_profiles.json'
    raw=read_json(source,{'profiles':[]})
    if not isinstance(raw,dict) or not isinstance(raw.get('profiles',[]),list):raise ValueError('Profil kitaplığı geçersiz.')
    rows=[valid_profile(row) for row in raw.get('profiles',[])[:5000]]
    ids={row['id'] for row in rows}
    value={'version':2,'active_id':raw.get('active_id') if raw.get('active_id') in ids else None,'profiles':rows}
    if not target.exists():atomic_json(target,value)
    return target,value

def store_database(path,profiles,active):
    value={'version':2,'active_id':active,'profiles':profiles}
    atomic_json(path,value)

def model_source(entry,kind):
    managed=(entry.get('managed_models') or {}).get(kind)
    if managed:
        path=Path(managed)
        if path.resolve().is_relative_to((data_root()/'library').resolve()) and path.is_file():return path
    value=entry.get(kind+'_path')
    return Path(value) if value and Path(value).is_file() else None

def runtime_entry(entry,app_root):
    result=dict(entry)
    for kind in ('pth','index'):
        source=model_source(entry,kind)
        if source is None:continue
        if str(source).isascii():result[kind+'_path']=str(source)
        else:
            alias=(entry.get('runtime_models') or {}).get(kind)
            if alias and Path(alias).is_file() and Path(alias).resolve().is_relative_to((Path(app_root)/'models'/'profile_runtime').resolve()):result[kind+'_path']=alias
    return result

def discard_changed_refs(entry,kind,value,app_root):
    previous=runtime_entry(entry,app_root).get(kind+'_path','')
    if value not in (entry.get(kind+'_path',''),previous):
        for key in ('managed_models','runtime_models'):
            if isinstance(entry.get(key),dict):entry[key].pop(kind,None)

def check_cancel(cancel):
    if cancel is not None and cancel.is_set():raise InterruptedError('İşlem iptal edildi.')

def durable_copy(source,kind,cancel,progress=None):
    source=Path(source)
    if source.suffix.casefold()!='.'+kind:raise ValueError('Model dosya türü geçersiz.')
    size=source.stat().st_size
    if size<=0 or size>LIMIT:raise ValueError('Model dosya boyutu geçersiz.')
    root=data_root()/'library';root.mkdir(parents=True,exist_ok=True)
    if source.resolve().is_relative_to(root.resolve()) and re.fullmatch(r'[a-f0-9]{64}\.'+kind,source.name):return source
    temporary=root/(uuid.uuid4().hex+'.part')
    digest=hashlib.sha256();copied=0
    try:
        with source.open('rb') as reader,temporary.open('xb') as writer:
            before=os.fstat(reader.fileno())
            while block:=reader.read(CHUNK):
                check_cancel(cancel);digest.update(block);writer.write(block);copied+=len(block)
                if progress:progress(copied,size)
            after=os.fstat(reader.fileno())
            if copied!=size or before.st_mtime_ns!=after.st_mtime_ns:raise OSError('Model kopyalama sırasında değişti; yeniden dene.')
        check_cancel(cancel)
        target=root/(digest.hexdigest()+'.'+kind)
        if target.exists():temporary.unlink()
        else:os.replace(temporary,target)
        return target
    finally:temporary.unlink(missing_ok=True)

def make_alias(source,identifier,kind,app_root,cancel):
    if str(source).isascii():return str(source)
    root=Path(app_root)/'models'/'profile_runtime'
    target=root/(identifier+'.'+kind)
    if not str(target).isascii():
        raise ValueError('Bu RVC motoru ASCII model yolu istiyor. Uygulamayı C:\\RVCStudio gibi bir klasöre kur.')
    root.mkdir(parents=True,exist_ok=True)
    try:
        if target.exists() and os.path.samefile(source,target):return str(target)
    except OSError:pass
    temporary=root/(uuid.uuid4().hex+'.part')
    try:
        try:os.link(source,temporary)
        except OSError:
            with Path(source).open('rb') as reader,temporary.open('xb') as writer:
                while block:=reader.read(CHUNK):check_cancel(cancel);writer.write(block)
        check_cancel(cancel);os.replace(temporary,target)
        return str(target)
    finally:temporary.unlink(missing_ok=True)

def sync_models(rows,app_root,cancel,progress=None):
    result=[]
    for row in rows:
        managed={};runtime={}
        for kind in ('pth','index'):
            source=model_source(row,kind)
            if source is None:continue
            destination=durable_copy(source,kind,cancel,progress)
            managed[kind]=str(destination)
            runtime[kind]=make_alias(destination,row['id'],kind,app_root,cancel)
        result.append({'id':row['id'],'source_paths':{k:row.get(k+'_path','') for k in ('pth','index')},
                       'managed_models':managed,'runtime_models':runtime})
    return result

def export_bundle(rows,active,destination,include_models,cancel,progress=None):
    if len(rows)>5000:raise ValueError('Yedekte en fazla 5000 profil olabilir.')
    destination=Path(destination)
    if destination.suffix.casefold() not in ('.rvcprofile','.zip'):raise ValueError('Yedek uzantısı .rvcprofile veya .zip olmalı.')
    destination.parent.mkdir(parents=True,exist_ok=True)
    protected=[model_source(row,kind) for row in rows for kind in ('pth','index')]
    if any(path and path.resolve()==destination.resolve() for path in protected):raise ValueError('Model dosyasının üstüne yedek yazılamaz.')
    temporary=destination.with_name(destination.name+'.'+uuid.uuid4().hex+'.part')
    manifest={'format':'RVCStudioProfile','schema':1,'app_version':APP_VERSION,'created_at':now(),
              'active_id':active,'include_models':bool(include_models),'profiles':[],'files':{}}
    written={}
    try:
        with zipfile.ZipFile(temporary,'w',allowZip64=True) as archive:
            for source_row in rows:
                check_cancel(cancel)
                row=valid_profile(source_row)
                record={k:row[k] for k in ('id','name','settings')}
                record['models']={}
                for kind in ('pth','index'):
                    source=model_source(row,kind)
                    if source is None:
                        if kind=='pth' and include_models:raise FileNotFoundError('Model bulunamadı: '+row['name'])
                        continue
                    if not include_models:continue
                    size=source.stat().st_size
                    # Use a stable neutral temporary member id; model hash is verified on import.
                    key=str(source.resolve())
                    if key in written:record['models'][kind]=written[key];continue
                    if len(written)>=2047:raise ValueError('Yedekte en fazla 2047 model dosyası olabilir.')
                    if sum(item['bytes'] for item in manifest['files'].values())+size>LIMIT:
                        raise ValueError('Yedek 32 GB sınırını aşıyor.')
                    digest=hashlib.sha256();copied=0
                    member='models/'+uuid.uuid4().hex+uuid.uuid4().hex+'.'+kind
                    info=zipfile.ZipInfo(member);info.compress_type=zipfile.ZIP_STORED
                    with source.open('rb') as reader,archive.open(info,'w',force_zip64=True) as writer:
                        before=os.fstat(reader.fileno())
                        while block:=reader.read(CHUNK):
                            check_cancel(cancel);digest.update(block);writer.write(block);copied+=len(block)
                            if progress:progress(copied,size)
                        after=os.fstat(reader.fileno())
                    if copied!=size or before.st_mtime_ns!=after.st_mtime_ns:raise OSError('Model dışa aktarılırken değişti.')
                    written[key]=member
                    manifest['files'][member]={'sha256':digest.hexdigest(),'bytes':copied,'kind':kind}
                    record['models'][kind]=member
                manifest['profiles'].append(record)
            payload=json.dumps(manifest,ensure_ascii=False,allow_nan=False).encode('utf-8')
            if len(payload)>8*1024*1024:raise ValueError('Yedek bildirimi çok büyük.')
            if sum(item['bytes'] for item in manifest['files'].values())+len(payload)>LIMIT:
                raise ValueError('Yedek 32 GB sınırını aşıyor.')
            archive.writestr('manifest.json',payload,compress_type=zipfile.ZIP_DEFLATED)
        check_cancel(cancel);os.replace(temporary,destination)
        return str(destination)
    finally:temporary.unlink(missing_ok=True)

class ImportedProfiles(list):
    active_id=None

def import_bundle(source,app_root,cancel,progress=None):
    staging_root=data_root()/'staging';staging_root.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='profile-',dir=staging_root) as temporary:
        stage=Path(temporary)
        with zipfile.ZipFile(source) as archive:
            infos=archive.infolist()
            names=[info.filename for info in infos]
            if len(names)>2048 or len(names)!=len(set(names)) or 'manifest.json' not in names:raise ValueError('Yedek dosya listesi geçersiz.')
            if sum(info.file_size for info in infos)>LIMIT:raise ValueError('Yedek açılmış boyutu 32 GB sınırını aşıyor.')
            for info in infos:
                check_cancel(cancel)
                if info.filename!='manifest.json' and not FILE_PATTERN.fullmatch(info.filename):raise ValueError('Yedek içinde izin verilmeyen dosya: '+info.filename)
                if stat.S_ISLNK(info.external_attr>>16) or info.flag_bits&1:raise ValueError('Bağlantılı veya şifreli yedek desteklenmiyor.')
                if info.file_size/max(1,info.compress_size)>500:raise ValueError('Yedek sıkıştırma oranı güvenli sınırı aşıyor.')
            if archive.getinfo('manifest.json').file_size>8*1024*1024:raise ValueError('Yedek bildirimi çok büyük.')
            manifest=json.loads(archive.read('manifest.json'))
            if not isinstance(manifest,dict) or manifest.get('format')!='RVCStudioProfile' or manifest.get('schema')!=1:raise ValueError('Desteklenmeyen profil yedeği.')
            records,files=manifest.get('profiles'),manifest.get('files')
            if not isinstance(records,list) or len(records)>5000 or not isinstance(files,dict) or set(files)!=set(names)-{'manifest.json'}:raise ValueError('Yedek bildirimi geçersiz.')
            validated=ImportedProfiles()
            for raw in records:
                row=valid_profile(raw,local=False)
                refs=raw.get('models',{})
                if not isinstance(refs,dict) or any(kind not in ('pth','index') or name not in files or not str(name).endswith('.'+kind) for kind,name in refs.items()):raise ValueError('Yedek model bağlantısı geçersiz.')
                row['_refs']=refs;validated.append(row)
            staged={}
            for name,description in files.items():
                info=archive.getinfo(name)
                if not isinstance(description,dict) or description.get('bytes')!=info.file_size or not re.fullmatch('[a-f0-9]{64}',str(description.get('sha256',''))) or description.get('kind')!=Path(name).suffix[1:]:raise ValueError('Model bütünlük bilgisi geçersiz.')
                target=stage/Path(name).name;digest=hashlib.sha256();copied=0
                with archive.open(name) as reader,target.open('xb') as writer:
                    while block:=reader.read(CHUNK):
                        check_cancel(cancel);digest.update(block);writer.write(block);copied+=len(block)
                        if progress:progress(copied,info.file_size)
                if digest.hexdigest()!=description['sha256'] or copied!=info.file_size:raise ValueError('Yedek model dosyası bozuk: '+name)
                staged[name]=target
        check_cancel(cancel)
        library=data_root()/'library';library.mkdir(parents=True,exist_ok=True)
        targets={}
        for name,file in staged.items():
            target=library/(files[name]['sha256']+'.'+files[name]['kind'])
            intact=False
            if target.is_file() and target.stat().st_size==files[name]['bytes']:
                digest=hashlib.sha256()
                with target.open('rb') as reader:
                    while block:=reader.read(CHUNK):check_cancel(cancel);digest.update(block)
                intact=digest.hexdigest()==files[name]['sha256']
            if intact:file.unlink()
            else:os.replace(file,target)
            targets[name]=target
        for row in validated:
            row['managed_models']={};row['runtime_models']={}
            for kind,name in row.pop('_refs').items():
                path=targets[name]
                row['managed_models'][kind]=str(path)
                row['runtime_models'][kind]=make_alias(path,row['id'],kind,app_root,cancel)
                row[kind+'_path']=row['runtime_models'][kind]
            if not row['index_path']:row['settings']['index_rate']=0.
        candidate=manifest.get('active_id')
        if not isinstance(candidate,str):candidate=None
        validated.active_id=candidate if candidate in {row['id'] for row in validated} else None
        return validated

def merge_profiles(existing,incoming):
    result=copy.deepcopy(existing);by_id={row['id']:row for row in result}
    for row in incoming:
        row=copy.deepcopy(row)
        previous=by_id.get(row['id'])
        if previous:
            if previous.get('settings')==row.get('settings') and previous.get('managed_models')==row.get('managed_models'):continue
            row['id']=uuid.uuid4().hex
            row['name']=row['name']+' (İçe aktarılan)'
        result.append(row);by_id[row['id']]=row
    return result

class ProfileService:
    def __init__(self,owner,app_root):
        self.owner,self.app_root=owner,Path(app_root)
        self.messages=queue.Queue(maxsize=100)
        self.jobs=queue.Queue(maxsize=4)
        self.cancel=threading.Event();self.stop=threading.Event();self.lock=threading.Lock()
        self.pending_sync=None;self.busy=False;self.current='';self.worker=None
        self._last_progress=0

    def post(self,kind,**value):
        try:self.messages.put_nowait((kind,value))
        except queue.Full:pass

    def _start(self):
        if self.worker is None:
            self.worker=threading.Thread(target=self._work,name='RVC profile files',daemon=True)
            self.worker.start()

    def queue_sync(self,rows):
        with self.lock:self.pending_sync=copy.deepcopy(rows)
        self._start()

    def submit(self,kind,**options):
        if self.busy:raise RuntimeError('Profil dosya işleminin bitmesini bekle.')
        self.jobs.put_nowait((kind,options));self.busy=True;self._start()

    def progress(self,done,total):
        stamp=__import__('time').monotonic()
        if stamp-self._last_progress>=.15:
            self._last_progress=stamp;self.post('progress',job=self.current,done=done,total=total)

    def cancel_pending(self):
        self.cancel.set()
        while True:
            try:kind,_=self.jobs.get_nowait()
            except queue.Empty:break
            self.post('error',job=kind,error=InterruptedError('İşlem iptal edildi.'))
            self.busy=False

    def _work(self):
        while not self.stop.is_set():
            try:kind,options=self.jobs.get(timeout=.2)
            except queue.Empty:
                with self.lock:rows,self.pending_sync=self.pending_sync,None
                if rows is None:continue
                kind,options='sync',{'rows':rows}
            self.current=kind;self.cancel.clear()
            if self.stop.is_set():break
            try:
                if kind=='sync':result=sync_models(options['rows'],self.app_root,self.cancel,self.progress)
                elif kind=='export':result=export_bundle(options['rows'],options.get('active'),options['path'],options.get('include_models',True),self.cancel,self.progress)
                elif kind=='import':result=import_bundle(options['path'],self.app_root,self.cancel,self.progress)
                else:raise ValueError('Unknown profile operation')
                self.post('done',job=kind,result=result)
            except Exception as exc:self.post('error',job=kind,error=exc)
            finally:
                self.current=''
                if kind!='sync':self.busy=False

    def close(self):
        self.stop.set();self.cancel.set()
        if self.worker:self.worker.join(timeout=.5)
