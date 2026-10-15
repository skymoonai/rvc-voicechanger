"""Settings holds language, beta/legal information, profile portability, hotkeys and performance."""
from __future__ import annotations
import copy
import queue
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QTabWidget,QScrollArea,QComboBox,
    QCheckBox,QPushButton,QLabel,QProgressBar,QFileDialog,QMessageBox)
from tools.rvc_i18n import t
from tools import rvc_i18n as i18n
from tools.rvc_user_state import preferences,save_preferences,data_root,APP_VERSION,atomic_json,now
from tools.rvc_profile_backup import merge_profiles
from tools.rvc_errors import report

class SettingsPage(QWidget):
    def __init__(self,window):
        super().__init__()
        self.window=window
        import weakref
        window.app._rvc_settings_reference=weakref.ref(self)
        self.setProperty('studio_audio_editor',True)
        root=QVBoxLayout(self);root.setContentsMargins(0,8,0,8)
        root.addWidget(window._text('Ayarlar','section'))
        self.tabs=QTabWidget();root.addWidget(self.tabs,1)
        self.sections={}
        general=self.section('general','Genel')
        profiles=self.section('profiles','Profiller ve Yedekler')
        self.shortcuts_layout=self.section('shortcuts','Kısayollar')
        self.performance_layout=self.section('performance','Performans')

        card,layout=window._card('Arayüz dili')
        self.language=QComboBox();self.language.setProperty('rvc_language_selector',True);self.language.addItem('Türkçe','tr');self.language.addItem('English','en')
        self.language.setCurrentIndex(0 if i18n.LANGUAGE=='tr' else 1)
        layout.addWidget(self.language)
        layout.addWidget(window._text('Dil tercihi hemen uygulanır; model ve cihaz seçimleri değişmez.'))
        self.language.currentIndexChanged.connect(self.language_changed)
        general.addWidget(card)
        beta,bl=window._card('Beta bildirimi',APP_VERSION)
        bl.addWidget(window._text('Beta uygulama ücretsizdir ve kâr amacı gütmez. Hatalar, donmalar ve çökmeler olabilir.'))
        bl.addWidget(self.button('Sözleşmeyi Aç',self.show_terms))
        general.addWidget(beta)
        errors,el=window._card('Hata bildirimi')
        self.error_dialogs=QCheckBox(t('Hata bildirim pencerelerini göster'))
        self.error_dialogs.setChecked(preferences()['error_dialogs'])
        self.error_dialogs.toggled.connect(self.error_options)
        el.addWidget(self.error_dialogs)
        el.addWidget(window._text('Tekrarlayan hatalar birleştirilir. Hata raporları otomatik gönderilmez.'))
        from tools.rvc_beta_ui import open_error_logs,save_error_report
        el.addWidget(self.button('Yerel hata günlüklerini aç',open_error_logs))
        el.addWidget(self.button('Hata raporunu kaydet',lambda:save_error_report(window.root)))
        general.addWidget(errors)
        general.addWidget(self.button('Sekme sırasını varsayılana döndür',window.reset_tab_order))

        pc,pl=window._card('Yerel profil kütüphanesi')
        pl.addWidget(window._text('Model ayarların kullanıcı klasöründe korunur. Model dosyaları arka planda kalıcı kütüphaneye kopyalanır.'))
        self.profile_location=QLabel(str(data_root()))
        self.profile_location.setProperty('rvc_i18n_skip',True)
        self.profile_location.setWordWrap(True);pl.addWidget(self.profile_location)
        pl.addWidget(self.button('Yerel profil klasörünü aç',self.open_profile_folder))
        pl.addWidget(window._text('Paylaşmadan önce modellerin lisansını kontrol et. Yedekler kişisel model dosyalarını içerebilir.'))
        self.include_models=QCheckBox(t('Model dosyalarını da ekle · taşınabilir yedek'))
        self.include_models.setChecked(True);pl.addWidget(self.include_models)
        pl.addWidget(window._text('Model dosyaları olmadan alınan yedekte modelleri tekrar bağlamak gerekir.'))
        row=QHBoxLayout()
        self.export=self.button('Profilleri Dışa Aktar',self.export_profiles,True);row.addWidget(self.export)
        self.import_button=self.button('Profilleri İçe Aktar',self.import_profiles);row.addWidget(self.import_button)
        pl.addLayout(row)
        self.progress=QProgressBar();self.progress.setRange(0,100);pl.addWidget(self.progress)
        self.cancel=self.button('İşlemi İptal Et',window.profile_service.cancel_pending)
        self.cancel.setEnabled(False);pl.addWidget(self.cancel)
        self.status=window._text('Hazır.');pl.addWidget(self.status)
        profiles.addWidget(pc)

    def section(self,key,title):
        scroll=QScrollArea();scroll.setWidgetResizable(True)
        content=QWidget();content.setObjectName('root');layout=QVBoxLayout(content)
        layout.setContentsMargins(7,15,7,15);layout.setSpacing(14)
        from PySide6.QtCore import Qt
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        scroll.setWidget(content);scroll.setProperty('rvc_tab_title',title)
        self.sections[key]=scroll;self.tabs.addTab(scroll,t(title))
        return layout

    def button(self,title,action,primary=False):
        button=QPushButton(t(title))
        if primary:button.setObjectName('primary')
        button.clicked.connect(action)
        return button

    def open_section(self,key):
        if key in self.sections:self.tabs.setCurrentWidget(self.sections[key])

    def language_changed(self,*_args):
        language=self.language.currentData()
        try:save_preferences(language=language)
        except OSError as exc:report(exc,'Dil ayarını kaydetme');return
        i18n.set_language(language,self.window.app)
        self.window._save_ui()

    def sync_engine_language(self,language):
        method=getattr(self.window.owner,'_studio_original_event_handler',None)
        function=getattr(method,'__func__',method)
        scope=getattr(function,'__globals__',{})
        engine_language=scope.get('i18n')
        if engine_language is not None and hasattr(engine_language,'language_map'):
            try:
                import json
                from tools.rvc_user_state import ROOT
                locale='tr_TR' if language=='tr' else 'en_US'
                engine_language.language=locale
                engine_language.language_map=json.loads((ROOT/'i18n/locale'/(locale+'.json')).read_text(encoding='utf-8-sig'))
            except (OSError,ValueError) as exc:report(exc,'Dil ayarını kaydetme')

    def error_options(self,value):
        try:save_preferences(error_dialogs=value)
        except OSError as exc:report(exc,'Hata bildirimi ayarı')

    def show_terms(self):
        from tools.rvc_beta_ui import TermsDialog
        dialog=TermsDialog(self.window.root)
        try:dialog.exec()
        finally:dialog.deleteLater()

    def open_profile_folder(self):
        path=data_root();path.mkdir(parents=True,exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def export_profiles(self):
        owner=self.window.owner
        try:
            owner.profile_save_values(self.window._values())
            if not owner.profiles:raise ValueError(t('Önce kütüphaneye bir ses modeli ekle.'))
            path,_=QFileDialog.getSaveFileName(self.window.root,t('Profil yedeğini kaydet'),'RVCStudio_profiles.rvcprofile',t('Profil yedeği (*.rvcprofile *.zip)'))
            if path:
                if not __import__('pathlib').Path(path).suffix:path+='.rvcprofile'
                self.window.profile_service.submit('export',rows=copy.deepcopy(owner.profiles),active=owner.active_profile_id,path=path,include_models=self.include_models.isChecked())
                self.status.setText(t('Profil dosya işlemi sürüyor…'))
        except Exception as exc:report(exc,'Profil dosya işlemi')

    def import_profiles(self):
        if self.window.owner.studio_tools.busy or self.window.owner.file_converter.busy:
            report(RuntimeError(t('Dönüştürme işlemi bitince profilleri içe aktarabilirsin.')),'Profil dosya işlemi');return
        path,_=QFileDialog.getOpenFileName(self.window.root,t('Profil yedeği seç'),'',t('Profil yedeği (*.rvcprofile *.zip)'))
        if not path:return
        answer=QMessageBox.question(self.window.root,t('Profilleri İçe Aktar'),
            t('Yalnız güvenilir kaynaklardan gelen modelleri içe aktar. Model dosyaları kod içerebilir.')+'\n\n'+t('Yedek kitaplığa birleştirilir; mevcut profiller silinmez.'),
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,QMessageBox.StandardButton.No)
        if answer!=QMessageBox.StandardButton.Yes:return
        try:self.window.profile_service.submit('import',path=path)
        except Exception as exc:report(exc,'Profil dosya işlemi')

    def tick(self):
        service=self.window.profile_service;owner=self.window.owner
        for _ in range(10):
            try:kind,value=service.messages.get_nowait()
            except queue.Empty:break
            job=value['job']
            if kind=='progress':
                self.progress.setValue(min(100,int(value['done']*100/max(1,value['total']))))
                self.status.setText(t('Kalıcı model kopyaları hazırlanıyor…' if job=='sync' else 'Profil dosya işlemi sürüyor…'))
            elif kind=='error':
                if isinstance(value['error'],InterruptedError):self.status.setText(t('İşlem iptal edildi.'))
                else:
                    report(value['error'],'Profil dosya işlemi')
                    self.status.setText(t('İşlem tamamlanamadı')+': '+t(str(value['error'])))
            elif kind=='done':
                if job=='sync':
                    changed=False
                    for result in value['result']:
                        row=next((row for row in owner.profiles if row['id']==result['id']),None)
                        if row is None or any(row.get(key+'_path','')!=path for key,path in result['source_paths'].items()):continue
                        for key in ('managed_models','runtime_models'):
                            if row.get(key)!=result[key]:row[key]=result[key];changed=True
                    if changed:
                        owner.profile_store()
                        current=owner.profile_current()
                        value=self.window._values().get('pth_path','')
                        if current and (not value or not __import__('pathlib').Path(value).is_file()):owner.profile_apply(current)
                    missing=sum(not row.get('managed_models',{}).get('pth') for row in owner.profiles)
                    if missing:
                        self.status.setText(t('Kalıcı kopyası eksik {0} model var; PTH yolunu yeniden bağla veya dosyaları içeren bir yedek aktar.').replace('{0}',str(missing)))
                    else:self.status.setText(t('Kalıcı kütüphane hazır · {0} profil.').replace('{0}',str(len(owner.profiles))))
                elif job=='export':
                    self.status.setText(t('Yedek kaydedildi: ')+value['result'])
                elif job=='import':
                    # Snapshot before merge; user's current active model keeps running.
                    backup=data_root()/'backups'/('profiles_before_import_'+__import__('uuid').uuid4().hex+'.json')
                    try:
                        atomic_json(backup,{'at':now(),'profiles':owner.profiles,'active_id':owner.active_profile_id})
                        merged=merge_profiles(owner.profiles,value['result'])
                        ids={row['id'] for row in merged}
                        restored=getattr(value['result'],'active_id',None)
                        active=owner.active_profile_id if owner.active_profile_id in ids else (restored if restored in ids else (merged[0]['id'] if merged else None))
                        from tools.rvc_profile_backup import store_database
                        store_database(owner.profile_path,merged,active)
                        owner.profiles=merged;owner.active_profile_id=active
                        owner.profile_labels_refresh()
                        if not self.window._values().get('pth_path') and owner.profile_current():owner.profile_apply(owner.profile_current())
                        service.queue_sync(owner.profiles)
                        self.status.setText(t('İçe aktarma tamamlandı: {0} profil.').replace('{0}',str(len(value['result']))))
                    except Exception as exc:report(exc,'Profil dosya işlemi')
                self.progress.setValue(100)
        self.export.setEnabled(not service.busy)
        self.import_button.setEnabled(not service.busy)
        self.cancel.setEnabled(service.busy or service.current=='sync')
