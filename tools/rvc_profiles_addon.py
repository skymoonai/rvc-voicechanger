"""GUI model profiles for RVC realtime_gui.py, standalone no external dependencies beyond FreeSimpleGUI."""
import json
import os
from pathlib import Path
from uuid import uuid4

from tools.rvc_modern_qt import sg


class ProfileSupport:
    PROFILE_KEYS = (
        "pitch", "formant", "index_rate", "rms_mix_rate", "threhold",
        "block_time", "crossfade_length", "extra_time", "I_noise_reduce",
        "O_noise_reduce",
    )
    PROFILE_EVENTS = set(PROFILE_KEYS) | {"pm", "rmvpe", "fcpe", "sr_model", "sr_device"}

    def profile_init(self):
        from tools.rvc_profile_backup import load_database
        from tools.rvc_user_state import data_root
        root = Path(__file__).resolve().parent.parent
        self.profile_path = data_root() / 'profiles.json'
        self.profiles, self.active_profile_id, self.profile_labels = [], None, {}
        try:
            self.profile_path, obj = load_database(root)
            self.profiles = obj['profiles']
            self.active_profile_id = obj['active_id']
        except (OSError, ValueError, TypeError) as exc:
            from tools.rvc_errors import report
            report(exc, 'Profil kitaplığını açma')
            if self.profile_path.is_file():
                try:
                    import shutil
                    backup = data_root()/'backups'/('profiles_unreadable_'+uuid4().hex+'.json')
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(self.profile_path, backup)
                except OSError:pass

    def profile_current(self):
        return next((p for p in self.profiles if p.get("id") == self.active_profile_id), None)

    def profile_store(self):
        from tools.rvc_profile_backup import store_database
        from tools.rvc_user_state import data_root
        obj = {'version': 2, 'active_id': self.active_profile_id, 'profiles': self.profiles}
        if obj == getattr(self, '_last_persisted_profiles', None):return
        if not getattr(self, '_profile_snapshot_done', False) and self.profile_path.is_file():
            import shutil
            directory=data_root()/'backups'
            directory.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.profile_path, directory/('profiles_previous_session_'+uuid4().hex+'.json'))
            snapshots=sorted(directory.glob('profiles_previous_session_*.json'),key=lambda path:path.stat().st_mtime)
            for old in snapshots[:-5]:old.unlink(missing_ok=True)
            self._profile_snapshot_done=True
        store_database(self.profile_path,self.profiles,self.active_profile_id)
        import copy
        self._last_persisted_profiles=copy.deepcopy(obj)
        service=getattr(self,'_profile_service',None)
        if service:service.queue_sync(self.profiles)

    def profile_label(self, entry):
        return entry.get("name", "Adsiz") + " [" + entry.get("id", "")[:6] + "]"

    def profile_labels_refresh(self):
        self.profile_labels = {self.profile_label(p): p.get("id") for p in self.profiles}
        current = self.profile_current()
        if hasattr(self, "window"):
            self.window["profile_select"].update(
                values=list(self.profile_labels),
                value=self.profile_label(current) if current else "",
            )

    def profile_start_data(self, data):
        current = self.profile_current()
        if current:
            data = dict(data)
            data.update(current.get("settings", {}))
            data["pth_path"] = current.get("pth_path", "")
            data["index_path"] = current.get("index_path", "")
            f0 = data.get("f0method", "rmvpe")
            if f0 not in ("pm", "rmvpe", "fcpe"):
                f0 = "rmvpe"
            for method in ("pm", "rmvpe", "fcpe"):
                data[method] = method == f0
            data["sr_model"] = data.get("sr_type", "sr_model") == "sr_model"
            data["sr_device"] = not data["sr_model"]
        return data

    def profile_layout(self, data):
        self.profile_labels_refresh()
        current = self.profile_current()
        return [[sg.Frame("KAYITLI SES MODELLERI - OTOMATIK PROFIL", [
            [sg.Combo(
                list(self.profile_labels),
                default_value=self.profile_label(current) if current else "",
                key="profile_select", readonly=True, enable_events=True, size=(42, 1)
            ),
             sg.Button("Model Ekle", key="profile_add"),
             sg.Button("Kaydet", key="profile_save"),
             sg.Button("Yeniden Adlandir", key="profile_rename"),
             sg.Button("Sil", key="profile_delete")],
            [sg.Text("Model secince ayarlari yuklenir. Degisiklikler otomatik kaydedilir.",
                     key="profile_status", size=(100, 1))],
        ])]]

    def profile_capture(self, values):
        settings = {k: values.get(k) for k in self.PROFILE_KEYS if k in values}
        settings["f0method"] = next((x for x in ("pm", "rmvpe", "fcpe") if values.get(x)), "rmvpe")
        settings["sr_type"] = "sr_device" if values.get("sr_device") else "sr_model"
        return settings

    def profile_save_values(self, values):
        if not isinstance(values, dict):
            return
        current = self.profile_current()
        if current is None:
            return
        current["settings"] = self.profile_capture(values)
        from tools.rvc_profile_backup import discard_changed_refs
        root=Path(__file__).resolve().parent.parent
        for kind in ('pth','index'):
            key=kind+'_path'
            value=str(values.get(key,current.get(key,''))).strip()
            discard_changed_refs(current,kind,value,root)
            current[key]=value
        self.profile_store()

    def profile_show_status(self, message):
        self.window["profile_status"].update(message)

    def profile_apply(self, entry):
        self.window["pth_path"].update(value=entry.get("pth_path", ""))
        self.window["index_path"].update(value=entry.get("index_path", ""))
        settings = entry.get("settings", {})
        for key in self.PROFILE_KEYS:
            if key in settings:
                self.window[key].update(value=settings[key])
        f0 = settings.get("f0method", "rmvpe")
        for name in ("pm", "rmvpe", "fcpe"):
            self.window[name].update(value=(name == f0))
        sr = settings.get("sr_type", "sr_model")
        for name in ("sr_model", "sr_device"):
            self.window[name].update(value=(name == sr))
        self.profile_show_status("Aktif: " + entry.get("name", "Model") + " | Ayarlar yuklendi")

    def profile_choose_model(self, label, values, is_running):
        next_id = self.profile_labels.get(label)
        if next_id is None or next_id == self.active_profile_id:
            return
        self.profile_save_values(values)
        self.stop_stream()
        self.active_profile_id = next_id
        target = self.profile_current()
        if not target:
            return
        self.profile_store()
        self.profile_apply(target)
        if is_running:
            self.profile_show_status("Model degistiriliyor: " + target.get("name", ""))
            self.window.write_event_value("start_vc", "model_switch")

    def profile_handle_event(self, event, values, is_running):
        if event == "profile_select":
            self.profile_choose_model(values.get("profile_select", ""), values, is_running)
            return True

        if event == "profile_add":
            self.profile_save_values(values)
            pth = sg.popup_get_file("RVC model (.pth) sec", file_types=(("RVC", "*.pth"),), no_window=True)
            if not pth:
                return True
            idx = sg.popup_get_file("Bu modele ait (.index) dosyasini sec", file_types=(("Index", "*.index"),), no_window=True)
            if not idx:
                return True
            if not Path(pth).is_file() or not Path(idx).is_file():
                sg.popup_error("Model veya index dosyasi bulunamadi")
                return True
            if not pth.isascii() or not idx.isascii():
                sg.popup_error("RVC bu surumde Turkce/Unicode karakterli dosya yollarini reddediyor. Modeli ASCII bir klasore tasi.")
                return True
            name = sg.popup_get_text("Model icin gorunen ad", default_text=Path(pth).stem)
            if name is None:
                return True
            name = name.strip() or Path(pth).stem
            self.stop_stream()
            self.profiles.append({
                "id": uuid4().hex, "name": name,
                "pth_path": str(Path(pth).resolve()),
                "index_path": str(Path(idx).resolve()),
                "settings": self.profile_capture(values),
            })
            self.active_profile_id = self.profiles[-1]["id"]
            self.profile_store()
            self.profile_labels_refresh()
            self.profile_apply(self.profile_current())
            if is_running:
                self.window.write_event_value("start_vc", "model_added")
                self.profile_show_status("Yeni model yukleniyor...")
            else:
                self.profile_show_status("Model eklendi. Baslat'a basabilirsin.")
            return True

        if event == "profile_save":
            if self.profile_current():
                self.profile_save_values(values)
                self.profile_show_status("Aktif profil kaydedildi.")
            else:
                sg.popup("Once Model Ekle ile bir profil olustur.")
            return True

        if event == "profile_rename":
            current = self.profile_current()
            if current:
                name = sg.popup_get_text("Yeni model adi", default_text=current.get("name", ""))
                if name and name.strip():
                    current["name"] = name.strip()
                    self.profile_store()
                    self.profile_labels_refresh()
            return True

        if event == "profile_delete":
            current = self.profile_current()
            if current and sg.popup_yes_no("Sadece profili sil? PTH/INDEX dosyalari silinmeyecek.") == "Yes":
                self.stop_stream()
                self.profiles = [p for p in self.profiles if p.get("id") != self.active_profile_id]
                self.active_profile_id = None
                self.profile_store()
                self.profile_labels_refresh()
                self.profile_show_status("Profil silindi. Model dosyalarina dokunulmadi.")
            return True

        if event in self.PROFILE_EVENTS:
            self.profile_save_values(values)
        return False
