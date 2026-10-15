"""Local TR/EN catalog. Translate display text, keep model/device data and tab IDs stable."""
from __future__ import annotations
from functools import lru_cache
import json
import re
from pathlib import Path
from tools.rvc_user_state import preferences

LANGUAGE=preferences()['language']
_catalog=None
_reverse=None
_patterns=[]
_translator=None

def _load():
    global _catalog,_reverse,_patterns
    if _catalog is not None:return
    path=Path(__file__).resolve().parent.parent/'assets'/'locales'/'en.json'
    _catalog={}
    for item in sorted(path.parent.glob('en*.json')):_catalog.update(json.loads(item.read_text(encoding='utf-8')))
    _reverse={value:key for key,value in _catalog.items()}
    for source,target in _catalog.items():
        if re.search(r'\{\d+\}',source):
            def pattern(value):
                if value in ('{0} ses','{0} clips'):
                    return re.compile(r'^(\d+) '+('ses' if value.endswith('ses') else 'clips')+'$'),['{0}']
                parts=re.split(r'(\{\d+\})',value)
                tokens=[part for part in parts if re.fullmatch(r'\{\d+\}',part)]
                return re.compile('^'+''.join('(.*?)' if re.fullmatch(r'\{\d+\}',part) else re.escape(part) for part in parts)+'$',re.S),tokens
            _patterns.append((source,target,pattern(source),pattern(target)))

def _expand(template,tokens,values):
    result=template
    for key,value in zip(tokens,values):result=result.replace(key,value)
    return result

@lru_cache(maxsize=512)
def _translate(text,language):
    _load()
    if language=='en' and text in _catalog:return _catalog[text]
    if language=='tr' and text in _reverse:return _reverse[text]
    for source,target,tr_pattern,en_pattern in _patterns:
        expression,tokens=tr_pattern if language=='en' else en_pattern
        match=expression.fullmatch(text)
        if match:return _expand(target if language=='en' else source,tokens,match.groups())
    # Stable sentence fragments are explicitly opted in by the catalog.
    fragments=[(a,b) for a,b in (_catalog.items() if language=='en' else _reverse.items())
               if a.endswith((': ',' · ')) and len(a)>3 and '{' not in a]
    for source,target in sorted(fragments,key=lambda pair:-len(pair[0])):
        if text.startswith(source):return target+_translate(text[len(source):],language)
    pairs=_catalog.items() if language=='en' else _reverse.items()
    for source,target in pairs:
        if len(source)>8 and (source.startswith((' · ','\n','  ·  ')) or source.startswith(' ') and source.endswith('…')):
            text=text.replace(source,target)
    return text

def t(text,language=None):
    value=str(text)
    if len(value)>24000:return value
    return _translate(value,language or LANGUAGE)

def set_language(language,app=None):
    global LANGUAGE,_translator
    if language not in ('tr','en'):raise ValueError('Unsupported language')
    LANGUAGE=language
    if app is None:return
    from PySide6.QtCore import QLocale,QTranslator,QLibraryInfo
    QLocale.setDefault(QLocale('tr_TR' if language=='tr' else 'en_US'))
    if _translator is not None:app.removeTranslator(_translator);_translator=None
    if language=='tr':
        translator=QTranslator(app)
        if translator.load('qtbase_tr',QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)):
            app.installTranslator(translator);_translator=translator
    for widget in app.topLevelWidgets():localize_tree(widget)
    reference=getattr(app,'_rvc_settings_reference',None)
    settings=reference() if reference else None
    if settings is not None:settings.sync_engine_language(language)

def _property(widget,name,current,setter):
    source=widget.property('_rvc_source_'+name)
    last=widget.property('_rvc_last_'+name)
    if source is None or current!=last:
        source=t(current,'tr')
        widget.setProperty('_rvc_source_'+name,source)
    translated=t(source)
    if current!=translated:setter(translated)
    widget.setProperty('_rvc_last_'+name,translated)

def localize_tree(root):
    from PySide6.QtCore import QSignalBlocker
    from PySide6.QtWidgets import QWidget,QLabel,QAbstractButton,QComboBox,QLineEdit,QTabWidget,QGroupBox,QMessageBox,QMenu
    widgets=[root]+root.findChildren(QWidget)
    for widget in widgets:
        if widget.property('rvc_i18n_skip'):continue
        if isinstance(widget,(QLabel,QAbstractButton)):
            _property(widget,'text',widget.text(),widget.setText)
        if widget.windowTitle():_property(widget,'title',widget.windowTitle(),widget.setWindowTitle)
        if widget.toolTip():_property(widget,'tip',widget.toolTip(),widget.setToolTip)
        if isinstance(widget,QLineEdit) and widget.placeholderText():
            _property(widget,'placeholder',widget.placeholderText(),widget.setPlaceholderText)
        if isinstance(widget,QGroupBox):_property(widget,'group',widget.title(),widget.setTitle)
        if isinstance(widget,QMessageBox):
            _property(widget,'message',widget.text(),widget.setText)
            if widget.informativeText():_property(widget,'info',widget.informativeText(),widget.setInformativeText)
        if isinstance(widget,QComboBox) and not widget.property('rvc_i18n_device'):
            with QSignalBlocker(widget):
                for index in range(widget.count()):
                    source=widget.itemData(index,257)
                    if source is None:
                        source=t(widget.itemText(index),'tr');widget.setItemData(index,source,257)
                        if widget.itemData(index) is None:widget.setItemData(index,source)
                    widget.setItemText(index,t(source))
                if widget.property('rvc_language_selector'):
                    selected=widget.findData(LANGUAGE)
                    if selected>=0:widget.setCurrentIndex(selected)
        if isinstance(widget,QTabWidget):
            for index in range(widget.count()):
                source=widget.widget(index).property('rvc_tab_title')
                if source is None:
                    source=t(widget.tabText(index),'tr');widget.widget(index).setProperty('rvc_tab_title',source)
                widget.setTabText(index,t(source))
        if isinstance(widget,QMenu):
            for action in widget.actions():
                if action.text():_property(action,'action',action.text(),action.setText)

def install(app):
    if hasattr(app,'_rvc_localizer'):return
    from PySide6.QtCore import QObject,QEvent,QTimer
    from PySide6.QtWidgets import QDialog,QMenu
    class Localizer(QObject):
        def eventFilter(self,watched,event):
            if event.type()==QEvent.Type.Show and isinstance(watched,(QDialog,QMenu)):
                QTimer.singleShot(0,watched,lambda:localize_tree(watched))
            return False
    app._rvc_localizer=Localizer(app)
    app.installEventFilter(app._rvc_localizer)
    set_language(LANGUAGE,app)
