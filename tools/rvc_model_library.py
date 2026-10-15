"""Optional-index profiles, installed only by the modern runtime adapter."""
from pathlib import Path
from uuid import uuid4


def add_model(owner, pth, index, name, values, running=False):
    pth, index = str(Path(pth).resolve()), str(Path(index).resolve()) if index else ''
    if not Path(pth).is_file() or (index and not Path(index).is_file()):
        raise ValueError('Model veya indeks dosyası bulunamadı.')
    if not pth.isascii() or not index.isascii():
        raise ValueError('Bu RVC sürümünde dosya yolları ASCII olmalı. Modeli Türkçe karakter içermeyen bir klasöre taşı.')
    owner.profile_save_values(values)
    settings = owner.profile_capture(values)
    # A newly imported voice has no saved pitch of its own.
    settings['pitch'] = 0
    if not index:
        settings['index_rate'] = 0.
    owner.stop_stream()
    entry = {'id': uuid4().hex, 'name': name.strip() or Path(pth).stem,
             'pth_path': pth, 'index_path': index, 'settings': settings}
    owner.profiles.append(entry)
    owner.active_profile_id = entry['id']
    owner.profile_store()
    owner.profile_labels_refresh()
    owner.profile_apply(entry)
    if running:
        owner.window.write_event_value('start_vc', 'model_added')
    owner.profile_show_status('Model eklendi: ' + entry['name'] + (' · indeks kullanılmıyor.' if not index else ''))
    return entry


def install_profile_hooks(profile_class, sg):
    if getattr(profile_class, '_studio_optional_index', False):
        return
    original = profile_class.profile_handle_event
    original_apply = profile_class.profile_apply
    original_start_data = profile_class.profile_start_data

    def handle(owner, event, values, running):
        if event != 'profile_add':
            return original(owner, event, values, running)
        pth = sg.popup_get_file('RVC model (.pth) seç', file_types=(('RVC', '*.pth'),), no_window=True)
        if not pth:
            return True
        answer = sg.popup_yes_no('Bu modele bir indeks (.index) eklemek istiyor musunuz?')
        if answer not in ('Yes', 'No'):
            return True
        index = ''
        if answer == 'Yes':
            index = sg.popup_get_file('Bu modele ait .index dosyasını seç', file_types=(('Index', '*.index'),), no_window=True)
            if not index:
                return True
        name = sg.popup_get_text('Model için görünen ad', default_text=Path(pth).stem)
        if name is None:
            return True
        try:
            add_model(owner, pth, index, name, values, running)
        except Exception as exc:
            sg.popup_error(str(exc))
        return True

    def apply(owner, entry):
        from tools.rvc_profile_backup import runtime_entry
        entry = runtime_entry(entry, Path(__file__).resolve().parent.parent)
        settings = dict(entry.get('settings') or {})
        settings.setdefault('pitch', 0)
        if not entry.get('index_path'):
            settings['index_rate'] = 0.
        entry = dict(entry, settings=settings)
        return original_apply(owner, entry)

    def start_data(owner, data):
        result = original_start_data(owner, data)
        current = owner.profile_current()
        if current:
            result = dict(result, pitch=(current.get('settings') or {}).get('pitch', 0))
        if current:
            from tools.rvc_profile_backup import runtime_entry
            runtime=runtime_entry(current, Path(__file__).resolve().parent.parent)
            result=dict(result,pth_path=runtime.get('pth_path',''),index_path=runtime.get('index_path',''))
        if not result.get('index_path', '').strip():
            result = dict(result, index_rate=0.)
        return result

    profile_class.profile_handle_event = handle
    profile_class.profile_apply = apply
    profile_class.profile_start_data = start_data
    profile_class._studio_optional_index = True