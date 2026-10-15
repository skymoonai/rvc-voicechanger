"""First-use beta/terms gate and nonblocking error notifications."""
from __future__ import annotations
import html
import json
from pathlib import Path
from PySide6.QtCore import Qt,QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication,QDialog,QVBoxLayout,QHBoxLayout,QLabel,QCheckBox,
    QPushButton,QComboBox,QTextBrowser,QMessageBox,QFileDialog)
from tools import rvc_i18n as i18n
from tools.rvc_i18n import t
from tools.rvc_user_state import terms,save_preferences,save_consent,data_root,APP_VERSION
from tools.rvc_errors import CENTER,report

def terms_html(language):
    sections=terms()[language]
    return ''.join('<h3>'+html.escape(title)+'</h3><p>'+html.escape(body)+'</p>' for title,body in sections)

class TermsDialog(QDialog):
    def __init__(self,parent=None,require_acceptance=False):
        super().__init__(parent)
        self.setProperty('studio_audio_editor',True)
        self.require_acceptance=require_acceptance
        self.setWindowTitle(t('Beta / Kullanım Koşulları'))
        self.resize(790,690)
        self.setMinimumSize(600,480)
        screen=QApplication.primaryScreen()
        if screen:self.resize(min(790,screen.availableGeometry().width()-40),min(690,screen.availableGeometry().height()-45))
        root=QVBoxLayout(self);root.setContentsMargins(24,22,24,20);root.setSpacing(13)
        row=QHBoxLayout();self.heading=QLabel('RVC Studio · Beta');self.heading.setObjectName('hero')
        row.addWidget(self.heading,1)
        self.language=QComboBox();self.language.setProperty('rvc_language_selector',True);self.language.addItem('Türkçe','tr');self.language.addItem('English','en')
        self.language.setCurrentIndex(0 if i18n.LANGUAGE=='tr' else 1);row.addWidget(self.language)
        root.addLayout(row)
        self.notice=QLabel();self.notice.setWordWrap(True);self.notice.setStyleSheet('color:#D4C8FF;padding:10px;background:#211D38;border-radius:8px')
        root.addWidget(self.notice)
        self.body=QTextBrowser();self.body.setOpenExternalLinks(False)
        self.body.setStyleSheet('QTextBrowser {background:#101627;color:#F1F4FF;border:1px solid #36405D;border-radius:8px;padding:8px;}')
        self.body.document().setDefaultStyleSheet('p {color:#D7DCEF;line-height:135%;} h3 {color:#FFFFFF;}')
        root.addWidget(self.body,1)
        self.beta_check=QCheckBox();self.accept_check=QCheckBox()
        if require_acceptance:
            root.addWidget(self.beta_check);root.addWidget(self.accept_check)
            self.beta_check.toggled.connect(self.ready);self.accept_check.toggled.connect(self.ready)
        self.version=QLabel(APP_VERSION+' · '+terms()['version']);root.addWidget(self.version)
        actions=QHBoxLayout();actions.addStretch()
        self.cancel=QPushButton();self.cancel.clicked.connect(self.reject);actions.addWidget(self.cancel)
        self.accept_button=QPushButton();self.accept_button.setObjectName('primary')
        self.accept_button.clicked.connect(self.confirm);actions.addWidget(self.accept_button)
        root.addLayout(actions)
        self.language.currentIndexChanged.connect(self.change_language)
        self.refresh();self.ready()

    def refresh(self):
        self.setWindowTitle(t('Beta / Kullanım Koşulları'))
        self.notice.setText(t('Beta uygulama ücretsizdir ve kâr amacı gütmez. Hatalar, donmalar ve çökmeler olabilir.'))
        self.body.setHtml(terms_html(i18n.LANGUAGE))
        self.beta_check.setText(t('Bu sürümün beta olduğunu ve hatalar / çökmeler olabileceğini anladım.'))
        self.accept_check.setText(t('Kullanım koşullarını okudum ve kabul ediyorum.'))
        self.cancel.setText(t('Uygulamadan Çık' if self.require_acceptance else 'Kapat'))
        self.accept_button.setVisible(self.require_acceptance)
        self.accept_button.setText(t('Kabul Et ve Devam Et'))

    def ready(self,*_args):
        self.accept_button.setEnabled(self.beta_check.isChecked() and self.accept_check.isChecked())

    def change_language(self,*_args):
        language=self.language.currentData()
        try:save_preferences(language=language)
        except OSError as exc:
            QMessageBox.warning(self,t('İşlem tamamlanamadı'),str(exc));return
        i18n.set_language(language,QApplication.instance());self.refresh()

    def confirm(self):
        if not self.accept_button.isEnabled():return
        try:save_consent(i18n.LANGUAGE)
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,t('İşlem tamamlanamadı'),str(exc));return
        self.accept()

def user_hint(item):
    text=item['message'].casefold();kind=item['type'].casefold()
    if 'out of memory' in text or 'cuda' in text and 'memory' in text or kind=='memoryerror':
        return t('GPU belleği yetersiz. Daha küçük bir model seç veya CPU ile tekrar dene.')
    if 'portaudio' in kind or 'device' in text and any(word in text for word in ('invalid','unavailable','error')):
        return t('Ses cihazına erişilemedi. Giriş ve çıkış seçimlerini kontrol edip tekrar dene.')
    if kind=='filenotfounderror':return t('Gerekli dosya bulunamadı. Model veya kayıt yolunu kontrol et.')
    if kind=='permissionerror':return t('Dosya / klasör izni yetersiz. Yazılabilir bir konum seç.')
    return t('Ayarları ve hata ayrıntılarını kontrol edip işlemi tekrar deneyebilirsin.')

def error_report_text(item):
    return json.dumps({'app':APP_VERSION,**item},ensure_ascii=False,indent=2)

def show_error(window,item):
    dialog=QMessageBox(window.root)
    dialog.setWindowTitle(t('İşlem tamamlanamadı'))
    dialog.setIcon(QMessageBox.Icon.Warning)
    dialog.setText(t(item['context'])+'\n'+user_hint(item))
    dialog.setInformativeText(t('Uygulama açık kaldı; hata raporu yerel kaydedildi.')+'\n'+t('Hata kimliği: {0}').replace('{0}',item['id']))
    dialog.setDetailedText(item['detail'])
    dialog.addButton(t('Tamam'),QMessageBox.ButtonRole.AcceptRole)
    copy_button=dialog.addButton(t('Raporu Kopyala'),QMessageBox.ButtonRole.ActionRole)
    settings_button=dialog.addButton(t('Ayarları Aç'),QMessageBox.ButtonRole.ActionRole)
    copy_button.clicked.connect(lambda:QApplication.clipboard().setText(error_report_text(item)))
    settings_button.clicked.connect(lambda:(dialog.accept(),window.open_settings('general')))
    window.error_dialog=dialog
    dialog.finished.connect(lambda _code:setattr(window,'error_dialog',None))
    dialog.finished.connect(dialog.deleteLater)
    dialog.open()

def save_error_report(parent,item=None):
    item=item or CENTER.last
    if not item:return
    name,_=QFileDialog.getSaveFileName(parent,t('Hata raporunu kaydet'),'RVCStudio_error_'+item['id']+'.json','JSON (*.json)')
    if not name:return
    try:Path(name).write_text(error_report_text(item),encoding='utf-8')
    except OSError as exc:report(exc,'Hata raporunu kaydetme')

def open_error_logs():
    path=data_root()/'logs';path.mkdir(parents=True,exist_ok=True)
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
