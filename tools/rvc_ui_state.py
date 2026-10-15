"""Restore the user's approved UI, device choices and tab layout."""
from __future__ import annotations
import json
from pathlib import Path
from tools.rvc_audio_files import atomic_json

PATH = Path(__file__).resolve().parent.parent/'configs'/'ui_state.json'


def load(path=None):
    try:
        data = json.loads(Path(path or PATH).read_text(encoding='utf-8'))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def initial_data(data, saved):
    result = dict(data)
    for key,value in saved.get('values', {}).items():
        if key in result and isinstance(value,(str,int,float,bool)):
            if key in ('pth_path','index_path') and value and not Path(value).is_file():
                continue
            result[key] = value
    return result


def save(window):
    # Shortcut drafts remain drafts until their explicit Save button is used.
    editable = {'input', 'combo', 'slider', 'check', 'radio'}
    values = {k:h.get() for k,h in window.widgets.items()
              if h.kind in editable and not k.startswith(('hotkey_','studio_','monitor_'))
              and isinstance(h.get(),(str,int,float,bool))}
    state = {'version':2,'tabs':[window.tab_id(i) for i in range(window.tabs.count())],
             'current':window.tab_id(window.tabs.currentIndex()),'values':values,
             'voice':getattr(window.owner,'_studio_requested_mode','vc'),
             'geometry':bytes(window.root.saveGeometry().toBase64()).decode('ascii'),
             'log_follow':window.log_follow.isChecked()}
    if state != getattr(window, '_last_saved_ui', None):
        atomic_json(PATH,state)
        window._last_saved_ui = state
