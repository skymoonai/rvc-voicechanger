"""RNNoise controls, drag/drop conversion and a nondestructive Soundpad editor."""
from __future__ import annotations
from tools.rvc_i18n import t as _tr
import copy
from pathlib import Path
import numpy as np
from PySide6.QtCore import Qt, Signal, QSignalBlocker, QTimer, QUrl
from PySide6.QtGui import QColor, QPainter, QPen, QKeySequence, QDesktopServices
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton, QLineEdit,
    QListWidget, QListWidgetItem, QFileDialog, QComboBox, QSlider, QCheckBox,
    QDialog, QDialogButtonBox, QDoubleSpinBox, QSpinBox, QMenu, QMessageBox,
    QProgressBar, QInputDialog,
)
from tools.rvc_audio_files import AUDIO_FILTER, RATE
from tools.rvc_soundpad import ROUTES


def label(text, role='subtitle'):
    widget = QLabel(_tr(text))
    widget.setObjectName(role)
    widget.setWordWrap(True)
    return widget


def button(text, action, primary=False):
    widget = QPushButton(_tr(text))
    if primary:
        widget.setObjectName('primary')
    widget.clicked.connect(action)
    return widget


def duration(seconds):
    seconds = max(0, float(seconds))
    return f'{int(seconds)//60:02d}:{seconds%60:05.2f}'


def error(parent, exc):
    QMessageBox.warning(parent, _tr('RVC Studio'), _tr(str(exc)))


class DropList(QListWidget):
    files_dropped = Signal(list)
    def __init__(self, hint, parent=None):
        super().__init__(parent)
        self.hint = _tr(hint)
        self.setAcceptDrops(True)
        self.setDragDropMode(QListWidget.DragDropMode.DropOnly)
        self.setMinimumHeight(190)
        self.setAlternatingRowColors(False)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        files = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile() and Path(url.toLocalFile()).is_file()]
        if files:
            self.files_dropped.emit(files)
            event.acceptProposedAction()

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.count():
            painter = QPainter(self.viewport())
            painter.setPen(QColor('#9DA8C4'))
            painter.drawText(self.viewport().rect().adjusted(20,20,-20,-20), Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap, self.hint)


class APBWEPanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.engine = window, window.owner.apbwe
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        self.enabled = QCheckBox(_tr('Canlı RVC sesinde AP-BWE’yi etkinleştir'))
        self.enabled.setChecked(self.engine.settings['enabled'])
        layout.addWidget(self.enabled)
        row = QHBoxLayout()
        row.addWidget(label('İyileştirme miktarı'))
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(0,100)
        self.strength.setValue(self.engine.settings['strength'])
        row.addWidget(self.strength,1)
        self.amount = QLabel()
        row.addWidget(self.amount)
        layout.addLayout(row)
        controls = QHBoxLayout()
        self.prepare_button = button('Modeli Hazırla / Yeniden Dene',self.prepare)
        controls.addWidget(self.prepare_button)
        self.cancel_button = button('Hazırlığı İptal Et',self.engine.cancel.set)
        controls.addWidget(self.cancel_button)
        controls.addStretch()
        layout.addLayout(controls)
        self.status = label('')
        layout.addWidget(self.status)
        layout.addWidget(label('Yalnızca dönüştürülmüş mikrofon sesine uygulanır. Normal mikrofon ve Soundpad etkilenmez. Model hazırlığı arka planda yapılır; ses yetişmezse normal RVC’ye geçilir.'))
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save)
        self.enabled.toggled.connect(self.save)
        self.strength.valueChanged.connect(lambda _v:self.save_timer.start(150))
        self.strength.valueChanged.connect(self.refresh)
        self.refresh()

    def save(self, *_args):
        try:
            self.engine.configure(enabled=self.enabled.isChecked(),strength=self.strength.value())
        except Exception as exc:
            self.status.setText(_tr('Ayar kaydedilemedi: '+str(exc)))

    def prepare(self):
        self.engine.prepare()
        self.refresh()

    def refresh(self, *_args):
        self.amount.setText(_tr(f'%{self.strength.value()}'))
        self.prepare_button.setEnabled(not self.engine.busy)
        self.cancel_button.setEnabled(self.engine.busy)
        text = self.engine.status
        if not self.engine.settings['enabled'] and not self.engine.busy:
            text = 'Kapalı · '+('Model hazır.' if self.engine.runtime is not None else 'İlk hazırlıkta 114 MB model indirilir.')
        elif self.engine.settings['enabled'] and self.engine.settings['strength'] == 0:
            text = 'Etki %0 · normal RVC sesi.'
        if self.engine.error:
            text += ' '+self.engine.error
        self.status.setText(_tr(text))


class NoisePanel(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window, self.engine = window, window.owner.noise_filter
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0,0,0,0)
        layout.addWidget(label('RNNoise · Akıllı Mikrofon Temizliği', 'section'))
        layout.addWidget(label('Fan, klavye ve sürekli arka plan gürültüsünü konuşmadan ayırır. Normal mikrofon ve ses dönüşümünde girişe uygulanır; Soundpad kliplerini değiştirmez.'))
        self.enabled = QCheckBox(_tr('RNNoise’u etkinleştir'))
        self.enabled.setChecked(bool(self.engine.settings['enabled']))
        layout.addWidget(self.enabled)
        row = QHBoxLayout()
        row.addWidget(label('Temizlenmiş ses oranı'))
        self.strength = QSlider(Qt.Orientation.Horizontal)
        self.strength.setRange(0,100)
        self.strength.setValue(int(self.engine.settings['strength']))
        row.addWidget(self.strength,1)
        self.strength_label = QLabel()
        row.addWidget(self.strength_label)
        layout.addLayout(row)
        self.gate = QCheckBox(_tr('Konuşma yokken mikrofonu sessizleştir'))
        self.gate.setChecked(bool(self.engine.settings['gate']))
        layout.addWidget(self.gate)
        advanced = QHBoxLayout()
        advanced.addWidget(label('Konuşma eşiği'))
        self.threshold = QSpinBox()
        self.threshold.setRange(1,90)
        self.threshold.setSuffix(' %')
        self.threshold.setValue(int(self.engine.settings['threshold']))
        advanced.addWidget(self.threshold)
        advanced.addWidget(label('Cümle sonunu koru'))
        self.hold = QSpinBox()
        self.hold.setRange(50,2000)
        self.hold.setSingleStep(50)
        self.hold.setSuffix(' ms')
        self.hold.setValue(int(self.engine.settings['hold_ms']))
        advanced.addWidget(self.hold)
        advanced.addStretch()
        advanced.addWidget(button('Filtreyi Sıfırla', self.engine.reset))
        layout.addLayout(advanced)
        self.status = label('')
        layout.addWidget(self.status)
        layout.addWidget(label('CPU üzerinde yerel çalışır. Yaklaşık 30 ms ek tampon gecikmesi getirir. Konuşma kapısı varsayılan olarak kapalıdır; çok yüksek eşik sessiz konuşmaları kesebilir.'))
        self.save_timer = QTimer(self)
        self.save_timer.setSingleShot(True)
        self.save_timer.timeout.connect(self.save)
        for widget in (self.enabled,self.gate):
            widget.toggled.connect(self.save)
        for widget in (self.threshold,self.hold):
            widget.valueChanged.connect(lambda _v: self.save_timer.start(150))
        self.strength.valueChanged.connect(lambda _v: self.save_timer.start(150))
        self.strength.valueChanged.connect(self.refresh)
        self.refresh()

    def save(self, *_args):
        try:
            self.engine.configure(enabled=self.enabled.isChecked(), strength=self.strength.value(),
                                  gate=self.gate.isChecked(), threshold=self.threshold.value(), hold_ms=self.hold.value())
        except Exception as exc:
            with QSignalBlocker(self.enabled):
                self.enabled.setChecked(bool(self.engine.settings['enabled']))
            self.status.setText(_tr(str(exc)))
            error(self, exc)

    def refresh(self, *_args):
        self.strength_label.setText(_tr(f'%{self.strength.value()}'))
        self.threshold.setEnabled(self.gate.isChecked())
        self.hold.setEnabled(self.gate.isChecked())
        if self.engine.error:
            self.status.setText(_tr('Filtre hatası: '+self.engine.error+' · Mikrofon sesi kesilmeden devam ediyor.'))
        elif self.engine.settings['enabled']:
            self.status.setText(_tr(f"Etkin · konuşma olasılığı %{self.engine.speech*100:.0f} · azaltma {self.engine.reduction_db:.1f} dB"))
        else:
            self.status.setText(_tr('Kapalı · ayarlar otomatik kaydedilir. Ayarlar > Kısayollar bölümünden aç/kapat tuşu atayabilirsin.'))

    def toggle(self):
        self.enabled.setChecked(not self.enabled.isChecked())


class DownloadDialog(QDialog):
    def __init__(self,page):
        super().__init__(page)
        self.page,self.engine = page,page.engine.downloader
        self.setWindowTitle(_tr('Soundpad · Linkten İndir'))
        self.resize(760,480)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.addWidget(label('Linkten Ses Ekle','hero'))
        layout.addWidget(label('YouTube ve SoundCloud’dan sesi indirip Soundpad’e ekle. Spotify parça bağlantısında YouTube eşleşmesini seç; sesin kaynağı listede gösterilir.'))
        entry = QHBoxLayout()
        self.url = QLineEdit(self.engine.url)
        self.url.setPlaceholderText(_tr('https://… bağlantısını yapıştır'))
        self.url.returnPressed.connect(self.inspect)
        entry.addWidget(self.url,1)
        self.inspect_button = button('Bağlantıyı İncele',self.inspect)
        entry.addWidget(self.inspect_button)
        layout.addLayout(entry)
        self.list = QListWidget()
        self.list.itemDoubleClicked.connect(lambda _i:self.download())
        layout.addWidget(self.list,1)
        self.progress = QProgressBar()
        self.progress.setRange(0,100)
        layout.addWidget(self.progress)
        self.status = label(self.engine.status)
        layout.addWidget(self.status)
        row = QHBoxLayout()
        self.download_button = button('İndir ve Soundpad’e Ekle',self.download,True)
        row.addWidget(self.download_button)
        self.cancel_button = button('İptal Et',self.engine.cancel.set)
        row.addWidget(self.cancel_button)
        row.addStretch()
        row.addWidget(button('Kapat',self.accept))
        layout.addLayout(row)
        layout.addWidget(label('Spotify albüm/çalma listesi bu sürümde desteklenmez. İndirilen klip otomatik çalmaz; düzenleyip tuş atadıktan sonra sen başlatırsın.'))
        self.shown_results = None
        self.url.textEdited.connect(self.url_changed)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.finished.connect(lambda _v:self.timer.stop())
        self.tick()

    def url_changed(self,*_args):
        self.engine.results = []
        self.shown_results = None
        self.list.clear()

    def inspect(self):
        try:
            self.engine.inspect(self.url.text())
        except Exception as exc:
            error(self,exc)

    def download(self):
        try:
            index = self.list.currentRow()
            if index>=0 and self.shown_results is self.engine.results:
                self.engine.download(self.engine.results[index]['url'])
            else:
                self.engine.download(self.url.text())
        except Exception as exc:
            error(self,exc)

    def tick(self):
        self.engine.poll()
        if self.shown_results is not self.engine.results:
            self.shown_results = self.engine.results
            self.list.clear()
            for result in self.engine.results:
                item = QListWidgetItem(result['title']+'\n'+result['source']+' · '+result['channel']+' · '+duration(result['duration']))
                item.setToolTip(_tr(result['url']))
                self.list.addItem(item)
            if self.list.count():
                self.list.setCurrentRow(0)
        self.status.setText(_tr(self.engine.status))
        self.progress.setValue(self.engine.progress)
        self.inspect_button.setEnabled(not self.engine.busy)
        self.download_button.setEnabled(not self.engine.busy)
        self.url.setEnabled(not self.engine.busy)
        self.cancel_button.setEnabled(self.engine.busy)


class Waveform(QWidget):
    selection_changed = Signal(float,float)
    view_changed = Signal()
    context_requested = Signal(object)
    def __init__(self, engine, entry):
        super().__init__()
        self.setMinimumHeight(210)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.engine, self.entry = engine, entry
        self.total = entry['frames']/RATE
        self.start, self.end = entry['start']/RATE, entry['end']/RATE
        self.zoom, self.offset, self.gain, self.playhead = 1.0, 0.0, 1.0, None
        self.dragging = None
        self.peaks = np.load(engine.pcm_path(entry).with_suffix('.peaks.npy'), allow_pickle=False)
        self.raw = None
        self._wave_cache = None
        self._wave_key = None

    def set_entry(self, entry):
        if self.raw is not None:
            self.raw._mmap.close()
        self.raw = None
        self.entry = entry
        self.total = entry['frames']/RATE
        self.peaks = np.load(self.engine.pcm_path(entry).with_suffix('.peaks.npy'),allow_pickle=False)
        self._wave_cache = self._wave_key = None
        self.offset = min(self.offset,max(0,self.total-self.span))
        self.update()

    def contextMenuEvent(self, event):
        self.setFocus()
        self.context_requested.emit(event.pos())
        event.accept()

    @property
    def span(self):
        return max(1/RATE, self.total/self.zoom)

    def time_at(self, x):
        return min(self.total, max(0, self.offset+x/max(1,self.width())*self.span))

    def x_at(self, seconds):
        return (seconds-self.offset)/self.span*self.width()

    def set_zoom(self, value):
        center = self.offset+self.span/2
        self.zoom = max(1, float(value))
        self.offset = min(max(0,self.total-self.span), max(0,center-self.span/2))
        self.view_changed.emit()
        self.update()

    def set_pan(self, value):
        self.offset = max(0,self.total-self.span)*value/10000
        self.update()

    def set_selection(self, start, end):
        self.start = min(self.total,max(0,start))
        self.end = max(self.start,min(self.total,end))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor('#101627'))
        w,h = self.width(),self.height()
        middle, amplitude = h/2, (h-45)/2
        painter.setPen(QPen(QColor('#29314C'),1))
        painter.drawLine(0,int(middle),w,int(middle))
        left,right = self.x_at(self.start),self.x_at(self.end)
        painter.fillRect(int(max(0,left)),24,int(max(0,min(w,right)-max(0,left))),h-48,QColor(128,101,242,35))
        # Raw detail is needed only when zoomed past the 10 ms overview bins.
        detailed = self.span/max(1,w) < .01
        if detailed and self.raw is None:
            from tools.rvc_audio_quality import pcm_view
            self.raw = pcm_view(self.engine.pcm_path(self.entry), self.entry.get('channels', 1))
        cache_key = (w, h, self.span, self.offset, self.gain)
        if cache_key != self._wave_key:
            self._wave_cache = self._draw_wave(w, h, middle, amplitude, detailed)
            self._wave_key = cache_key
        painter.drawPixmap(0, 0, self._wave_cache)
        painter.setPen(QPen(QColor('#C9BBFF'),2))
        for x in (left,right):
            if 0 <= x <= w:
                painter.drawLine(int(x),22,int(x),h-20)
                painter.drawEllipse(int(x)-4,16,8,8)
        if self.playhead is not None:
            x = self.x_at(self.playhead)
            if 0 <= x <= w:
                painter.setPen(QPen(QColor('#52D5AD'),2))
                painter.drawLine(int(x),25,int(x),h-20)
        painter.setPen(QColor('#9DA8C4'))
        for index in range(5):
            x = index*w/4
            seconds = self.offset+index*self.span/4
            painter.drawText(int(min(w-70,max(4,x))),h-5,duration(seconds))
        painter.drawText(12,16,'Sürükleyerek alan seç · sağ tıkla işlem yap · tekerlekle yakınlaştır')

    def _draw_wave(self, w, h, middle, amplitude, detailed):
        from PySide6.QtGui import QPixmap
        image = QPixmap(w, h)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setPen(QPen(QColor('#9D8AFF'),1))
        for x in range(w):
            a = self.time_at(x)
            b = self.time_at(x+1)
            if detailed:
                begin,end = int(a*RATE), min(self.entry['frames'],max(int(a*RATE)+1,int(b*RATE)))
                values = self.raw[begin:end]
                low, high = (float(values.min()),float(values.max())) if len(values) else (0,0)
            else:
                begin,end = int(a*100), min(len(self.peaks),max(int(a*100)+1,int(b*100)))
                values = self.peaks[begin:end]
                low, high = (float(values[:,0].min()),float(values[:,1].max())) if len(values) else (0,0)
            low,high = np.clip([low*self.gain,high*self.gain],-1,1)
            painter.drawLine(x,int(middle-high*amplitude),x,int(middle-low*amplitude))
        painter.end()
        return image

    def mousePressEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            return
        self.setFocus()
        x = event.position().x()
        if self.end-self.start<=1/RATE:
            self.anchor = self.time_at(x)
            self.dragging = 'range'
        elif abs(x-self.x_at(self.start)) < 12:
            self.dragging = 'start'
        elif abs(x-self.x_at(self.end)) < 12:
            self.dragging = 'end'
        else:
            self.anchor = self.time_at(x)
            self.dragging = 'range'
        self.mouseMoveEvent(event)

    def mouseMoveEvent(self, event):
        if not self.dragging:
            return
        at = self.time_at(event.position().x())
        if self.dragging == 'start':
            self.start = min(at,self.end-1/RATE)
        elif self.dragging == 'end':
            self.end = max(at,self.start+1/RATE)
        else:
            self.start,self.end = min(self.anchor,at),max(self.anchor,at)
            self.end = min(self.total,max(self.end,self.start+1/RATE))
        self.selection_changed.emit(max(0,self.start),self.end)
        self.update()

    def mouseReleaseEvent(self, event):
        self.dragging = None

    def wheelEvent(self, event):
        # Zoom around the mouse, retaining its audio time under the cursor.
        fraction = event.position().x()/max(1,self.width())
        anchor = self.time_at(event.position().x())
        self.zoom = min(100,max(1,self.zoom*(1.25 if event.angleDelta().y()>0 else .8)))
        self.offset = min(max(0,self.total-self.span), max(0,anchor-fraction*self.span))
        self.view_changed.emit()
        self.update()
        event.accept()


from tools.rvc_clip_editor import ClipEditor


class SoundpadPage(QWidget):
    def __init__(self,window):
        super().__init__()
        self.setObjectName('root')
        self.window,self.engine = window,window.owner.soundpad
        from tools.rvc_modern_qt import ShortcutEdit
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8,17,8,16)
        layout.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label('Soundpad','hero'),1)
        top.addWidget(button('+ Ses Ekle',self.add_files,True))
        top.addWidget(button('Linkten İndir',self.open_downloader))
        top.addWidget(button('Düzenle',self.edit_selected))
        top.addWidget(button('WAV Dışa Aktar',self.export_selected))
        layout.addLayout(top)
        layout.addWidget(label('Klipleri sürükleyip ekle. Sağ tıkla: düzenle, adlandır, tuş ata veya çıkış seç. Çift tık ve klip kısayolu: çal / duraklat / devam et.'))
        search = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(_tr('Ses kitaplığında ara…'))
        self.search.setText(str(self.engine.settings['search']))
        self.search.textChanged.connect(self.filter)
        search.addWidget(self.search,1)
        self.count = label('')
        search.addWidget(self.count)
        layout.addLayout(search)
        self.list = DropList('MP3, WAV, FLAC, OGG ve diğer ses dosyalarını buraya bırak.\nKitaplıkta yapay bir klip sayısı sınırı yoktur.')
        self.list.files_dropped.connect(self.add_paths)
        self.list.itemDoubleClicked.connect(lambda _i:self.toggle_selected())
        self.list.currentItemChanged.connect(self.selection)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.context)
        layout.addWidget(self.list,1)
        controls = QHBoxLayout()
        self.play_button = button('▶ Çal',self.play_selected,True)
        controls.addWidget(self.play_button)
        self.pause_button = button('Duraklat / Devam',self.engine.pause)
        controls.addWidget(self.pause_button)
        controls.addWidget(button('■ Durdur',self.engine.stop))
        self.route = QComboBox()
        for key,title in ROUTES.items():
            self.route.addItem(title,key)
        self.route.currentIndexChanged.connect(self.route_changed)
        controls.addWidget(self.route,1)
        self.loop = QCheckBox(_tr('Döngü'))
        self.loop.toggled.connect(lambda v:self.change_selected(loop=v))
        controls.addWidget(self.loop)
        layout.addLayout(controls)
        transport = QHBoxLayout()
        self.elapsed = QLabel(_tr('00:00.00'))
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setRange(0,10000)
        self.seek.sliderReleased.connect(self.seek_released)
        self.length = QLabel(_tr('00:00.00'))
        transport.addWidget(self.elapsed)
        transport.addWidget(self.seek,1)
        transport.addWidget(self.length)
        layout.addLayout(transport)
        volume = QHBoxLayout()
        volume.addWidget(label('Klip ses seviyesi'))
        self.gain = QSlider(Qt.Orientation.Horizontal)
        self.gain.setRange(-600,180)
        self.gain.sliderReleased.connect(lambda:self.change_selected(gain_db=self.gain.value()/10))
        self.gain.valueChanged.connect(lambda v:self.gain_label.setText(_tr(f'{v/10:+.1f} dB')))
        volume.addWidget(self.gain,1)
        self.gain_label = QLabel(_tr('0.0 dB'))
        volume.addWidget(self.gain_label)
        volume.addWidget(label('Genel'))
        self.master = QSlider(Qt.Orientation.Horizontal)
        self.master.setRange(0,150)
        self.master.setValue(int(self.engine.settings['master']))
        self.master.setMaximumWidth(150)
        self.master.valueChanged.connect(self.master_changed)
        volume.addWidget(self.master)
        self.master_label = QLabel(_tr(f"%{self.master.value()}"))
        volume.addWidget(self.master_label)
        layout.addLayout(volume)
        shortcut = QHBoxLayout()
        shortcut.addWidget(label('Seçili klibin tuşu'))
        self.shortcut = ShortcutEdit(QKeySequence())
        shortcut.addWidget(self.shortcut,1)
        self.shortcut_save = button('Kaydet',self.save_shortcut)
        self.shortcut.keySequenceChanged.connect(lambda _seq:self.shortcut_save.setText(_tr('Kaydet')))
        self.shortcut.stroke_captured.connect(lambda:self.shortcut_save.setText(_tr('Kaydet')))
        shortcut.addWidget(self.shortcut_save)
        shortcut.addWidget(button('Atamayı Kaldır',self.clear_shortcut))
        layout.addLayout(shortcut)
        options = QHBoxLayout()
        self.duck = QCheckBox(_tr('Klip çalarken konuşma sesini azalt'))
        self.duck.setChecked(bool(self.engine.settings['duck']))
        self.duck.toggled.connect(self.duck_changed)
        options.addWidget(self.duck)
        self.duck_db = QSpinBox()
        self.duck_db.setRange(-36,0)
        self.duck_db.setValue(int(self.engine.settings['duck_db']))
        self.duck_db.setSuffix(' dB')
        self.duck_db.valueChanged.connect(self.duck_changed)
        options.addWidget(self.duck_db)
        options.addStretch()
        options.addWidget(label('Ayarlar otomatik kaydedilir.'))
        layout.addLayout(options)
        quality = QHBoxLayout()
        self.protect_peaks = QCheckBox(_tr('Yüksek seste kırpılmayı önle'))
        self.protect_peaks.setChecked(bool(self.engine.settings['protect_peaks']))
        self.protect_peaks.toggled.connect(self.quality_changed)
        quality.addWidget(self.protect_peaks)
        quality.addWidget(label('Kulaklıkta stereo · mikrofon çıkışında mono · WAV dışa aktarımında 24 bit.'), 1)
        layout.addLayout(quality)
        self.status = label('')
        layout.addWidget(self.status)
        layout.addWidget(label('Mikrofon çıkışı, Giriş ve Çıkış bölümündeki çıkıştır. Oyun/Discord’a göndermek için sanal kabloyu seç; uygulama içinden fiziksel mikrofona ses yazılamaz. Kulaklık çıkışı Giriş ve Çıkış bölümünden seçilir.'))
        self.normalize_results = __import__('queue').Queue()
        self.rebuild()
        try:
            self.window.hotkeys.set_extra(*self.engine.bindings())
        except ValueError as exc:
            self.status.setText(_tr('Kayıtlı klip tuşlarında çakışma: '+str(exc)))

    def selected(self):
        item = self.list.currentItem()
        return self.engine.get(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def rebuild(self):
        identifier = self.engine.settings['selected']
        with QSignalBlocker(self.list):
            self.list.clear()
            for entry in self.engine.entries:
                seconds = (entry['end']-entry['start'])/RATE
                item = QListWidgetItem(f"{entry['name']}\n{duration(seconds)}  ·  {ROUTES[entry['route']]}  ·  {entry['gain_db']:+.1f} dB  ·  {entry['hotkey'] or 'Tuş atanmamış'}"+('  ·  Döngü' if entry['loop'] else ''))
                item.setData(Qt.ItemDataRole.UserRole,entry['id'])
                item.setToolTip(_tr(entry['source'] or entry.get('url','')))
                self.list.addItem(item)
                if entry['id']==identifier:
                    self.list.setCurrentItem(item)
        self.filter(self.search.text())
        self.selection()

    def filter(self,text):
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(text.casefold() not in item.text().casefold())
        current = self.list.currentItem()
        if current is not None and current.isHidden():
            visible = next((self.list.item(i) for i in range(self.list.count()) if not self.list.item(i).isHidden()),None)
            self.list.setCurrentItem(visible)
        self.count.setText(_tr(f'{len(self.engine.entries)} ses'))
        self.engine.settings['search'] = text
        self.engine.dirty = True

    def selection(self,*_args):
        entry = self.selected()
        for widget in (self.route,self.loop,self.gain):
            widget.setEnabled(entry is not None)
        self.shortcut.setEnabled(entry is not None)
        self.shortcut_save.setEnabled(entry is not None)
        if not entry:
            return
        self.engine.settings['selected'] = entry['id']
        self.engine.dirty = True
        with QSignalBlocker(self.route),QSignalBlocker(self.loop),QSignalBlocker(self.gain):
            self.route.setCurrentIndex(self.route.findData(entry['route']))
            self.loop.setChecked(bool(entry['loop']))
            self.gain.setValue(round(entry['gain_db']*10))
        self.gain_label.setText(_tr(f"{entry['gain_db']:+.1f} dB"))
        self.shortcut.setKeySequence(QKeySequence(entry['hotkey']))
        self.shortcut.capture = entry['keycode']
        self.shortcut_save.setText(_tr('Kaydedildi' if entry['hotkey'] else 'Kaydet'))
        self.length.setText(_tr(duration((entry['end']-entry['start'])/RATE)))

    def open_downloader(self):
        DownloadDialog(self).exec()

    def add_files(self):
        paths,_ = QFileDialog.getOpenFileNames(self,_tr('Soundpad’e ses ekle'),'',_tr(AUDIO_FILTER))
        self.add_paths(paths)

    def add_paths(self,paths):
        self.engine.import_files(paths)
        self.status.setText(_tr(f'{len(paths)} dosya içe aktarma kuyruğuna alındı…'))

    def play_selected(self):
        entry = self.selected()
        if entry:
            try:
                self.engine.play(entry['id'])
            except Exception as exc:
                error(self,exc)

    def toggle_selected(self):
        entry = self.selected()
        if entry:
            try:
                self.engine.toggle(entry['id'])
            except Exception as exc:
                error(self,exc)

    def change_selected(self,**changes):
        entry = self.selected()
        if entry:
            try:
                self.engine.update(entry['id'],**changes)
                self.rebuild()
            except Exception as exc:
                error(self,exc)

    def route_changed(self,*_args):
        self.change_selected(route=self.route.currentData())

    def master_changed(self,value):
        self.engine.settings['master'] = value
        self.engine.dirty = True
        self.master_label.setText(_tr(f'%{value}'))

    def duck_changed(self,*_args):
        self.engine.settings['duck'] = self.duck.isChecked()
        self.engine.settings['duck_db'] = self.duck_db.value()
        self.engine.dirty = True

    def quality_changed(self, checked):
        self.engine.settings['protect_peaks'] = bool(checked)
        self.engine.dirty = True

    def seek_released(self):
        state = self.engine.state()
        if state:
            self.engine.seek(state['duration']*self.seek.value()/10000)
        else:
            entry = self.selected()
            if entry:
                self.engine.update(entry['id'],cursor=(entry['end']-entry['start'])/RATE*self.seek.value()/10000)

    def edit_selected(self):
        entry = self.selected()
        if entry:
            dialog = None
            try:
                dialog = ClipEditor(self,entry)
                dialog.exec()
            except Exception as exc:
                error(self,exc)
            finally:
                if dialog is not None:
                    dialog.deleteLater()

    def export_selected(self):
        entry = self.selected()
        if not entry:
            return
        target,_ = QFileDialog.getSaveFileName(self,_tr('Düzenlenmiş klibi kaydet'),entry['name']+'.wav',_tr('WAV (*.wav)'))
        if target:
            try:
                self.engine.export(entry['id'],target)
            except Exception as exc:
                error(self,exc)

    def save_shortcut(self):
        entry = self.selected()
        if not entry:
            return
        try:
            candidate = dict(entry,hotkey=self.shortcut.keySequence().toString(QKeySequence.SequenceFormat.PortableText),keycode=self.shortcut.capture)
            self.window.hotkeys.validate_extra(*self.engine.bindings(candidate))
            self.engine.update(entry['id'],hotkey=candidate['hotkey'],keycode=candidate['keycode'])
            self.window.hotkeys.set_extra(*self.engine.bindings())
            self.rebuild()
            self.shortcut_save.setText(_tr('Kaydedildi'))
            self.window.root.setFocus()
        except Exception as exc:
            error(self,exc)

    def clear_shortcut(self):
        self.shortcut.setKeySequence(QKeySequence())
        self.save_shortcut()

    def context(self,point):
        item = self.list.itemAt(point)
        if not item:
            return
        self.list.setCurrentItem(item)
        entry = self.selected()
        menu = QMenu(self)
        menu.addAction('Çal / duraklat / devam et',self.toggle_selected)
        menu.addAction('Baştan çal',lambda:self.engine.play(entry['id'],restart=True))
        menu.addAction('Ses dalgasını düzenle…',self.edit_selected)
        menu.addAction('Tuş ata…',lambda:self.shortcut.setFocus())
        route = menu.addMenu(_tr('Çıkış'))
        for key,title in ROUTES.items():
            route.addAction(title,lambda _checked=False,selected=key:self.change_selected(route=selected))
        menu.addSeparator()
        menu.addAction('Yeniden adlandır…',self.rename)
        menu.addAction('Listede yukarı taşı',lambda:self.move(-1))
        menu.addAction('Listede aşağı taşı',lambda:self.move(1))
        menu.addAction('Düzenlenmiş WAV dışa aktar…',self.export_selected)
        menu.addAction('Kitaplıktan kaldır',self.remove)
        menu.exec(self.list.mapToGlobal(point))

    def rename(self):
        entry = self.selected()
        text,ok = QInputDialog.getText(self,_tr('Ses adı'),_tr('Klip adı:'),text=entry['name'])
        if ok and text.strip():
            self.change_selected(name=text.strip())
            self.window.hotkeys.set_extra(*self.engine.bindings())

    def move(self,delta):
        entry = self.selected()
        index = self.engine.entries.index(entry)
        target = min(len(self.engine.entries)-1,max(0,index+delta))
        self.engine.entries.insert(target,self.engine.entries.pop(index))
        self.engine.save()
        self.rebuild()

    def remove(self):
        entry = self.selected()
        if entry:
            self.engine.remove(entry['id'])
            self.window.hotkeys.set_extra(*self.engine.bindings())
            self.rebuild()

    def request_normalize(self,dialog,entry,start,end):
        import threading,weakref
        def calculate():
            try:
                from tools.rvc_audio_quality import pcm_view
                pcm = pcm_view(self.engine.pcm_path(entry), entry.get('channels', 1))
                peak = 0.0
                for pos in range(start,end,RATE*5):
                    peak = max(peak,float(np.max(np.abs(pcm[pos:min(end,pos+RATE*5)]),initial=0)))
                gain = min(18,max(-60,20*np.log10(.89/peak))) if peak>1e-9 else 0
                self.normalize_results.put((weakref.ref(dialog),gain,''))
                pcm._mmap.close()
            except Exception as exc:
                self.normalize_results.put((weakref.ref(dialog),0,str(exc)))
        threading.Thread(target=calculate,name='Soundpad-normalize',daemon=True).start()

    def tick(self):
        if self.engine.poll():
            self.rebuild()
        self.status.setText(_tr((self.engine.downloader.status if self.engine.downloader.busy else self.engine.status)+(f' · {self.engine.importing} dosya bekliyor' if self.engine.importing else '')))
        state = self.engine.state()
        self.pause_button.setText(_tr('Devam Et' if state and state['paused'] else 'Duraklat'))
        self.pause_button.setEnabled(state is not None)
        if state:
            self.elapsed.setText(_tr(duration(state['position'])))
            self.length.setText(_tr(duration(state['duration'])))
            if not self.seek.isSliderDown():
                self.seek.setValue(round(10000*state['position']/max(.0001,state['duration'])))
        else:
            entry = self.selected()
            self.elapsed.setText(_tr(duration(entry['cursor'] if entry else 0)))
            if not self.seek.isSliderDown():
                self.seek.setValue(round(10000*entry['cursor']/max(1/RATE,(entry['end']-entry['start'])/RATE)) if entry else 0)
        while not self.normalize_results.empty():
            ref,gain,message = self.normalize_results.get_nowait()
            dialog = ref()
            if dialog is not None and dialog.isVisible():
                if message:
                    dialog.info.setText(_tr(message))
                else:
                    dialog.gain.setValue(gain)
                    dialog.info.setText(_tr('Seçili bölümün tepe seviyesi yaklaşık −1 dBFS olacak şekilde ayarlandı.'))


class ConversionPage(QWidget):
    def __init__(self,window):
        super().__init__()
        self.setObjectName('root')
        self.window,self.engine = window,window.owner.file_converter
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8,17,8,16)
        layout.setSpacing(12)
        top = QHBoxLayout()
        top.addWidget(label('Dosyadan Ses Dönüştür','hero'),1)
        top.addWidget(button('+ Dosya Ekle',self.add_files))
        self.remove_button = button('Seçileni Kaldır',self.remove)
        top.addWidget(self.remove_button)
        layout.addLayout(top)
        layout.addWidget(label('MP3 veya WAV dosyalarını bırak; seçili model ve ses ayarlarınla WAV olarak dönüştür. İşlem sırasında canlı dönüşüm durur, bitince yeniden başlar.'))
        self.model = label('')
        layout.addWidget(self.model)
        self.list = DropList('Ses dosyalarını buraya sürükleyip bırak.\nBirden fazla dosyayı sırayla dönüştürebilirsin.')
        self.list.files_dropped.connect(self.add_paths)
        self.list.itemDoubleClicked.connect(lambda _i:self.open_result())
        layout.addWidget(self.list,1)
        folder = QHBoxLayout()
        folder.addWidget(label('Kayıt klasörü'))
        self.output = QLineEdit(str(self.engine.settings['output']))
        self.output.setReadOnly(True)
        folder.addWidget(self.output,1)
        self.folder_button = button('Klasör Seç',self.choose_folder)
        folder.addWidget(self.folder_button)
        folder.addWidget(button('Klasörü Aç',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(self.output.text()))))
        layout.addLayout(folder)
        self.noise = QCheckBox(_tr('Dosyaya da RNNoise uygula · mikrofon temizliği ayarlarını kullan'))
        self.noise.setChecked(bool(self.engine.settings['rnnoise']))
        self.noise.toggled.connect(self.noise_changed)
        layout.addWidget(self.noise)
        self.enhance = QCheckBox(_tr('Deneysel ses onarımı · Resemble Enhance'))
        self.enhance.setChecked(bool(self.engine.settings['enhance']))
        self.enhance.setToolTip(_tr('RVC dönüşümünden sonra dosyadaki ses bozulmalarını onarmayı dener. Doğallığı artıracağı garanti değildir.'))
        self.enhance.toggled.connect(self.enhance_changed)
        layout.addWidget(self.enhance)
        self.enhance_details = QWidget()
        repair_layout = QVBoxLayout(self.enhance_details)
        repair_layout.setContentsMargins(20,0,0,0)
        repair_layout.setSpacing(6)
        repair_layout.addWidget(label('Yalnız dosya dönüşümüne uygulanır. İşlem uzayabilir ve sesin karakteri değişebilir. Normal RVC kaydı ayrıca saklanır.'))
        repair_controls = QHBoxLayout()
        repair_controls.addWidget(label('Onarım kalitesi'))
        self.enhance_quality = QComboBox()
        self.enhance_quality.addItems(['Hızlı','Dengeli','Ayrıntılı'])
        self.enhance_quality.setCurrentText(self.engine.settings['enhance_quality'])
        self.enhance_quality.currentTextChanged.connect(self.quality_changed)
        repair_controls.addWidget(self.enhance_quality)
        self.prepare_repair_button = button('Modeli İndir / Kontrol Et',self.prepare_repair)
        repair_controls.addWidget(self.prepare_repair_button)
        repair_controls.addStretch()
        repair_layout.addLayout(repair_controls)
        self.repair_status = label(self.engine.repair.cache_status())
        repair_layout.addWidget(self.repair_status)
        self.enhance_details.setVisible(self.enhance.isChecked())
        layout.addWidget(self.enhance_details)
        actions = QHBoxLayout()
        self.convert = button('Dosyaları Dönüştür',self.start,True)
        actions.addWidget(self.convert)
        self.cancel = button('İptal Et',self.engine.cancel.set)
        actions.addWidget(self.cancel)
        actions.addStretch()
        actions.addWidget(button('Seçili Sonucu Aç',self.open_result))
        self.open_original_button = button('Onarımsız RVC Kaydını Aç',self.open_original)
        self.open_original_button.setToolTip(_tr('Onarım öncesindeki RVC kaydını karşılaştırmak için aç.'))
        self.open_original_button.setVisible(self.enhance.isChecked())
        actions.addWidget(self.open_original_button)
        layout.addLayout(actions)
        self.progress = QProgressBar()
        self.progress.setRange(0,100)
        layout.addWidget(self.progress)
        self.status = label('')
        layout.addWidget(self.status)
        layout.addWidget(label('Orijinal dosya korunur. Aynı ad varsa yeni çıktı numaralandırılır. Model ve pitch ayarlarını Ses Dönüşümü sekmesinden seçebilirsin.'))
        self.rebuild()

    def rebuild(self):
        current = self.list.currentRow()
        self.list.clear()
        for item in self.engine.items:
            row = QListWidgetItem(Path(item['path']).name+'\n'+item['state'])
            row.setToolTip(_tr(item['path']+'\n'+item['state']+('\n'+item['result'] if item['result'] else '')
                           + ('\nOnarımsız: '+item['original'] if item.get('original') else '')))
            self.list.addItem(row)
        if self.list.count():
            self.list.setCurrentRow(min(max(0,current),self.list.count()-1))

    def add_files(self):
        paths,_ = QFileDialog.getOpenFileNames(self,_tr('Dönüştürülecek ses dosyaları'),'',_tr(AUDIO_FILTER))
        self.add_paths(paths)

    def add_paths(self,paths):
        try:
            self.engine.add(paths)
            self.rebuild()
        except Exception as exc:
            error(self,exc)

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self,'Dönüştürülmüş seslerin klasörü',self.output.text())
        if path:
            self.output.setText(path)
            self.engine.settings['output'] = path
            self.engine.save()

    def noise_changed(self,value):
        self.engine.settings['rnnoise'] = value
        self.engine.save()

    def enhance_changed(self,value):
        self.engine.settings['enhance'] = value
        self.enhance_details.setVisible(value)
        self.open_original_button.setVisible(value or any(i.get('original') for i in self.engine.items))
        self.engine.save()

    def quality_changed(self,value):
        self.engine.settings['enhance_quality'] = self.enhance_quality.currentData() or value
        self.engine.save()

    def prepare_repair(self):
        try:
            self.engine.prepare_repair()
        except Exception as exc:
            error(self,exc)

    def remove(self):
        row = self.list.currentRow()
        if row>=0 and not self.engine.busy:
            self.engine.items.pop(row)
            self.engine.save()
            self.rebuild()

    def start(self):
        try:
            self.engine.start(self.window._values())
        except Exception as exc:
            error(self,exc)

    def open_result(self):
        row = self.list.currentRow()
        if row>=0:
            path = self.engine.items[row]['result']
            if path and Path(path).is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def open_original(self):
        row = self.list.currentRow()
        if row>=0:
            path = self.engine.items[row].get('original','')
            if path and Path(path).is_file():
                QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def tick(self):
        changed,resume = self.engine.poll()
        if changed:
            self.rebuild()
        if resume and not self.window.close_when_done:
            self.window.events.put('start_vc')
        values = self.window._values()
        self.model.setText(_tr('Model: '+(Path(values.get('pth_path','')).name or 'seçilmedi')+f" · Pitch: {values.get('pitch',0):g}"))
        self.status.setText(_tr(self.engine.status))
        self.progress.setValue(self.engine.progress)
        self.convert.setEnabled(not self.engine.busy and not self.window.owner.studio_tools.busy)
        self.cancel.setEnabled(self.engine.busy)
        for widget in (self.remove_button,self.folder_button,self.noise,self.enhance,
                       self.enhance_quality,self.prepare_repair_button):
            widget.setEnabled(not self.engine.busy)
        self.repair_status.setText(_tr(self.engine.repair.cache_status()))
        self.open_original_button.setVisible(self.enhance.isChecked() or any(i.get('original') for i in self.engine.items))
