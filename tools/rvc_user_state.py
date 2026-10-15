"""Per-user state survives app updates/uninstall; no cloud account is involved."""
from __future__ import annotations
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

APP_VERSION = '1.3.0-beta.1'
TERMS_VERSION = '2026-10-12.1'
ROOT = Path(__file__).resolve().parent.parent

def data_root():
    custom = os.environ.get('RVCSTUDIO_USER_DATA')
    if custom:
        return Path(custom).expanduser().resolve()
    base = os.environ.get('LOCALAPPDATA') if os.name == 'nt' else os.environ.get('XDG_DATA_HOME')
    return (Path(base) if base else Path.home() / '.local' / 'share') / 'RVCStudio'

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')

def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent,
                prefix=path.name+'.', suffix='.tmp', delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:temporary.unlink(missing_ok=True)

def read_json(path, default=None):
    path = Path(path)
    if not path.exists():return {} if default is None else default
    if path.stat().st_size > 8*1024*1024:
        raise ValueError('Ayar dosyası beklenenden büyük: '+path.name)
    return json.loads(path.read_text(encoding='utf-8-sig'))

def preferences():
    try:
        raw=read_json(data_root()/'preferences.json')
        raw=raw if isinstance(raw,dict) else {}
    except (OSError,ValueError,TypeError):raw={}
    return {'language':raw.get('language') if raw.get('language') in ('tr','en') else 'tr',
            'error_dialogs':bool(raw.get('error_dialogs',True))}

def save_preferences(**changes):
    value=preferences()
    value.update({k:v for k,v in changes.items() if k in value})
    if value['language'] not in ('tr','en'):raise ValueError('Unsupported language')
    atomic_json(data_root()/'preferences.json',value)
    return value

def terms():
    return read_json(ROOT/'assets'/'legal'/'terms.json')

def terms_hash():
    return hashlib.sha256(json.dumps(terms(),ensure_ascii=False,sort_keys=True).encode('utf-8')).hexdigest()

def consent_valid():
    try:
        record=read_json(data_root()/'consent.json')
        return isinstance(record,dict) and record.get('version')==TERMS_VERSION and record.get('sha256')==terms_hash() and record.get('accepted') is True
    except (OSError,ValueError,TypeError):return False

def save_consent(language):
    if language not in ('tr','en'):raise ValueError('Unsupported language')
    atomic_json(data_root()/'consent.json',{'version':TERMS_VERSION,'sha256':terms_hash(),
        'accepted':True,'accepted_at':now(),'language':language,'app_version':APP_VERSION})

def begin_session():
    path=data_root()/'session.json'
    try:old=read_json(path)
    except (OSError,ValueError,TypeError):old={}
    token=uuid.uuid4().hex
    atomic_json(path,{'token':token,'pid':os.getpid(),'started_at':now(),'clean':False,'app_version':APP_VERSION})
    return token, isinstance(old,dict) and old.get('clean') is False

def end_session(token):
    path=data_root()/'session.json'
    value=read_json(path)
    if isinstance(value,dict) and value.get('token')==token:
        value.update(clean=True,ended_at=now())
        atomic_json(path,value)
