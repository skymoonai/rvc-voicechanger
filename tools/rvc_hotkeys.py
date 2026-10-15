"""Windows global hotkeys; dedicated message thread and opt-in key-state mode."""
from __future__ import annotations
import ctypes
import json
import os
import threading
import time
from pathlib import Path

ACTIONS = {
 'toggle_vc': ('Dönüşümü başlat / durdur', 'Ctrl+Alt+F6'),
 'mute': ('Mikrofon çıkışını sustur / aç', 'Ctrl+Alt+F7'),
 'monitor': ('Kulaklık dinlemesini aç / kapat', 'Ctrl+Alt+F8'),
 'record': ('Test kaydını başlat / bitir', 'Ctrl+Alt+F9'),
 'play_test': ('Son test kaydını dinle', 'Ctrl+Alt+F10'),
 'stop_play': ('Kayıt dinlemeyi durdur', 'Ctrl+Alt+F11'),
 'prev_model': ('Önceki model', 'Ctrl+Alt+PgUp'),
 'next_model': ('Sonraki model', 'Ctrl+Alt+PgDown'),
 'play_a': ('A sonucunu dinle', 'Ctrl+Alt+1'),
 'play_b': ('B sonucunu dinle', 'Ctrl+Alt+2'),
 'toggle_voice': ('Normal mikrofon / dönüştürülmüş ses', 'Ctrl+Alt+F5'),
 'soundpad_pause': ('Soundpad · duraklat / devam et', ''),
 'soundpad_stop': ('Soundpad · tüm sesleri durdur', ''),
 'soundpad_selected': ('Soundpad · seçili klibi çal / duraklat', ''),
 'soundpad_restart': ('Soundpad · seçili klibi baştan çal', ''),
 'soundpad_mic': ('Soundpad · yalnız mikrofona yönlendir', ''),
 'soundpad_head': ('Soundpad · yalnız kulaklığa yönlendir', ''),
 'soundpad_both': ('Soundpad · mikrofon + kulaklık', ''),
 'rnnoise_toggle': ('RNNoise · aç / kapat', ''),
 'apbwe_toggle': ('AP-BWE · canlı ses iyileştirmeyi aç / kapat', ''),
 'clear_cache': ('Gecikmeyi sıfırla · ses tamponlarını temizle', 'Ctrl+Alt+F4'),
}
MODS={'ALT':1,'CTRL':2,'CONTROL':2,'SHIFT':4,'WIN':8,'META':8}
KEYS={'SPACE':0x20,'TAB':9,'ENTER':13,'RETURN':13,'ESC':27,'ESCAPE':27,
 'LEFT':0x25,'UP':0x26,'RIGHT':0x27,'DOWN':0x28,'PGUP':0x21,'PAGEUP':0x21,
 'PGDOWN':0x22,'PAGEDOWN':0x22,'HOME':0x24,'END':0x23,'INSERT':0x2D,'DELETE':0x2E,
 'PAUSE':0x13,'BACKSPACE':8}


def character_key(character):
    if os.name == 'nt':
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.GetKeyboardLayout.argtypes = [wintypes.DWORD]
        user32.GetKeyboardLayout.restype = wintypes.HKL
        user32.VkKeyScanExW.argtypes = [wintypes.WCHAR, wintypes.HKL]
        user32.VkKeyScanExW.restype = ctypes.c_short
        code = user32.VkKeyScanExW(character, user32.GetKeyboardLayout(0))
        if code == -1:
            raise ValueError('Bu klavye düzeninde tuş bulunamadı: ' + character)
        shift = (code >> 8) & 0xFF
        return ((4 if shift & 1 else 0) | (2 if shift & 2 else 0) | (1 if shift & 4 else 0), code & 0xFF)
    # Only for non-Windows compatibility checks; Windows resolves the active layout.
    punctuation = {'-': (0, 0xBD), '=': (0, 0xBB), '+': (4, 0xBB), '*': (4, 0x38),
                   '/': (0, 0xBF), ',': (0, 0xBC), '.': (0, 0xBE), ';': (0, 0xBA),
                   '[': (0, 0xDB), ']': (0, 0xDD), "'": (0, 0xDE), '\\': (0, 0xDC), '`': (0, 0xC0)}
    if character in punctuation:
        return punctuation[character]
    raise ValueError('Desteklenmeyen tuş: ' + character)


def parse_shortcut(text):
    if not text.strip():
        return None
    key = text.strip().upper()
    modifiers = 0
    keypad = False
    while '+' in key and len(key) > 1:
        token, remaining = key.split('+', 1)
        if token in MODS:
            modifiers |= MODS[token]
        elif token in ('NUM', 'NUMPAD'):
            keypad = True
        else:
            break
        key = remaining
    if keypad:
        numkeys = {'*': 0x6A, '+': 0x6B, '-': 0x6D, '.': 0x6E, ',': 0x6E, '/': 0x6F,
                   'ENTER': 13, 'RETURN': 13}
        if len(key) == 1 and key in '0123456789':
            vk = 0x60 + int(key)
        elif key in numkeys:
            vk = numkeys[key]
        else:
            raise ValueError('Desteklenmeyen sayı takımı tuşu: ' + key)
    elif len(key)==1 and key.isascii() and key.isalnum():
        vk=ord(key)
    elif key.startswith('F') and key[1:].isdigit() and 1<=int(key[1:])<=24:
        vk=0x70+int(key[1:])-1
    elif key in KEYS:
        vk=KEYS[key]
    elif len(key) == 1:
        implied, vk = character_key(key)
        modifiers |= implied
    else:
        raise ValueError('Desteklenmeyen tuş: '+key)
    if vk==0x7B:
        raise ValueError('F12 Windows hata ayıklayıcısına ayrılmıştır; başka tuş seç.')
    if modifiers & 8:
        raise ValueError('Windows tuşlu kombinasyonlar sistem tarafından kullanılabilir; Ctrl / Alt / Shift seç.')
    return modifiers,vk


def validate_bindings(bindings, keycodes=None, labels=None):
    parsed={}; seen={}
    for action in bindings:
        label = (labels or {}).get(action, ACTIONS.get(action, (action, ''))[0])
        text = str(bindings.get(action, ''))
        capture = (keycodes or {}).get(action)
        if isinstance(capture, dict) and capture.get('text') == text and text:
            mods, vk = capture.get('modifiers'), capture.get('vk')
            if type(mods) is not int or type(vk) is not int or not 0 <= mods <= 7 or not 1 <= vk <= 254 or vk == 0x7B:
                raise ValueError('Geçersiz fiziksel tuş kaydı: ' + label)
            pair = mods, vk
        else:
            pair = parse_shortcut(text)
        if pair is None:
            continue
        if pair in seen:
            raise ValueError('Aynı kısayol iki işleme atanamaz: '+label)
        seen[pair]=action; parsed[action]=pair
    return parsed


class Hotkeys:
    def __init__(self, emit, status, path=None):
        self.emit=emit; self.status=status
        self.path=Path(path) if path else Path(__file__).resolve().parent.parent/'configs'/'hotkeys.json'
        self.bindings={k:v[1] for k,v in ACTIONS.items()}
        self.keycodes = {}
        self.extra_bindings = {}; self.extra_keycodes = {}; self.extra_labels = {}
        self.enabled=True; self.game_mode=False; self.paused=False
        self.revision=0; self.stop_event=threading.Event(); self.thread=None
        try:
            data=json.loads(self.path.read_text(encoding='utf-8'))
            if not isinstance(data, dict) or not isinstance(data.get('bindings', {}), dict):
                raise ValueError('Geçersiz kısayol dosyası.')
            loaded={k:str(data.get('bindings',{}).get(k,v)) for k,v in self.bindings.items()}
            codes = data.get('keycodes', {})
            if not isinstance(codes, dict): codes = {}
            # Add new actions without taking over a user's existing physical keys.
            existing = {k: v for k, v in loaded.items() if k in data.get('bindings', {})}
            parsed = validate_bindings(existing, codes)
            used = set(parsed.values())
            for action in loaded:
                if action not in existing:
                    key = validate_bindings({action: loaded[action]}, {}).get(action)
                    if key in used:
                        loaded[action] = ''
                    elif key:
                        used.add(key)
            validate_bindings(loaded, codes); self.bindings=loaded; self.keycodes=codes
            self.enabled=bool(data.get('enabled',True)); self.game_mode=bool(data.get('game_mode',False))
        except (OSError,ValueError,TypeError):
            pass

    def start(self):
        if os.name!='nt':
            self.status('Genel kısayollar Windows üzerinde kullanılabilir.')
            return
        self.thread=threading.Thread(target=self._run,name='RVC-global-hotkeys',daemon=True)
        self.thread.start()

    def apply(self, bindings, enabled, game_mode, keycodes=None):
        codes = self.keycodes if keycodes is None else keycodes
        codes = {action: code for action, code in codes.items() if action in bindings and isinstance(code, dict) and code.get('text') == bindings[action]}
        validate_bindings(dict(bindings, **self.extra_bindings), dict(codes, **self.extra_keycodes), self.extra_labels)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        data={'bindings':bindings,'keycodes':codes,'enabled':bool(enabled),'game_mode':bool(game_mode)}
        temp=self.path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
        os.replace(temp,self.path)
        self.bindings=dict(bindings); self.keycodes=codes; self.enabled=bool(enabled); self.game_mode=bool(game_mode)
        self.revision+=1
        if os.name!='nt': self.status('Ayarlar kaydedildi. Genel kısayollar Windows üzerinde çalışır.')

    def validate_extra(self, bindings, codes=None, labels=None):
        return validate_bindings(dict(self.bindings, **bindings), dict(self.keycodes, **(codes or {})), labels)

    def set_extra(self, bindings, codes=None, labels=None):
        self.validate_extra(bindings, codes, labels)
        self.extra_bindings = dict(bindings)
        self.extra_keycodes = dict(codes or {})
        self.extra_labels = dict(labels or {})
        self.revision += 1

    def close(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=1)

    def _run(self):
        from ctypes import wintypes
        user32=ctypes.WinDLL('user32',use_last_error=True)
        user32.RegisterHotKey.argtypes=[wintypes.HWND,ctypes.c_int,wintypes.UINT,wintypes.UINT]
        user32.RegisterHotKey.restype=wintypes.BOOL
        user32.UnregisterHotKey.argtypes=[wintypes.HWND,ctypes.c_int]
        user32.UnregisterHotKey.restype=wintypes.BOOL
        user32.PeekMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT,wintypes.UINT]
        user32.PeekMessageW.restype=wintypes.BOOL
        user32.GetAsyncKeyState.argtypes=[ctypes.c_int]
        user32.GetAsyncKeyState.restype=ctypes.c_short
        msg=wintypes.MSG()
        # Create this thread's message queue before registration.
        user32.PeekMessageW(ctypes.byref(msg),None,0,0,0)
        registered={}; parsed={}; state=None; latches={}; lastfire={}
        try:
            while not self.stop_event.is_set():
                current=(self.revision,self.enabled,self.game_mode,self.paused)
                if state!=current:
                    for identifier in registered: user32.UnregisterHotKey(None,identifier)
                    registered={}; latches={}; parsed={}
                    # Drain old notifications when bindings/mode change.
                    while user32.PeekMessageW(ctypes.byref(msg),None,0x0312,0x0312,1): pass
                    state=current
                    if self.enabled and not self.paused:
                        parsed=validate_bindings(dict(self.bindings, **self.extra_bindings), dict(self.keycodes, **self.extra_keycodes), self.extra_labels)
                        if self.game_mode:
                            self.status('Oyun uyumluluk modu açık · tuş durumu 8 ms aralıkla izlenir.')
                        else:
                            failed=[]
                            for identifier,(action,(mods,vk)) in enumerate(parsed.items(),1):
                                if user32.RegisterHotKey(None,identifier,mods|0x4000,vk): registered[identifier]=action
                                else: failed.append(self.extra_labels.get(action, ACTIONS.get(action, (action, ''))[0]))
                            self.status(('Genel kısayollar etkin.' if not failed else
                                         'Kısayol kaydedilemedi; başka tuş seç: '+', '.join(failed)))
                    else:
                        self.status('Kısayol düzenleniyor; algılama geçici olarak durdu.' if self.paused else 'Genel kısayollar kapalı.')
                now=time.monotonic()
                while user32.PeekMessageW(ctypes.byref(msg),None,0x0312,0x0312,1):
                    action=registered.get(int(msg.wParam))
                    if action and not self.paused and now-lastfire.get(action,0)>.25:
                        lastfire[action]=now; self.emit(action)
                if self.game_mode and self.enabled and not self.paused:
                    keys={vk for _mods,vk in parsed.values()} | {0x12,0x11,0x10,0x5B,0x5C}
                    key_states={key:bool(user32.GetAsyncKeyState(key)&0x8000) for key in keys}
                    for action,(mods,vk) in parsed.items():
                        # Use only the reliable high bit; never the 'pressed since last call' bit.
                        down=key_states[vk]
                        for bit,key in [(1,0x12),(2,0x11),(4,0x10)]:
                            actual=key_states[key]
                            down=down and actual==bool(mods&bit)
                        down=down and not (key_states[0x5B] or key_states[0x5C])
                        if down and not latches.get(action,False) and now-lastfire.get(action,0)>.25:
                            lastfire[action]=now; self.emit(action)
                        latches[action]=down
                self.stop_event.wait(.008 if self.game_mode else .015)
        except Exception as exc:
            self.status('Kısayol algılama durdu: '+str(exc))
        finally:
            for identifier in registered: user32.UnregisterHotKey(None,identifier)
