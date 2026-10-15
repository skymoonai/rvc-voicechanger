"""Selection editing UI; immutable audio views and off-thread file work."""
from __future__ import annotations
from tools.rvc_i18n import t as _tr
import copy
from dataclasses import replace
from pathlib import Path
import queue
import uuid
from PySide6.QtCore import Qt,QSignalBlocker,QTimer
from PySide6.QtGui import QAction,QKeySequence
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,QLabel,
    QPushButton,QSlider,QDoubleSpinBox,QDialogButtonBox,QMenu,QFileDialog,QInputDialog,QMessageBox,QScrollArea)
from tools.rvc_audio_files import RATE,AUDIO_FILTER,export_clip,import_audio
from tools.rvc_wave_edit import Audio,State,History,EditWorker,transform,render


class ClipEditor(QDialog):
    def __init__(self,page,entry):
        super().__init__(page)
        self.setProperty('studio_audio_editor',True)
        from tools.rvc_media_ui import Waveform,label,button
        self.page,self.engine,self.entry=page,page.engine,copy.deepcopy(entry)
        pcm_path=self.engine.pcm_path(entry)
        if pcm_path.stat().st_size!=entry['frames']*entry.get('channels',1)*4:
            raise ValueError('Klibin yönetilen ses kopyası eksik veya bozuk; özgün dosyayı tekrar ekle.')
        audio=Audio.source(pcm_path,entry['frames'],entry.get('channels',1)).slice(entry['start'],entry['end'])
        self.initial_audio=audio
        self.history=History(State(audio,0,audio.frames,entry['gain_db'],entry['fade_in'],entry['fade_out']))
        self.busy,self.cleaned=False,False
        self.view_entry=copy.deepcopy(entry)
        self.source_paths={Path(entry['source']).resolve()} if entry.get('source') else set()
        self._preview_start=0
        self.setWindowTitle(_tr('Soundpad · '+entry['name']))
        self.resize(980,740)
        self.setMinimumSize(740,540)
        root=QVBoxLayout(self)
        area=QScrollArea()
        area.setWidgetResizable(True)
        content=QWidget()
        content.setObjectName('root')
        layout=QVBoxLayout(content)
        layout.setSpacing(11)
        area.setWidget(content)
        root.addWidget(area,1)
        layout.addWidget(label('Ses Dalgası Düzenleyicisi','hero'))
        layout.addWidget(label('Alan seçip sağ tıkla. Kaydet bütün düzenlemeyi saklar; yalnız seçimi tutmak için “Seçili alan dışını sil” kullan. Kaynak dosya korunur.'))
        toolbar=QHBoxLayout()
        self.menu_button=button('Seçim İşlemleri ▾',lambda:self.open_menu())
        toolbar.addWidget(self.menu_button)
        self.undo_button=button('Geri Al',lambda:self.travel(-1))
        self.redo_button=button('Yinele',lambda:self.travel(1))
        toolbar.addWidget(self.undo_button)
        toolbar.addWidget(self.redo_button)
        toolbar.addStretch()
        self.restore_button=button('Özgün Sese Dön',self.restore_original)
        toolbar.addWidget(self.restore_button)
        layout.addLayout(toolbar)
        self.wave=Waveform(self.engine,entry)
        layout.addWidget(self.wave,1)
        zoom=QHBoxLayout()
        zoom.addWidget(QLabel(_tr('Yakınlaştır')))
        self.zoom=QSlider(Qt.Orientation.Horizontal)
        self.zoom.setRange(10,1000)
        self.zoom.setValue(10)
        self.zoom.valueChanged.connect(lambda value:self.wave.set_zoom(value/10))
        zoom.addWidget(self.zoom,1)
        self.zoom_label=QLabel(_tr('1.0×'))
        zoom.addWidget(self.zoom_label)
        zoom.addWidget(button('Seçime Yaklaş',self.zoom_selection))
        zoom.addWidget(button('Tüm Sesi Göster',lambda:self.wave.set_zoom(1)))
        layout.addLayout(zoom)
        self.pan=QSlider(Qt.Orientation.Horizontal)
        self.pan.setRange(0,10000)
        self.pan.valueChanged.connect(self.wave.set_pan)
        layout.addWidget(self.pan)
        self.wave.view_changed.connect(self.view_changed)
        self.start,self.end,self.gain,self.fade_in,self.fade_out=(QDoubleSpinBox() for _ in range(5))
        for spin in (self.start,self.end):
            spin.setDecimals(6)
            spin.setRange(0,audio.frames/RATE)
            spin.setSingleStep(.01)
            spin.setSuffix(' sn')
        self.gain.setRange(-60,18)
        self.gain.setSuffix(' dB')
        for spin in (self.fade_in,self.fade_out):
            spin.setRange(0,10)
            spin.setDecimals(3)
            spin.setSingleStep(.01)
            spin.setSuffix(' sn')
        self.start.setValue(0)
        self.end.setValue(audio.frames/RATE)
        self.gain.setValue(entry['gain_db'])
        self.fade_in.setValue(entry['fade_in'])
        self.fade_out.setValue(entry['fade_out'])
        grid=QGridLayout()
        for col,title,widget in ((0,'Seçim başlangıcı',self.start),(1,'Seçim bitişi',self.end),
                (2,'Klip ses seviyesi',self.gain),(3,'Klip başını yumuşat',self.fade_in),(4,'Klip sonunu yumuşat',self.fade_out)):
            grid.addWidget(label(title),0,col)
            grid.addWidget(widget,1,col)
        layout.addLayout(grid)
        row=QHBoxLayout()
        self.preview_button=button('Seçimi Kulaklıkta Dinle',self.preview)
        row.addWidget(self.preview_button)
        row.addWidget(button('Duraklat / Devam Et',self.engine.pause))
        row.addWidget(button('Durdur',self.engine.stop_preview))
        row.addStretch()
        self.normalize_button=button('Seçimi Dengele',lambda:self.edit('normalize'))
        row.addWidget(self.normalize_button)
        self.select_button=button('Tümünü Seç',self.select_all)
        row.addWidget(self.select_button)
        layout.addLayout(row)
        self.info=label('')
        root.addWidget(self.info)
        self.box=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        self.box.button(QDialogButtonBox.StandardButton.Save).setText(_tr('Düzenlemeyi Kaydet'))
        self.box.button(QDialogButtonBox.StandardButton.Save).setObjectName('primary')
        self.box.button(QDialogButtonBox.StandardButton.Cancel).setText(_tr('Vazgeç'))
        self.box.accepted.connect(self.commit)
        self.box.rejected.connect(self.reject)
        root.addWidget(self.box)
        self.actions={}
        definitions=[
            ('undo','Geri al',lambda:self.travel(-1),'Ctrl+Z'),
            ('redo','Yinele',lambda:self.travel(1),'Ctrl+Shift+Z'),
            ('cut','Seçili alanı kes',lambda:self.copy_selection(True),'Ctrl+X'),
            ('copy','Seçili alanı kopyala',self.copy_selection,'Ctrl+C'),
            ('paste','Seçim yerine yapıştır',lambda:self.paste('paste'),'Ctrl+V'),
            ('paste_before','Seçimin başına yapıştır',lambda:self.paste('paste_before'),''),
            ('paste_after','Seçimin sonuna yapıştır',lambda:self.paste('paste_after'),''),
            ('delete','Seçili alanı sil',lambda:self.edit('delete'),'Delete'),
            ('keep','Seçili alan dışını sil',lambda:self.edit('keep'),'Ctrl+Shift+K'),
            ('export','Seçili alanı farklı kaydet…',self.export_selection,'Ctrl+Shift+E'),
            ('new','Seçili alanı yeni klip olarak içe aktar…',self.new_clip,'Ctrl+Shift+N'),
            ('duplicate','Seçili alanı çoğalt',lambda:self.edit('duplicate'),'Ctrl+D'),
            ('mute','Seçili alanı sustur · süreyi koru',lambda:self.edit('mute'),''),
            ('reverse','Seçili alanı ters çevir',lambda:self.edit('reverse'),''),
            ('gain','Seçimin ses seviyesini değiştir…',self.selection_gain,''),
            ('normalize','Seçimi dengele · tepe −1 dBFS',lambda:self.edit('normalize'),''),
            ('fade_in','Seçim başını yumuşat…',lambda:self.selection_fade('in'),''),
            ('fade_out','Seçim sonunu yumuşat…',lambda:self.selection_fade('out'),''),
            ('dc','Seçimdeki DC kaymasını gider',lambda:self.edit('dc'),''),
            ('silence','Seçimin başına sessizlik ekle…',self.insert_silence,''),
            ('insert','Dosyadan seçim yerine ses ekle…',self.insert_file,''),
            ('trim_silence','Baştaki / sondaki sessizliği kırp…',self.trim_silence,''),
            ('select_all','Tümünü seç',self.select_all,'Ctrl+A'),
            ('zoom','Seçime yakınlaş',self.zoom_selection,''),
            ('restore','Özgün sese dön',self.restore_original,'')]
        for key,title,callback,shortcut in definitions:
            action=QAction(_tr(title),self)
            action.triggered.connect(lambda _checked=False,func=callback:func())
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
                if key=='redo':action.setShortcuts([QKeySequence(shortcut),QKeySequence('Ctrl+Y')])
                action.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
                self.wave.addAction(action)
            self.actions[key]=action
        self.wave.context_requested.connect(self.open_menu)
        self.wave.selection_changed.connect(self.selection_changed)
        self.start.valueChanged.connect(self.range_changed)
        self.end.valueChanged.connect(self.range_changed)
        for widget in (self.gain,self.fade_in,self.fade_out):widget.valueChanged.connect(self.metadata_changed)
        self.worker=EditWorker(self.engine.editor_root())
        self.engine._edit_workers.add(self.worker)
        self.worker.pending_paths=set()
        self.finished.connect(self.finish_preview)
        self.timer=QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(60)
        self.apply_state(self.history.current)
        if entry['start'] or entry['end']!=entry['frames']:
            self.run('initial',lambda:self.render_state(self.history.current))

    def show_error(self,exc):
        QMessageBox.warning(self,_tr('Soundpad'),_tr(str(exc)))

    def selection_changed(self,start,end):
        with QSignalBlocker(self.start),QSignalBlocker(self.end):
            self.start.setValue(start);self.end.setValue(end)
        self.range_changed()

    def range_changed(self,*_args):
        state=self.history.current
        start=min(state.audio.frames,max(0,round(self.start.value()*RATE)))
        end=min(state.audio.frames,max(start,round(self.end.value()*RATE)))
        if round(self.end.value()*RATE)!=end:
            with QSignalBlocker(self.end):self.end.setValue(end/RATE)
        self.history.selection(start,end)
        self.wave.set_selection(start/RATE,end/RATE)
        self.refresh_actions()
        if not self.busy:
            self.info.setText(_tr(f'Seçim: {(end-start)/RATE:.3f} sn · klip: {state.audio.frames/RATE:.3f} sn · {state.audio.channels} kanal · Kaydet bütün klibi saklar.'))

    def metadata_changed(self,*_args):
        if self.busy:return
        state=replace(self.history.current,gain_db=self.gain.value(),fade_in=self.fade_in.value(),fade_out=self.fade_out.value())
        if state!=self.history.current:self.history.push(state)
        self.wave.gain=10**(state.gain_db/20)
        self.wave.update()
        self.refresh_actions()

    def apply_state(self,state,view=None):
        if view is not None:
            old=self.view_entry.get('_preview_path')
            self.engine.stop_preview()
            self.view_entry=view
            self.wave.set_entry(view)
            self.worker.retire(old)
        blockers=[QSignalBlocker(widget) for widget in (self.start,self.end,self.gain,self.fade_in,self.fade_out)]
        for widget in (self.start,self.end):widget.setRange(0,state.audio.frames/RATE)
        self.start.setValue(state.start/RATE);self.end.setValue(state.end/RATE)
        self.gain.setValue(state.gain_db);self.fade_in.setValue(state.fade_in);self.fade_out.setValue(state.fade_out)
        del blockers
        self.wave.gain=10**(state.gain_db/20)
        self.view_changed()
        self.range_changed()
        self.wave.update()

    def refresh_actions(self):
        state=self.history.current
        selected=state.end>state.start
        for key,action in self.actions.items():action.setEnabled(not self.busy and selected)
        can_delete=selected and state.end-state.start<state.audio.frames
        for key in ('delete','cut','keep'):self.actions[key].setEnabled(not self.busy and can_delete)
        self.undo_button.setEnabled(not self.busy and self.history.index>0)
        self.redo_button.setEnabled(not self.busy and self.history.index<len(self.history.states)-1)
        self.actions['undo'].setEnabled(self.undo_button.isEnabled())
        self.actions['redo'].setEnabled(self.redo_button.isEnabled())
        for key in ('paste','paste_before','paste_after'):
            self.actions[key].setEnabled(not self.busy and self.engine.editor_clipboard is not None)
        for key in ('silence','insert','trim_silence','select_all','restore'):
            self.actions[key].setEnabled(not self.busy)
        for widget in (self.wave,self.start,self.end,self.gain,self.fade_in,self.fade_out,self.preview_button,
                self.normalize_button,self.select_button,self.menu_button,self.restore_button):widget.setEnabled(not self.busy)
        self.preview_button.setEnabled(not self.busy and selected)
        self.normalize_button.setEnabled(not self.busy and selected)
        self.box.button(QDialogButtonBox.StandardButton.Save).setEnabled(not self.busy)

    def open_menu(self,point=None):
        if self.busy:return
        self.refresh_actions()
        menu=QMenu(self)
        for group in (('undo','redo'),('cut','copy','paste'),('delete','keep','export','new')):
            if menu.actions():menu.addSeparator()
            for key in group:menu.addAction(self.actions[key])
        paste=menu.addMenu(_tr('Seçimi koruyarak yapıştır'))
        for key in ('paste_before','paste_after'):paste.addAction(self.actions[key])
        menu.addSeparator()
        effects=menu.addMenu(_tr('Seçime ses işlemi uygula'))
        for key in ('duplicate','mute','reverse','gain','normalize','fade_in','fade_out','dc'):
            effects.addAction(self.actions[key])
        more=menu.addMenu(_tr('Ses ekle / sessizliği düzenle'))
        for key in ('silence','insert','trim_silence'):more.addAction(self.actions[key])
        menu.addSeparator()
        for key in ('select_all','zoom','restore'):menu.addAction(self.actions[key])
        try:
            menu.exec(self.wave.mapToGlobal(point) if point is not None else self.menu_button.mapToGlobal(self.menu_button.rect().bottomLeft()))
        finally:menu.deleteLater()

    def view_changed(self):
        with QSignalBlocker(self.zoom):self.zoom.setValue(round(self.wave.zoom*10))
        with QSignalBlocker(self.pan):self.pan.setValue(round(10000*self.wave.offset/max(1e-9,self.wave.total-self.wave.span)))
        self.pan.setEnabled(self.wave.zoom>1)
        self.zoom_label.setText(_tr(f'{self.wave.zoom:.1f}×'))

    def select_all(self):self.selection_changed(0,self.history.current.audio.frames/RATE)

    def zoom_selection(self):
        state=self.history.current
        if state.end<=state.start:return
        self.wave.set_zoom(min(100,state.audio.frames/(state.end-state.start)))
        self.wave.offset=min(state.start/RATE,max(0,self.wave.total-self.wave.span))
        self.view_changed();self.wave.update()

    def run(self,kind,func):
        if self.busy or self.cleaned:return
        self._return_wave_focus=self.wave.hasFocus()
        self.engine.stop_preview()
        self.busy=True
        self.info.setText(_tr('Ses hazırlanıyor… İşlem arka planda çalışıyor; Vazgeç ile iptal edebilirsin.'))
        self.refresh_actions()
        self.worker.submit(kind,func)

    def render_state(self,state):
        path=self.worker.directory/(uuid.uuid4().hex+'.f32')
        meta=render(state.audio,path,self.worker.cancel)
        return state,dict(self.entry,**meta,start=0,end=meta['frames'],_preview_path=str(path))

    def edit(self,operation,**options):
        if self.busy:return
        state=self.history.current
        def work():return self.render_state(transform(state,operation,self.worker.cancel,**options))
        self.run('edit',work)

    def travel(self,delta):
        index=self.history.index+delta
        if self.busy or not 0<=index<len(self.history.states):return
        state=self.history.states[index]
        if state.audio==self.history.current.audio:
            self.history.index=index
            self.apply_state(state)
        else:
            self.run('travel',lambda:(index,*self.render_state(state)))

    def restore_original(self):
        origin=self.entry.get('original_audio') or self.engine.audio_snapshot(self.entry)
        path=self.engine.folder/(origin['pcm_id']+'.f32')
        audio=Audio.source(path,origin['frames'],origin['channels'])
        state=State(audio,0,audio.frames,origin['gain_db'],origin['fade_in'],origin['fade_out'])
        self.run('edit',lambda:self.render_state(state))

    def freeze_clipboard(self,state):
        path=self.engine.editor_root()/(uuid.uuid4().hex+'.f32')
        audio=state.selected()
        meta=render(audio,path,self.worker.cancel)
        return Audio.source(path,meta['frames'],meta['channels'])

    def copy_selection(self,cut=False):
        state=self.history.current
        def work():
            clipboard=self.freeze_clipboard(state)
            return (clipboard,*self.render_state(transform(state,'cut',self.worker.cancel))) if cut else clipboard
        self.run('cut' if cut else 'copy',work)

    def paste(self,operation='paste'):
        if self.engine.editor_clipboard is not None:self.edit(operation,audio=self.engine.editor_clipboard)

    def selection_gain(self):
        value,ok=QInputDialog.getDouble(self,_tr('Seçimin ses seviyesi'),_tr('Seçili alana uygulanacak değişim (dB):'),6,-60,36,1)
        if ok:self.edit('gain',db=value)

    def selection_fade(self,direction):
        span=(self.history.current.end-self.history.current.start)/RATE
        if span<=0:return
        value,ok=QInputDialog.getDouble(self,_tr('Seçimi yumuşat'),_tr('Yumuşatma süresi (saniye):'),min(.05,span),min(.000042,span),span,6)
        if ok:self.edit('fade',direction=direction,seconds=value)

    def insert_silence(self):
        value,ok=QInputDialog.getDouble(self,_tr('Sessizlik ekle'),_tr('Eklenecek süre (saniye):'),.25,.001,300,3)
        if ok:self.edit('silence',seconds=value)

    def trim_silence(self):
        value,ok=QInputDialog.getDouble(self,_tr('Uçlardaki sessizliği kırp'),_tr('Sessizlik eşiği (dBFS):'),-50,-90,-10,1)
        if ok:self.edit('trim_silence',threshold=value)

    def insert_file(self):
        path,_=QFileDialog.getOpenFileName(self,_tr('Seçim yerine ses dosyası ekle'),'',_tr(AUDIO_FILTER))
        if not path:return
        self.source_paths.add(Path(path).resolve())
        state=self.history.current
        def work():
            target=self.engine.editor_root()/(uuid.uuid4().hex+'.f32')
            meta=import_audio(path,target,self.worker.cancel)
            audio=Audio.source(target,meta['frames'],meta['channels'])
            return self.render_state(transform(state,'paste',self.worker.cancel,audio=audio))
        self.run('edit',work)

    def selected_entry(self):
        state=self.history.current
        state.selected()
        return dict(self.view_entry,start=state.start,end=state.end,gain_db=state.gain_db,
                    fade_in=state.fade_in,fade_out=state.fade_out,cursor=0,route='head',loop=False)

    def preview(self):
        try:
            entry=self.selected_entry()
            self._preview_start=entry['start']/RATE
            self.engine.audition(entry)
            self.engine.sync_devices()
        except Exception as exc:self.show_error(exc)

    def export_selection(self):
        try:entry=self.selected_entry()
        except Exception as exc:self.show_error(exc);return
        target,_=QFileDialog.getSaveFileName(self,_tr('Seçili alanı farklı kaydet'),self.entry['name']+' - seçim.wav',_tr('WAV (*.wav)'))
        if not target:return
        target=Path(target)
        if not target.suffix:target=target.with_suffix('.wav')
        if target.suffix.casefold()!='.wav':
            self.show_error('WAV dışa aktarımı için .wav uzantısı kullan.');return
        if target.resolve() in self.source_paths or target.resolve().is_relative_to(self.engine.folder.resolve()) or target.resolve().is_relative_to(self.engine.editor_root().resolve()):
            self.show_error('Kaynak dosyayı ve Soundpad ses kopyalarını korumak için farklı bir konum / ad seç.');return
        pcm=self.engine.pcm_path(entry)
        entry['protect_peaks']=self.engine.settings['protect_peaks']
        library_sources=tuple(item['source'] for item in self.engine.entries if item.get('source'))
        def work():
            destination=target.resolve()
            for source in library_sources:
                if self.worker.cancel.is_set():raise InterruptedError('Dışa aktarma iptal edildi.')
                if destination==Path(source).resolve():
                    raise ValueError('Kaynak dosyayı korumak için farklı bir dosya adı seç.')
            export_clip(entry,pcm,target,self.worker.cancel)
            return str(target)
        self.run('export',work)

    def publish_audio(self,audio):
        identifier=uuid.uuid4().hex
        path=self.engine.folder/(identifier+'.f32')
        self.worker.pending_paths.add(path)
        meta=render(audio,path,self.worker.cancel)
        return identifier,meta

    def new_clip(self):
        state=self.history.current
        try:audio=state.playable_selection()
        except Exception as exc:self.show_error(exc);return
        name,ok=QInputDialog.getText(self,_tr('Seçimi Soundpad’e ekle'),_tr('Yeni klip adı:'),text=self.entry['name']+' - seçim')
        if ok and name.strip():self.run('new',lambda:(*self.publish_audio(audio),name.strip()))

    def commit(self):
        if self.busy:return
        state=self.history.current
        values=dict(gain_db=state.gain_db,fade_in=state.fade_in,fade_out=state.fade_out,cursor=0)
        if state.audio==self.initial_audio:
            try:
                self.engine.update(self.entry['id'],**values)
                self.page.rebuild();self.accept()
            except Exception as exc:self.show_error(exc)
        else:self.run('save',lambda:(*self.publish_audio(state.audio),values))

    def tick(self):
        if self.cleaned:return
        while True:
            try:kind,result,problem=self.worker.messages.get_nowait()
            except queue.Empty:break
            if kind=='retire':continue
            self.busy=False
            if problem:
                self.refresh_actions()
                if self._return_wave_focus:self.wave.setFocus()
                self.info.setText(_tr('İşlem tamamlanamadı: '+problem))
                continue
            try:
                if kind in ('edit','initial','travel','cut'):
                    if kind=='cut':
                        clipboard,state,view=result
                        self.engine.editor_clipboard=clipboard
                    elif kind=='travel':
                        index,state,view=result
                        self.history.index=index
                    else:state,view=result
                    if kind in ('edit','cut'):self.history.push(state)
                    self.apply_state(state,view)
                elif kind=='copy':
                    self.engine.editor_clipboard=result
                    self.info.setText(_tr('Seçim kopyalandı. Başka bir klibin düzenleyicisinde de yapıştırabilirsin.'))
                elif kind=='export':self.info.setText(_tr('Seçim WAV olarak kaydedildi: '+result))
                elif kind=='new':
                    identifier,meta,name=result
                    self.engine.accept_new_clip(identifier,meta,name,self.entry['source'],self.entry['route'])
                    self.worker.pending_paths.discard(self.engine.folder/(identifier+'.f32'))
                    self.page.rebuild()
                    self.info.setText(_tr('Seçim yeni klip olarak eklendi: '+name))
                elif kind=='save':
                    identifier,meta,values=result
                    self.engine.accept_edit(self.entry['id'],identifier,meta,values)
                    self.worker.pending_paths.discard(self.engine.folder/(identifier+'.f32'))
                    self.page.rebuild();self.accept();return
            except Exception as exc:
                self.info.setText(_tr('Kaydedilemedi: '+str(exc)))
            self.refresh_actions()
            if self._return_wave_focus:self.wave.setFocus()
        state=self.engine.state()
        self.wave.playhead=self._preview_start+state['position'] if state and state['id']==self.entry['id'] else None
        self.wave.update()

    def finish_preview(self,*_args):
        if self.cleaned:return
        self.cleaned=True
        self.timer.stop()
        self.engine.stop_preview()
        if self.wave.raw is not None:
            self.wave.raw._mmap.close();self.wave.raw=None
        self.worker.close()
        self.engine._edit_workers.discard(self.worker)
        self.engine.release_editor_cache()
