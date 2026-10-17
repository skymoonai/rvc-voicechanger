"""Modern PySide6 frontend for RVC realtime_gui.py (runtime adapter, no DSP changes)."""
from __future__ import annotations

import queue
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

from PySide6.QtCore import QObject, Qt, Signal, Slot, QThread, QTimer, QUrl, QSize
from PySide6.QtGui import QKeySequence, QTextCursor, QDesktopServices, QColor, QFont, QIcon, QPixmap, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QComboBox, QDialog,
    QFileDialog, QFrame, QGridLayout, QHBoxLayout, QInputDialog,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox,
    QPushButton, QRadioButton, QScrollArea, QSlider, QTabWidget, QTabBar, QToolButton,
    QVBoxLayout, QWidget, QProgressBar, QKeySequenceEdit, QPlainTextEdit,
    QAbstractItemView, QDialogButtonBox,
    QStyledItemDelegate, QStyle, QSizePolicy,
)

from tools.rvc_i18n import t as _tr
from tools.rvc_user_state import APP_VERSION

TAB_IDS={'Ses Ayarları':'voice','Ses Dönüşümü':'voice','Soundpad':'soundpad',
    'Dosyadan Dönüştür':'file','AI LAB Türkiye':'database','Ses Cihazları':'audio','Giriş ve Çıkış':'audio',
    'Ses Temizleme ve Kalite':'quality','Ses Testi / A-B':'testing','Ayarlar':'settings',
    'Hakkında':'about','Günlük':'log','Kısayollar':'settings','Gecikme ve Filtreler':'settings','Kendi Sesimi Dinle':'audio'}
DEFAULT_TAB_ORDER=['voice','soundpad','file','database','audio','quality','testing','settings','about','log']

C = {
    'bg': '#0B0D17', 'sidebar': '#101424', 'panel': '#161B2D',
    'panel2': '#1D2338', 'line': '#29314C', 'text': '#F1F4FF',
    'muted': '#9DA8C4', 'violet': '#8065F2', 'accent': '#9D8AFF',
    'green': '#52D5AD',
}

ASSETS = Path(__file__).resolve().parent.parent / 'assets'
CHANNEL_URL = 'https://www.youtube.com/@SkymoonSTR'
SUBSCRIBE_URL = CHANNEL_URL + '?sub_confirmation=1'
SHOP_URL = 'https://www.shopier.com/skymoonai'
_windows_app_id_set = False


def configure_branding(app):
    global _windows_app_id_set
    if sys.platform == 'win32' and not _windows_app_id_set:
        try:
            import ctypes
            api = ctypes.WinDLL('shell32').SetCurrentProcessExplicitAppUserModelID
            api.argtypes = [ctypes.c_wchar_p]
            api.restype = ctypes.c_long
            code = api('Skymoon.RVCStudio.Desktop')
            if code < 0:
                raise OSError(f'AppUserModelID: {code}')
            _windows_app_id_set = True
        except (OSError, AttributeError) as exc:
            print('Uygulama simgesi: ' + str(exc), flush=True)
    app.setApplicationName('RVC Studio')
    app.setApplicationDisplayName('RVC Studio')
    app.setOrganizationName('Skymoon')
    icon = QIcon(str(ASSETS / 'rvc_logo.ico'))
    if icon.isNull():
        icon = QIcon(str(ASSETS / 'rvc_logo.png'))
    app.setWindowIcon(icon)
    return icon

STYLESHEET = '''
QMainWindow, QDialog, QWidget#root { background: #0B0D17; }
QWidget { color: #F1F4FF; font-family: "Segoe UI"; font-size: 12px; }
QFrame#sidebar { background: #101424; border-right: 1px solid #29314C; }
QFrame#card { background: #161B2D; border: 1px solid #29314C; border-radius: 14px; }
QLabel#brand { color: #FFFFFF; font-size: 23px; font-weight: 800; }
QLabel#hero { color: #FFFFFF; font-size: 22px; font-weight: 750; }
QLabel#section { color: #E7EAFF; font-size: 15px; font-weight: 700; }
QLabel#subtitle { color: #9DA8C4; font-size: 12px; }
QLabel#eyebrow { color: #9D8AFF; font-size: 11px; font-weight: 700; }
QLabel#stat { color: #52D5AD; font-size: 16px; font-weight: 700; }
QPushButton { background: #272F49; border: 1px solid #3A4365; border-radius: 9px;
              padding: 9px 13px; min-height: 19px; font-weight: 600; }
QPushButton:hover { background: #343E61; border-color: #7C72CA; }
QPushButton:pressed { background: #252C48; }
QPushButton:disabled { background: #1B2032; color: #727D99; border-color: #29314C; }
QPushButton#primary { background: #8065F2; color: white; border: none; font-size: 14px;
                       padding: 12px 24px; border-radius: 11px; }
QPushButton#primary:hover { background: #957EFF; }
QPushButton#stop { background: #452937; color: #FFD2DC; border: 1px solid #7E445C;
                    font-size: 14px; padding: 12px 24px; border-radius: 11px; }
QPushButton#stop:hover { background: #633447; }
QPushButton#youtubeButton { background: #DC3548; color: white; border: 1px solid #EF6573;
                           border-radius: 10px; padding: 12px 18px; font-weight: 700; }
QPushButton#youtubeButton:hover { background: #EF4B5C; }
QLineEdit, QComboBox { background: #101627; color: #F1F4FF; border: 1px solid #36405D;
                      border-radius: 8px; padding: 8px 10px; min-height: 20px; }
QSpinBox, QDoubleSpinBox { background: #101627; color: #F1F4FF; border: 1px solid #36405D;
                           border-radius: 7px; padding: 7px; min-height: 20px; }
QMenu { background: #161B2D; border: 1px solid #36405D; padding: 6px; }
QMenu::item { padding: 7px 20px; border-radius: 5px; }
QMenu::item:selected { background: #38305D; }
QComboBox QAbstractItemView { background: #1D2338; color: white; selection-background-color: #6650BE; }
QListWidget { background: #101627; color: #F1F4FF; border: 1px solid #2D3651;
              border-radius: 10px; padding: 5px; outline: none; }
QListWidget::item { border-radius: 8px; padding: 13px 10px; margin: 3px 1px; }
QListWidget::item:selected { background: #38305D; color: #FFFFFF; border-left: 3px solid #9D8AFF; }
QListWidget::item:hover:!selected { background: #222A41; }
QSlider::groove:horizontal { background: #303851; height: 6px; border-radius: 3px; }
QSlider::sub-page:horizontal { background: #8065F2; border-radius: 3px; }
QSlider::handle:horizontal { background: #C9BBFF; border: 2px solid #8065F2;
                             height: 16px; width: 16px; margin: -7px 0; border-radius: 9px; }
QCheckBox, QRadioButton { spacing: 9px; }
QCheckBox::indicator, QRadioButton::indicator { width: 16px; height: 16px; }
QCheckBox::indicator:checked { background: #8065F2; border: 1px solid #AF9CFF; border-radius: 4px; }
QCheckBox::indicator:unchecked { background: #101627; border: 1px solid #63708D; border-radius: 4px; }
QRadioButton::indicator:checked { background: #8065F2; border: 3px solid #B4A4FF; border-radius: 9px; }
QRadioButton::indicator:unchecked { background: #101627; border: 2px solid #63708D; border-radius: 9px; }
QTabWidget::pane { border: none; }
QTabBar::tab { background: #151A2A; color: #9DA8C4; padding: 12px 20px; margin-right: 5px;
               border-top-left-radius: 10px; border-top-right-radius: 10px; }
QTabBar::tab:selected { color: #FFFFFF; background: #252A48; border-bottom: 2px solid #9D8AFF; }
QTabBar::tab:hover:!selected { background: #20263C; }
QTabBar::tear { width: 0; border: none; }
QTabBar QToolButton { background: #252A48; color: #C9BBFF; border: 1px solid #36405D;
                     border-radius: 5px; font-size: 20px; }
QTabBar QToolButton:disabled { color: #63708D; background: #151A2A; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: #111726; width: 9px; }
QScrollBar::handle:vertical { background: #485170; border-radius: 4px; min-height: 26px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar:horizontal { background: #111726; height: 9px; }
QScrollBar::handle:horizontal { background: #485170; border-radius: 4px; min-width: 26px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page, QScrollBar::sub-page { background: none; }
QAbstractScrollArea::corner { background: #101627; }
QProgressBar { border: none; background: #29314C; border-radius: 4px; min-height: 8px; max-height: 8px; }
QProgressBar::chunk { background: #52D5AD; border-radius: 4px; }
QKeySequenceEdit { background: #101627; border: 1px solid #36405D; border-radius: 8px; padding: 5px; }
'''


class Handle(QObject):
    update_requested = Signal(object)

    def __init__(self, widget, kind, *, minimum=0, step=1, value_label=None):
        super().__init__()
        self.widget = widget
        self.kind = kind
        self.minimum = minimum
        self.step = step
        self.value_label = value_label
        self.update_requested.connect(self._apply)

    def get(self):
        w, kind = self.widget, self.kind
        if kind in ('input', 'label'):
            return w.text()
        if kind == 'combo':
            return w.currentData() if w.currentData() is not None else w.currentText()
        if kind == 'keyseq':
            return w.keySequence().toString(QKeySequence.SequenceFormat.PortableText)
        if kind == 'list':
            item = w.currentItem()
            return item.text() if item is not None else ''
        if kind == 'slider':
            value = self.minimum + w.value() * self.step
            return round(float(value), 5)
        if kind in ('check', 'radio'):
            return w.isChecked()
        return None

    def update(self, value=None, values=None, **kwargs):
        data = {'value': value, 'values': values, 'has_value': value is not None}
        if QThread.currentThread() != QApplication.instance().thread():
            self.update_requested.emit(data)
        else:
            self._apply(data)

    Update = update

    @Slot(object)
    def _apply(self, data):
        w = self.widget
        w.blockSignals(True)
        try:
            if data['values'] is not None and self.kind in ('combo', 'list'):
                old = self.get()
                if self.kind == 'combo':
                    w.clear()
                    
                    for value in data['values']:w.addItem(str(value) if w.property('rvc_i18n_device') else _tr(str(value)),str(value))
                else:
                    w.clear()
                    w.addItems([str(s) for s in data['values']])
                if not data['has_value']:
                    data = dict(data, value=old, has_value=True)
            if data['has_value']:
                val = data['value']
                if self.kind in ('input', 'label'):
                    if w.text() != str(val):
                        w.setText(_tr(str(val)) if self.kind=='label' else str(val))
                elif self.kind == 'combo':
                    idx = w.findData(str(val))
                    if idx < 0:idx=w.findText(str(val))
                    w.setCurrentIndex(idx if idx >= 0 else -1)
                elif self.kind == 'keyseq':
                    w.setKeySequence(QKeySequence(str(val)))
                elif self.kind == 'list':
                    matches = w.findItems(str(val), Qt.MatchFlag.MatchExactly)
                    if matches:
                        w.setCurrentItem(matches[0])
                    else:
                        w.clearSelection()
                        w.setCurrentRow(-1)
                elif self.kind == 'slider':
                    w.setValue(round((float(val) - self.minimum) / self.step))
                    self._set_value_label()
                elif self.kind in ('check', 'radio'):
                    w.setChecked(bool(val))
        finally:
            w.blockSignals(False)

    def _set_value_label(self):
        if self.value_label is not None:
            val = self.get()
            self.value_label.setText(_tr(f'{val:g}'))


class ModernMainWindow(QMainWindow):
    def __init__(self, wrapper):
        super().__init__()
        self.wrapper = wrapper

    def closeEvent(self, event):
        tools = self.wrapper.owner.studio_tools
        if tools.busy or self.wrapper.database.busy or self.wrapper.owner.file_converter.busy:
            tools.cancel.set()
            self.wrapper.owner.file_converter.cancel.set()
            self.wrapper.database.cancel.set()
            self.wrapper.close_when_done = True
            event.ignore()
            return
        self.wrapper.shutdown()
        self.wrapper.closed = True
        event.accept()


class ShortcutEdit(QKeySequenceEdit):
    """Commit a single stroke immediately and retain its actual Windows virtual key."""
    stroke_captured = Signal()
    def __init__(self, sequence):
        self.capture = None
        super().__init__(sequence)
        self.setMaximumSequenceLength(1)
        line = self.findChild(QLineEdit)
        if line:
            line.setPlaceholderText(_tr('Tuşa bas…'))

    def setKeySequence(self, sequence):
        self.capture = None
        super().setKeySequence(sequence)
        line = self.findChild(QLineEdit)
        if line:
            line.setPlaceholderText(_tr('Tuşa bas…'))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Control, Qt.Key.Key_Shift, Qt.Key.Key_Alt, Qt.Key.Key_Meta, Qt.Key.Key_AltGr):
            event.accept()
            return
        if event.key() == Qt.Key.Key_Backspace and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.setKeySequence(QKeySequence())
            event.accept()
            return
        sequence = QKeySequence(event.keyCombination())
        self.setKeySequence(sequence)
        text = sequence.toString(QKeySequence.SequenceFormat.PortableText)
        modifiers = event.modifiers()
        mods = (1 if modifiers & Qt.KeyboardModifier.AltModifier else 0) | (2 if modifiers & Qt.KeyboardModifier.ControlModifier else 0) | (4 if modifiers & Qt.KeyboardModifier.ShiftModifier else 0) | (8 if modifiers & Qt.KeyboardModifier.MetaModifier else 0)
        vk = int(event.nativeVirtualKey())
        if vk:
            self.capture = {'text': text, 'modifiers': mods, 'vk': vk}
        self.stroke_captured.emit()
        event.accept()

    def keyReleaseEvent(self, event):
        event.accept()


class DragTabBar(QTabBar):
    """Quick drag pans; holding a tab 350 ms starts persistent reordering."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setUsesScrollButtons(True)
        self.setExpanding(False)
        self.setDrawBase(False)
        self.setElideMode(Qt.TextElideMode.ElideNone)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip(_tr('Görmek için sürükle. Yer değiştirmek için sekmeye 350 ms basılı tutup sürükle. Hakkında ve Günlük en sonda kalır.'))
        self._press = None
        self._dragging = False
        self._reordering = False
        self._distance = 0
        self.hold = QTimer(self)
        self.hold.setSingleShot(True)
        self.hold.timeout.connect(self._long_press)
        self.scroll = QTimer(self)
        self.scroll.setInterval(130)
        self.scroll.timeout.connect(self._edge_scroll)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        for name,text in (('ScrollLeftButton','‹'),('ScrollRightButton','›')):
            b = self.findChild(QToolButton,name)
            if b:
                b.setArrowType(Qt.ArrowType.NoArrow)
                b.setText(_tr(text))

    def mousePressEvent(self,event):
        if event.button() != Qt.MouseButton.LeftButton:
            return super().mousePressEvent(event)
        self._press = self._last = event.position().toPoint()
        self._dragging = self._reordering = False
        self._distance = 0
        self._index = self.tabAt(self._press)
        if self._index >= 0 and self.tabData(self._index) not in ('about','log'):
            self.hold.start(350)
        event.accept()

    def _long_press(self):
        if self._press is not None and not self._dragging:
            self._reordering = True
            self.setCurrentIndex(self._index)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.scroll.start()

    def _pan(self,direction):
        b = self.findChild(QToolButton,'ScrollLeftButton' if direction > 0 else 'ScrollRightButton')
        if b and b.isVisible() and b.isEnabled():
            b.click()

    def _edge_scroll(self):
        if self._press is not None and self._reordering:
            if self._last.x() < 35:
                self._pan(1)
            elif self._last.x() > self.width()-70:
                self._pan(-1)
            self._move_tab(self._last)

    def _move_tab(self,point):
        target = self.tabAt(point)
        if target >= 0:
            target = min(target,self.count()-3)
            if target >= 0 and target != self._index:
                self.moveTab(self._index,target)
                self._index = target

    def mouseMoveEvent(self,event):
        if self._press is None:
            return super().mouseMoveEvent(event)
        point = event.position().toPoint()
        if self._reordering:
            self._move_tab(point)
        else:
            if not self._dragging and (point-self._press).manhattanLength() >= QApplication.startDragDistance():
                self._dragging = True
                self.hold.stop()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
            if self._dragging:
                self._distance += point.x()-self._last.x()
                while abs(self._distance) >= 35:
                    direction = 1 if self._distance > 0 else -1
                    self._pan(direction)
                    self._distance -= direction*35
        self._last = point
        event.accept()

    def mouseReleaseEvent(self,event):
        if event.button() != Qt.MouseButton.LeftButton or self._press is None:
            return super().mouseReleaseEvent(event)
        self.hold.stop()
        self.scroll.stop()
        if not self._dragging and not self._reordering:
            index = self.tabAt(event.position().toPoint())
            if index >= 0:
                self.setCurrentIndex(index)
                self.tabBarClicked.emit(index)
        self._press = None
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()

    def wheelEvent(self,event):
        self._pan(event.angleDelta().x() or event.angleDelta().y())
        event.accept()


class ModelListDelegate(QStyledItemDelegate):
    """Two-line repository rows, painted on demand for large catalogs."""
    def sizeHint(self, option, index):
        return QSize(280, 78)

    def paint(self, painter, option, index):
        row = index.data(Qt.ItemDataRole.UserRole + 1) or {}
        painter.save()
        rect = option.rect.adjusted(3, 3, -3, -3)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.setPen(QColor(C['accent'] if selected else C['line']))
        painter.setBrush(QColor('#302947' if selected else C['panel2'] if hovered else '#141B2C'))
        painter.drawRoundedRect(rect, 8, 8)
        title = rect.adjusted(14, 10, -14, 0)
        title.setHeight(24)
        font = QFont(option.font)
        font.setPointSize(11)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(QColor(C['text']))
        name = row.get('id', '').replace('_', ' ')
        painter.drawText(title, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(name, Qt.TextElideMode.ElideRight, title.width()))
        meta = rect.adjusted(14, 40, -14, -8)
        font.setPointSize(9)
        font.setWeight(QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(C['muted']))
        details = row.get('package', '')
        updated = str(row.get('lastModified') or '')[:10]
        if updated:
            details += '  ·  ' + updated
        details += f"  ·  ↓ {row.get('downloads') or 0:,}  ·  ♡ {row.get('likes') or 0:,}"
        painter.drawText(meta, Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(details, Qt.TextElideMode.ElideRight, meta.width()))
        painter.restore()


class BrandImage(QWidget):
    """Paint bundled artwork with smooth scaling and a rounded clipping path."""
    def __init__(self, filename, *, size=None, banner=False, parent=None):
        super().__init__(parent)
        self.pixmap = QPixmap(str(ASSETS / filename))
        self.banner = banner
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        if size:
            self.setFixedSize(size, size)
        elif banner:
            self.setMinimumWidth(0)
            policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
            policy.setHeightForWidth(True)
            self.setSizePolicy(policy)

    def hasHeightForWidth(self):
        return self.banner

    def heightForWidth(self, width):
        if self.banner and not self.pixmap.isNull():
            return max(1, round(width * self.pixmap.height() / self.pixmap.width()))
        return -1

    def sizeHint(self):
        return QSize(800, self.heightForWidth(800)) if self.banner else super().sizeHint()

    def minimumSizeHint(self):
        return QSize(0, 1) if self.banner else super().minimumSizeHint()

    def paintEvent(self, event):
        if self.pixmap.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        path = QPainterPath()
        if self.banner:
            path.addRoundedRect(0, 0, self.width(), self.height(), 13, 13)
        else:
            path.addEllipse(0, 0, self.width(), self.height())
        painter.setClipPath(path)
        painter.drawPixmap(self.rect(), self.pixmap)


class Window:
    def __init__(self, owner, data):
        self.owner = owner
        self.events = queue.Queue()
        self.widgets = {}
        self.groups = {}
        self.closed = False
        self.close_when_done = False
        self._shut_down = False
        self.app = QApplication.instance() or QApplication(sys.argv[:1])
        from tools import rvc_i18n
        rvc_i18n.install(self.app)
        icon = configure_branding(self.app)
        self.app.setStyle('Fusion')
        self.app.setStyleSheet(STYLESHEET)
        self.root = ModernMainWindow(self)
        self.root.setWindowTitle(_tr('RVC Studio · '+APP_VERSION))
        self.root.setWindowIcon(icon)
        self.root.setMinimumSize(980, 580)
        self.root.resize(1280, 800)
        from tools.rvc_model_database import ModelDatabase
        from tools.rvc_gpu import GpuUsage
        self.database = ModelDatabase()
        self.gpu = GpuUsage()
        self.database_rows = []
        self.database_detail = None
        self.database_downloads = {}
        self.database_pending_imports = []
        self.database_requested = False
        from tools import rvc_ui_state
        self.saved_ui = rvc_ui_state.load()
        data = rvc_ui_state.initial_data(data, self.saved_ui)
        data=self.owner.profile_start_data(data)
        from tools.rvc_profile_backup import ProfileService
        self.profile_service=ProfileService(owner,ASSETS.parent)
        owner._profile_service=self.profile_service
        self.error_dialog=None
        self._build(data)
        from tools.rvc_hotkeys import Hotkeys
        self.hotkeys = Hotkeys(lambda action: self.owner.studio_tools.post('hotkey', action=action),
                               lambda message: self.owner.studio_tools.post('hotkey_status', text=message))
        self._build_hotkeys()
        self.clear_cache_status.setText(_tr('Kısayol: ' + (self.hotkeys.bindings['clear_cache'] or 'atanmadı') +
                                       ' · Ayarlar > Kısayollar bölümünden değiştirebilirsin.'))
        self._build_database()
        self._build_media()
        self._build_about()
        self._build_log()
        self._restore_ui()
        self.tabs.tabBar().tabMoved.connect(lambda _a,_b:self._save_ui())
        self.tabs.currentChanged.connect(lambda _i:self._save_ui())
        self._last_ui_save = time.monotonic()
        self.app.focusChanged.connect(self._hotkey_focus)
        self.app.applicationStateChanged.connect(lambda _state: self._hotkey_focus(None, self.app.focusWidget()))
        self.timer = QTimer(self.root)
        self.timer.timeout.connect(self._studio_tick)
        self.timer.start(50)
        rvc_i18n.localize_tree(self.root)
        screen=self.app.primaryScreen()
        if screen:
            available=screen.availableGeometry()
            self.root.resize(min(self.root.width(),available.width()-35),min(self.root.height(),available.height()-40))
        self.root.show()
        self.profile_service.queue_sync(owner.profiles)
        self.hotkeys.start()
        self.gpu.start()
        from tools.rvc_audio_setup import setup_complete
        if not setup_complete(ASSETS.parent):
            QTimer.singleShot(0, self.root, self.open_setup_wizard)

    def __getitem__(self, key):
        return self.widgets[key]

    def write_event_value(self, event, value):
        self.events.put(str(event))

    def read(self):
        while True:
            self.app.processEvents()
            if self.closed:
                return sg.WINDOW_CLOSED, self._values()
            try:
                event = self.events.get_nowait()
                if event.startswith('studio_'):
                    self._studio_event(event)
                    continue
                if event == 'audio_setup':
                    self.open_setup_wizard()
                    continue
                if (self.owner.studio_tools.busy or self.owner.file_converter.busy) and event != sg.WINDOW_CLOSED:
                    self.owner.studio_tools.ab_state = 'A/B dönüştürülüyor. Canlı ayar değişiklikleri işlem bitince kullanılabilir.'
                    continue
                if event in ('vc', 'im'):
                    self._request_voice(event)
                    continue
                if event == 'index_rate' and not self.widgets['index_path'].get().strip():
                    self.widgets['index_rate'].update(0.)
                    self.owner.gui_config.index_rate = 0.
                    if hasattr(self.owner, 'rvc'):
                        self.owner.rvc.change_index_rate(0.)
                    self.owner.profile_show_status('İndeks seçilmedi; indeks katkısı kapalı.')
                    continue
                if event.startswith('monitor_'):
                    if event == 'monitor_refresh':
                        self.owner.output_monitor.stop()
                        labels = self.owner.output_monitor.refresh()
                        self.widgets['monitor_device'].update(values=labels, value=self.owner.output_monitor.selected)
                    vals = self._values()
                    try:
                        self.owner.output_monitor.configure(vals['monitor_enabled'], vals['monitor_device'], vals['monitor_volume'])
                        if event == 'monitor_refresh':
                            self.owner.output_monitor.restart()
                    except Exception as exc:
                        self.owner.output_monitor.status('Dinleme ayarları kaydedilemedi: ' + str(exc))
                    continue
                if event in getattr(self, '_local_events', {}):
                    self._file_browse(event)
                    continue
                return event, self._values()
            except queue.Empty:
                monitor = self.owner.output_monitor
                if monitor.error:
                    error, monitor.error = monitor.error, None
                    monitor.stop()
                    monitor.status('Dinleme durdu: ' + error + ' · Dinlemeyi kapatıp tekrar açabilirsin.')
                time.sleep(0.025)

    def _values(self):
        values = {key: h.get() for key, h in self.widgets.items()}
        if not values.get('index_path', '').strip():
            values['index_rate'] = 0.
        return values

    def _register(self, key, widget, kind, *, event=False, **opts):
        handle = Handle(widget, kind, **opts)
        self.widgets[key] = handle
        if kind in ('input','list'):widget.setProperty('rvc_i18n_skip',True)
        if event:
            if kind == 'slider':
                widget.valueChanged.connect(lambda _v, k=key, h=handle: (h._set_value_label(), self.events.put(k)))
            elif kind == 'check':
                widget.toggled.connect(lambda _v, k=key: self.events.put(k))
            elif kind == 'radio':
                widget.toggled.connect(lambda checked, k=key: self.events.put(k) if checked else None)
            elif kind == 'list':
                widget.currentItemChanged.connect(lambda cur, _prev, k=key: self.events.put(k) if cur is not None else None)
            elif kind == 'combo':
                widget.currentTextChanged.connect(lambda _text, k=key: self.events.put(k))
        return widget

    def _text(self, text, role='subtitle'):
        label = QLabel(_tr(str(text)))
        label.setObjectName(role)
        label.setWordWrap(True)
        return label

    def _btn(self, title, key, role=None):
        b = QPushButton(_tr(title))
        if role:
            b.setObjectName(role)
        b.clicked.connect(lambda _checked=False, k=key: self.events.put(k))
        return b

    def _card(self, title, subtitle=None):
        frame = QFrame()
        frame.setObjectName('card')
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(22, 20, 22, 20)
        inner.setSpacing(14)
        inner.addWidget(self._text(title, 'section'))
        if subtitle:
            inner.addWidget(self._text(subtitle))
        return frame, inner

    def _edit(self, key, val, placeholder=''):
        w = QLineEdit(str(val))
        w.setPlaceholderText(_tr(placeholder))
        return self._register(key, w, 'input')

    def _combo(self, key, options, current, event=True):
        w = QComboBox()
        for value in options:w.addItem(str(value) if key in ('sg_hostapi','sg_input_device','sg_output_device','monitor_device') else _tr(str(value)),str(value))
        if key in ('sg_hostapi','sg_input_device','sg_output_device','monitor_device'):w.setProperty('rvc_i18n_device',True)
        idx = w.findData(str(current))
        w.setCurrentIndex(idx if idx >= 0 else -1)
        return self._register(key, w, 'combo', event=event)

    def _check(self, key, title, val=False):
        w = QCheckBox(_tr(title))
        w.setChecked(bool(val))
        return self._register(key, w, 'check', event=True)

    def _radio(self, key, group, title, val=False):
        w = QRadioButton(_tr(title))
        self.groups.setdefault(group, QButtonGroup(self.root)).addButton(w)
        w.setChecked(bool(val))
        return self._register(key, w, 'radio', event=True)

    def _slider(self, key, title, low, high, step, val, hint=''):
        outer = QWidget()
        lay = QVBoxLayout(outer)
        lay.setContentsMargins(0, 3, 0, 3)
        lay.setSpacing(7)
        head = QHBoxLayout()
        name = QLabel(_tr(title))
        name.setStyleSheet('font-weight: 600; color: #E7EAFF')
        val_label = QLabel(_tr(''))
        val_label.setStyleSheet('font-weight: 750; color: #B9AAFF; font-size: 13px')
        head.addWidget(name)
        head.addStretch()
        head.addWidget(val_label)
        lay.addLayout(head)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setMinimum(0)
        slider.setMaximum(round((high-low)/step))
        slider.setValue(max(0, min(slider.maximum(), round((float(val)-low)/step))))
        handle = self._register(key, slider, 'slider', event=True,
                                minimum=low, step=step, value_label=val_label)
        self.widgets[key]._set_value_label()
        lay.addWidget(slider)
        if hint:
            hint_label = self._text(hint)
            lay.addWidget(hint_label)
        return outer

    def _tab(self, tabs, name):
        scrolling = QScrollArea()
        scrolling.setWidgetResizable(True)
        content = QWidget()
        content.setObjectName('root')
        lay = QVBoxLayout(content)
        lay.setContentsMargins(8, 17, 8, 16)
        lay.setSpacing(14)
        lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        scrolling.setWidget(content)
        scrolling.setProperty('rvc_tab_title',name)
        scrolling.setProperty('studio_tab_id',TAB_IDS.get(name,name))
        index=tabs.addTab(scrolling,_tr(name))
        tabs.tabBar().setTabData(index,TAB_IDS.get(name,name))
        return lay

    def _build(self, data):
        root = QWidget()
        root.setObjectName('root')
        self.root.setCentralWidget(root)
        h = QHBoxLayout(root)
        h.setSpacing(0)
        h.setContentsMargins(0, 0, 0, 0)

        sidebar = QFrame()
        sidebar.setObjectName('sidebar')
        sidebar.setFixedWidth(285)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(23, 27, 23, 23)
        side.setSpacing(14)
        brand = QHBoxLayout()
        brand.setSpacing(10)
        self.brand_logo = QLabel()
        self.brand_logo.setFixedSize(48, 48)
        logo = QPixmap(str(ASSETS / 'rvc_logo.png')).scaled(
            96, 96, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        logo.setDevicePixelRatio(2)
        self.brand_logo.setPixmap(logo)
        brand.addWidget(self.brand_logo)
        brand.addWidget(self._text('RVC STUDIO', 'brand'), 1)
        side.addLayout(brand)
        side.addWidget(self._text('GERÇEK ZAMANLI SES DÖNÜŞTÜRME', 'eyebrow'))
        side.addSpacing(16)
        side.addWidget(self._text('SES MODELİ KÜTÜPHANESİ', 'section'))
        side.addWidget(self._text('Model seçince profil ayarları otomatik yüklenir.'))
        lst = QListWidget()
        self._register('profile_select', lst, 'list', event=True)
        self.owner.profile_labels_refresh()
        active = self.owner.profile_current()
        selected = self.owner.profile_label(active) if active else ''
        self.widgets['profile_select'].update(values=list(self.owner.profile_labels), value=selected)
        side.addWidget(lst, stretch=1)
        row = QHBoxLayout()
        row.addWidget(self._btn('+  Model Ekle', 'profile_add', 'primary'))
        row.addWidget(self._btn('Kaydet', 'profile_save'))
        side.addLayout(row)
        row2 = QHBoxLayout()
        row2.addWidget(self._btn('Adlandır', 'profile_rename'))
        row2.addWidget(self._btn('Profili Sil', 'profile_delete'))
        side.addLayout(row2)
        status = self._text('Profiller bu bilgisayarda saklanır.', 'subtitle')
        status.setMinimumHeight(48)
        self._register('profile_status', status, 'label')
        side.addWidget(status)
        side.addSpacing(6)
        side.addWidget(self._text('RVC STUDIO  ·  '+APP_VERSION, 'eyebrow'))
        settings_shortcut=QPushButton(_tr('Ayarlar'))
        settings_shortcut.clicked.connect(lambda:self.open_settings('general'))
        side.addWidget(settings_shortcut)
        h.addWidget(sidebar)

        central = QWidget()
        main = QVBoxLayout(central)
        main.setContentsMargins(28, 28, 28, 20)
        main.setSpacing(13)
        main.addWidget(self._text('Ses Kontrol Merkezi · Beta', 'hero'))
        main.addWidget(self._text('Modellerini yönet, sesi ayarla ve dönüşümü başlat.'))
        meter_frame = QFrame()
        meter_frame.setObjectName('card')
        meter_layout = QHBoxLayout(meter_frame)
        meter_layout.setContentsMargins(16, 10, 16, 10)
        self._meters = {}
        for key, title in [('input', 'MİKROFON GİRİŞİ'), ('output', 'SES ÇIKIŞI')]:
            column = QVBoxLayout()
            caption = self._text(title, 'eyebrow')
            value = self._text('— dBFS')
            bar = QProgressBar()
            bar.setRange(0, 600)
            bar.setTextVisible(False)
            column.addWidget(caption)
            column.addWidget(bar)
            column.addWidget(value)
            meter_layout.addLayout(column, 1)
            self._meters[key] = (bar, value)
        self.mute_button = self._btn('Çıkışı Sustur', 'studio_mute')
        meter_layout.addWidget(self.mute_button)
        self.cache_toolbar_button = self._btn('Gecikmeyi Sıfırla', 'studio_clear_cache')
        meter_layout.addWidget(self.cache_toolbar_button)
        main.addWidget(meter_frame)
        tabs = QTabWidget()
        tabs.setTabBar(DragTabBar(tabs))
        self.tabs = tabs
        main.addWidget(tabs, stretch=1)

        voice = self._tab(tabs, 'Ses Dönüşümü')
        paths, p = self._card('Aktif Model Dosyaları', 'Kütüphanedeki model yolları otomatik doldurulur.')
        p.addWidget(self._text('PTH model dosyası', 'eyebrow'))
        r = QHBoxLayout()
        r.addWidget(self._edit('pth_path', data.get('pth_path', ''), 'model.pth'), 1)
        b = self._btn('Gözat', '_browse_pth')
        r.addWidget(b)
        p.addLayout(r)
        p.addWidget(self._text('INDEX dosyası', 'eyebrow'))
        r = QHBoxLayout()
        r.addWidget(self._edit('index_path', data.get('index_path', ''), 'model.index'), 1)
        r.addWidget(self._btn('Gözat', '_browse_index'))
        p.addLayout(r)
        voice.addWidget(paths)

        conversion, cv = self._card('Ses Dönüşümü', 'Modelin karakterini ve sesin tepkisini ayarla.')
        cv.addWidget(self._slider('pitch', 'Pitch · Yarım ses', -16, 16, 1, data.get('pitch', 0)))
        cv.addWidget(self._slider('formant', 'Ses karakteri · Erkeksi / Kadınsı', -2, 2, 0.05, data.get('formant', 0),
                                  'Sola: daha erkeksi · Sağa: daha kadınsı · 0: modelin doğal karakteri.'))
        cv.addWidget(self._slider('index_rate', 'Index Rate', 0, 1, 0.01, data.get('index_rate', 0)))
        cv.addWidget(self._slider('rms_mix_rate', 'Ses yüksekliği · Mikrofon / Model', 0, 1, 0.01, data.get('rms_mix_rate', 0),
                                  '0: ses yüksekliği mikrofonunu takip eder · 1: modelin ürettiği yüksekliği korur.'))
        cv.addWidget(self._slider('threhold', 'Ses algılama eşiği · dB', -60, 0, 1, data.get('threhold', -60)))
        voice.addWidget(conversion)
        f0, fl = self._card('F0 Algoritması', 'Ses perdesini tespit etmek için kullanılacak algoritmayı seç.')
        fr = QHBoxLayout()
        for name in ('pm','rmvpe','fcpe'):
            fr.addWidget(self._radio(name, 'f0method', name.upper(), data.get(name, name=='rmvpe')))
        fr.addStretch()
        fl.addLayout(fr)
        voice.addWidget(f0)
        quality=self._tab(tabs,'Ses Temizleme ve Kalite')
        ap, apl = self._card('Deneysel Canlı Ses İyileştirme · AP-BWE',
                             'RVC çıkışındaki üst frekans ayrıntılarını yeniler. Etkisi modele göre değişir; yanlış perdeyi veya tonlamayı düzeltmez.')
        from tools.rvc_media_ui import APBWEPanel
        self.apbwe_panel = APBWEPanel(self)
        apl.addWidget(self.apbwe_panel)
        quality.addWidget(ap)

        from tools.rvc_settings_ui import SettingsPage
        self.settings_page=SettingsPage(self)
        self.settings_page.setProperty('studio_tab_id','settings')
        self.settings_page.setProperty('rvc_tab_title','Ayarlar')
        settings_index=tabs.addTab(self.settings_page,_tr('Ayarlar'))
        tabs.tabBar().setTabData(settings_index,'settings')
        perf=self.settings_page.performance_layout
        compute, cpl = self._card('Hesaplama Aygıtı',
                                  'Ses dönüştürme motorunu CPU veya ekran kartında çalıştır. Değişiklik için dönüşümü yeniden başlat.')
        cpl.addWidget(self._text('İşlemci / GPU tercihi', 'eyebrow'))
        cpl.addWidget(self._combo('compute_backend',
                                  ['Otomatik', 'CPU', 'Ekran Kartı (GPU)', "AMD ROCm'yi Zorla (Deneysel)"],
                                  data.get('compute_backend', 'Otomatik')))
        compute_status = self._text('ROCm\'yi Zorla yalnızca gerçek HIP GPU testi geçerse çalışır; CPU/DirectML\'ye dönmez. AppSandbox GPU-PV için garanti edilmez.')
        self._register('compute_backend_status', compute_status, 'label')
        cpl.addWidget(compute_status)
        perf.addWidget(compute)
        cache, cl = self._card('Gecikmeyi Sıfırla',
                              'Çıkış sesi zamanla geriden gelmeye başladıysa ses tamponlarını temizle. Model ve ayarlar korunur; ses kısa süre kesilir. Ayarlardan gelen normal işlem gecikmesi değişmez.')
        self.clear_cache_button = self._btn('Ses Tamponlarını Temizle', 'studio_clear_cache')
        cl.addWidget(self.clear_cache_button)
        self.clear_cache_status = self._text('Kısayol: Ctrl+Alt+F4 · Ayarlar > Kısayollar bölümünden değiştirebilirsin.')
        cl.addWidget(self.clear_cache_status)
        perf.addWidget(cache)
        blocks, bl = self._card('Performans', 'Değişiklikler ses akışını durdurabilir; yeniden başlatman gerekir.')
        bl.addWidget(self._slider('block_time','Örnek blok süresi · saniye', 0.02, 1.5, .01, data.get('block_time',.25)))
        bl.addWidget(self._slider('crossfade_length','Crossfade · saniye', .01,.15,.01,data.get('crossfade_length',.05)))
        bl.addWidget(self._slider('extra_time','Ek çıkarım penceresi · saniye', .05,5,.01,data.get('extra_time',2.5)))
        perf.addWidget(blocks)
        rn, rnl = self._card('RNNoise', 'Yerel yapay zekâ ile giriş gürültüsünü azalt.')
        from tools.rvc_media_ui import NoisePanel
        self.noise_panel = NoisePanel(self)
        rnl.addWidget(self.noise_panel)
        quality.addWidget(rn)
        noise, nl = self._card('Ek RVC Filtreleri', 'RNNoise ile birlikte kullanmak sesi fazla filtreleyebilir. İhtiyacına göre aç.')
        nl.addWidget(self._check('I_noise_reduce','Mikrofon girişinde gürültü engelle',data.get('I_noise_reduce',False)))
        nl.addWidget(self._check('O_noise_reduce','Dönüştürülmüş seste gürültü engelle',data.get('O_noise_reduce',False)))
        quality.addWidget(noise)

        devices = self._tab(tabs, 'Giriş ve Çıkış')
        dev, dl = self._card('Ses Girişi ve Çıkışı','Mikrofonunu ve sanal kablo çıkışını buradan ayarla.')
        dl.addWidget(self._text('Ses API / Host', 'eyebrow'))
        dl.addWidget(self._combo('sg_hostapi',self.owner.hostapis,data.get('sg_hostapi','')))
        dl.addWidget(self._text('Mikrofon · Giriş', 'eyebrow'))
        dl.addWidget(self._combo('sg_input_device',self.owner.input_devices,data.get('sg_input_device','')))
        dl.addWidget(self._text('Hoparlör / VB-CABLE · Çıkış','eyebrow'))
        dl.addWidget(self._combo('sg_output_device',self.owner.output_devices,data.get('sg_output_device','')))
        dl.addWidget(self._check('sg_wasapi_exclusive','WASAPI özel mod',data.get('sg_wasapi_exclusive',False)))
        sample = QHBoxLayout()
        sample.addWidget(self._radio('sr_model','sr_type','Model örnekleme hızı',data.get('sr_model',True)))
        sample.addWidget(self._radio('sr_device','sr_type','Cihaz örnekleme hızı',data.get('sr_device',False)))
        sample.addStretch()
        dl.addLayout(sample)
        r = QHBoxLayout()
        r.addWidget(self._btn('↻  Cihaz listesini yenile','reload_devices'))
        r.addWidget(self._btn('Başlangıç Sihirbazını Aç','audio_setup'))
        r.addStretch()
        dl.addLayout(r)
        devices.addWidget(dev)

        listening = devices
        mon, ml = self._card('Kendi Sesimi Dinle', 'Dönüştürülmüş sesi kulaklığından dinle. Ana çıkışın sanal kabloya gönderilmeye devam eder.')
        monitor = self.owner.output_monitor
        labels = monitor.refresh()
        ml.addWidget(self._check('monitor_enabled', 'Seçili mikrofon çıkışını dinle', monitor.enabled))
        ml.addWidget(self._text('Dinleme cihazı · Kulaklık', 'eyebrow'))
        ml.addWidget(self._combo('monitor_device', labels, monitor.selected))
        ml.addWidget(self._slider('monitor_volume', 'Dinleme ses seviyesi · %', 0, 100, 1, round(monitor.volume * 100)))
        ml.addWidget(self._text('Kulaklık kullan. Hoparlör sesi mikrofonuna geri girerse yankı oluşabilir.'))
        state = self._text('Dinleme açık. Sesi başlattığında dinleme devam eder.' if monitor.enabled else 'Dinleme kapalı. Ses dönüşümünü başlatıp dinlemeyi açabilirsin.')
        self._register('monitor_status', state, 'label')
        ml.addWidget(state)
        ml.addWidget(self._btn('Dinleme cihazlarını yenile', 'monitor_refresh'))
        listening.addWidget(mon)

        testing = self._tab(tabs, 'Ses Testi / A-B')
        record, rl = self._card('Kısa Ses Testi', 'Konuşmanı ve dönüştürülmüş sonucu belleğe al. Sonucu seçili kulaklıktan tekrar dinle.')
        rr = QHBoxLayout()
        rr.addWidget(self._text('Kayıt süresi · saniye'))
        rr.addWidget(self._combo('studio_seconds', ['5', '10', '15', '20', '30'], '10', event=False))
        self.record_button = self._btn('●  Test Kaydı Al', 'studio_record', 'primary')
        rr.addWidget(self.record_button)
        rl.addLayout(rr)
        rbuttons = QHBoxLayout()
        rbuttons.addWidget(self._btn('▶  Sonucu Dinle', 'studio_play_test'))
        rbuttons.addWidget(self._btn('■  Dinlemeyi Durdur', 'studio_stop_play'))
        rbuttons.addWidget(self._btn('WAV Kaydet', 'studio_export_test'))
        rl.addLayout(rbuttons)
        self.record_label = self._text(self.owner.studio_tools.record_state)
        rl.addWidget(self.record_label)
        testing.addWidget(record)
        ab, al = self._card('A/B Ayar Karşılaştırması', 'A ayarlarını sakla; ayarları değiştirip B’yi sakla. Aynı mikrofon kaydı iki ayarla dönüştürülür. İşlem sırasında canlı dönüşüm duraklar.')
        self.ab_labels = {}
        for name in ('A', 'B'):
            row = QHBoxLayout()
            row.addWidget(self._btn(name+' Ayarlarını Sakla', 'studio_snapshot_'+name))
            label = self._text(name+' · henüz saklanmadı')
            row.addWidget(label, 1)
            self.ab_labels[name] = label
            al.addLayout(row)
        self.compare_button = self._btn('A/B Sonuçlarını Oluştur', 'studio_compare', 'primary')
        al.addWidget(self.compare_button)
        compare_row = QHBoxLayout()
        compare_row.addWidget(self._btn('▶  A’yı Dinle', 'studio_play_A'))
        compare_row.addWidget(self._btn('▶  B’yi Dinle', 'studio_play_B'))
        compare_row.addWidget(self._btn('A WAV', 'studio_export_A'))
        compare_row.addWidget(self._btn('B WAV', 'studio_export_B'))
        al.addLayout(compare_row)
        self.ab_label = self._text(self.owner.studio_tools.ab_state)
        al.addWidget(self.ab_label)
        testing.addWidget(ab)
        self.hotkey_layout = self.settings_page.shortcuts_layout

        footer = QFrame()
        footer.setObjectName('card')
        footer_layout=QVBoxLayout(footer)
        footer_layout.setContentsMargins(14,8,14,8)
        bottom = QHBoxLayout()
        footer_layout.addLayout(bottom)
        bottom.setContentsMargins(0, 2, 0, 2)
        bottom.setSpacing(12)
        bottom.addWidget(self._btn('▶  SESİ BAŞLAT', 'start_vc', 'primary'))
        bottom.addWidget(self._btn('■  DURDUR', 'stop_vc', 'stop'))
        bottom.addSpacing(10)
        rb = QVBoxLayout()
        rb.addWidget(self._radio('vc', 'function','Dönüştürülmüş Ses',True))
        rb.addWidget(self._radio('im','function','Normal Mikrofon',False))
        bottom.addLayout(rb)
        bottom.addStretch(1)
        stats = QGridLayout()
        for index, (key, title, val) in enumerate((
            ('sr_stream','ÖRNEKLEME', '—'), ('delay_time','GECİKME / MS','0'),
            ('infer_time','ÇIKARIM / MS','0'), ('gpu_usage','GPU / %','—'))):
            caption = self._text(title,'eyebrow')
            label = self._text(val,'stat')
            self._register(key,label,'label')
            if key == 'gpu_usage':
                label.setToolTip(_tr('Bu RVC işleminin en yoğun GPU motorunun kullanımı. Oyun ve diğer uygulamaların toplam kullanımı dahil değildir. Sayaç desteklenmiyorsa — gösterilir.'))
            stats.addWidget(caption,0,index)
            stats.addWidget(label,1,index)
        footer_layout.addLayout(stats)
        main.addWidget(footer)
        h.addWidget(central,1)

        # Browsers are not RVC events; change only the input path field.
        self._local_events = {'_browse_pth': ('pth_path','RVC model (*.pth)'),
                              '_browse_index': ('index_path','INDEX dosyası (*.index)')}

    def _build_hotkeys(self):
        from tools.rvc_hotkeys import ACTIONS
        card, layout = self._card('Genel Klavye Kısayolları', 'Alanı seçip bir tuşa veya kombinasyona bas; yanındaki Kaydet düğmesi yalnızca o atamayı etkinleştirir. Sayı takımı tuşları üst sıradaki sayılardan ayrılır. Backspace ile atamayı temizleyebilirsin.')
        layout.addWidget(self._check('studio_hotkeys_enabled', 'Genel kısayolları etkinleştir', self.hotkeys.enabled))
        layout.addWidget(self._check('studio_hotkeys_game', 'Oyun uyumluluk modu · alternatif tuş algılama', self.hotkeys.game_mode))
        grid = QGridLayout()
        grid.setColumnStretch(0, 1)
        self.hotkey_editors = {}
        self.hotkey_save_buttons = {}
        for row, (action, (title, _default)) in enumerate(ACTIONS.items()):
            grid.addWidget(self._text(title), row, 0)
            editor = ShortcutEdit(QKeySequence(self.hotkeys.bindings[action]))
            editor.capture = self.hotkeys.keycodes.get(action)
            editor.setMaximumWidth(340)
            self._register('hotkey_'+action, editor, 'keyseq')
            self.hotkey_editors[action] = editor
            grid.addWidget(editor, row, 1)
            save = QPushButton(_tr('Kaydet'))
            save.clicked.connect(lambda _checked=False, key=action: self._save_hotkey(key))
            editor.keySequenceChanged.connect(lambda _sequence, key=action: self._hotkey_changed(key))
            editor.stroke_captured.connect(lambda key=action: self._hotkey_changed(key))
            self.hotkey_save_buttons[action] = save
            grid.addWidget(save, row, 2)
        layout.addLayout(grid)
        buttons = QGridLayout()
        buttons.addWidget(self._btn('Tüm Atamaları Kaydet', 'studio_hotkeys_apply', 'primary'),0,0,1,2)
        options = QPushButton(_tr('Algılama Ayarlarını Kaydet'))
        options.clicked.connect(self._save_hotkey_options)
        buttons.addWidget(options,1,0)
        buttons.addWidget(self._btn('Varsayılan Tuşları Doldur', 'studio_hotkeys_defaults'),1,1)
        layout.addLayout(buttons)
        self.hotkey_status = self._text('Genel kısayollar hazırlanıyor…')
        layout.addWidget(self.hotkey_status)
        layout.addWidget(self._text('Tam ekran oyunda standart mod çalışmazsa oyun uyumluluk modunu seç. Bu mod tuşları oyuna da iletir; oyun içindeki aynı atamalarla çakışabilir. Oyun yönetici olarak çalışıyorsa RVC’yi de yönetici başlatmayı dene. Bazı oyunların giriş koruması algılamayı engelleyebilir.'))
        self.hotkey_layout.addWidget(card)

    def _hotkey_focus(self, old, current):
        if hasattr(self, 'hotkeys'):
            active = self.app.applicationState() == Qt.ApplicationState.ApplicationActive
            editing = any(current is editor or (current is not None and editor.isAncestorOf(current))
                          for editor in self.hotkey_editors.values())
            editing = editing or (isinstance(current, QLineEdit) and not current.isReadOnly())
            ancestor = current
            while ancestor is not None:
                editing = editing or isinstance(ancestor, QKeySequenceEdit) or bool(ancestor.property('studio_audio_editor'))
                ancestor = ancestor.parentWidget()
            self.hotkeys.paused = active and editing

    def _save_hotkey_options(self):
        try:
            self.hotkeys.apply(self.hotkeys.bindings, self.widgets['studio_hotkeys_enabled'].get(), self.widgets['studio_hotkeys_game'].get())
            self.root.setFocus()
            self.hotkey_status.setText(_tr('Algılama ayarları kaydedildi.'))
        except Exception as exc:
            _popup(str(exc), error=True)

    def _hotkey_changed(self, action):
        editor = self.hotkey_editors[action]
        same = self.widgets['hotkey_' + action].get() == self.hotkeys.bindings[action] and editor.capture == self.hotkeys.keycodes.get(action)
        self.hotkey_save_buttons[action].setText(_tr('Kaydedildi' if same else 'Kaydet'))

    def _save_hotkey(self, action):
        try:
            bindings = dict(self.hotkeys.bindings)
            bindings[action] = self.widgets['hotkey_' + action].get()
            codes = dict(self.hotkeys.keycodes)
            codes.pop(action, None)
            if self.hotkey_editors[action].capture:
                codes[action] = self.hotkey_editors[action].capture
            self.hotkeys.apply(bindings, self.widgets['studio_hotkeys_enabled'].get(), self.widgets['studio_hotkeys_game'].get(), codes)
            if action == 'clear_cache':
                self.clear_cache_status.setText(_tr('Kısayol: ' + (bindings[action] or 'atanmadı') +
                                               ' · Ses tamponlarını temizler.'))
            self.hotkey_save_buttons[action].setText(_tr('Kaydedildi'))
            self.root.setFocus()
            from tools.rvc_hotkeys import ACTIONS
            self.hotkey_status.setText(_tr(ACTIONS[action][0] + ': ' + (bindings[action] or 'Kapalı') + ' · kaydedildi.'))
        except Exception as exc:
            self.hotkey_status.setText(_tr(str(exc)))
            _popup(str(exc), error=True)

    def _request_voice(self, mode):
        self.owner._studio_requested_mode = mode
        self.widgets['vc'].update(mode == 'vc')
        self.widgets['im'].update(mode == 'im')

    def _build_log(self):
        layout = self._tab(self.tabs, 'Günlük')
        self.log_tab_widget = self.tabs.widget(self.tabs.count()-1)
        layout.addWidget(self._text('Uygulama Günlüğü', 'section'))
        layout.addWidget(self._text('Başlangıç, dönüşüm ve hata mesajları burada görünür. Yerel kayıtlar için Ayarlar bölümündeki hata günlüklerini aç.'))
        row = QHBoxLayout()
        self.log_follow = QCheckBox(_tr('Otomatik kaydır'))
        self.log_follow.setChecked(True)
        self.log_pause = QCheckBox(_tr('Görüntüyü duraklat'))
        row.addWidget(self.log_follow)
        row.addWidget(self.log_pause)
        row.addStretch()
        copy = QPushButton(_tr('Kopyala'))
        copy.clicked.connect(lambda: self.app.clipboard().setText(self.log_view.toPlainText()))
        clear = QPushButton(_tr('Temizle'))
        clear.clicked.connect(self._clear_log)
        row.addWidget(copy)
        row.addWidget(clear)
        layout.addLayout(row)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(3000)
        self.log_view.setMinimumHeight(340)
        self.log_view.setStyleSheet('background: #101627; color: #C9D3E8; border: 1px solid #36405D; border-radius: 8px; padding: 10px; font-family: Consolas; font-size: 12px;')
        layout.addWidget(self.log_view, 1)

    def _open_external(self, url):
        if not QDesktopServices.openUrl(QUrl(url)):
            _popup('Tarayıcı açılamadı. Bağlantı: ' + url, error=True)

    def _external_button(self, text, url, role=None):
        button = QPushButton(_tr(text))
        if role:
            button.setObjectName(role)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(_tr(url))
        button.clicked.connect(lambda _checked=False: self._open_external(url))
        return button

    def _build_about(self):
        layout = self._tab(self.tabs, 'Hakkında')
        self.about_tab_widget = self.tabs.widget(self.tabs.count() - 1)
        layout.setSpacing(16)
        title = QHBoxLayout()
        title.addWidget(self._text('Hakkında', 'section'))
        title.addStretch()
        badge = self._text('RVC STUDIO  /  SKYMOON', 'eyebrow')
        badge.setWordWrap(False)
        title.addWidget(badge)
        layout.addLayout(title)

        channel = QFrame()
        channel.setObjectName('card')
        hero = QVBoxLayout(channel)
        hero.setContentsMargins(10, 10, 10, 10)
        hero.setSpacing(0)
        self.about_banner = BrandImage('skymoon_banner.png', banner=True)
        self.about_banner.setAccessibleName('Skymoon YouTube kanal bannerı')
        hero.addWidget(self.about_banner)
        channel_body = QWidget()
        body = QHBoxLayout(channel_body)
        body.setContentsMargins(14, 20, 14, 12)
        body.setSpacing(18)
        self.about_avatar = BrandImage('skymoon_avatar.png', size=88)
        self.about_avatar.setAccessibleName('Skymoon kanal profil fotoğrafı')
        body.addWidget(self.about_avatar, 0, Qt.AlignmentFlag.AlignTop)
        identity = QVBoxLayout()
        identity.setSpacing(7)
        name_row = QHBoxLayout()
        name = self._text('Skymoon')
        name.setStyleSheet('font-size: 28px; font-weight: 750; color: #F1F4FF;')
        name_row.addWidget(name)
        creator = QLabel(_tr('YAPIMCI'))
        creator.setStyleSheet('color: #C9BBFF; background: #302947; border: 1px solid #554675; border-radius: 6px; padding: 4px 8px; font-size: 10px; font-weight: 700;')
        name_row.addWidget(creator, 0, Qt.AlignmentFlag.AlignVCenter)
        name_row.addStretch()
        identity.addLayout(name_row)
        identity.addWidget(self._text('@SkymoonSTR', 'eyebrow'))
        identity.addWidget(self._text('Ses değiştirme içerikleri ve kullanım videoları için kanala göz at. Yeni içerikleri takip etmek için abone ol.'))
        body.addLayout(identity, 1)
        actions = QVBoxLayout()
        actions.setSpacing(9)
        self.about_subscribe = self._external_button('▶  Abone Ol', SUBSCRIBE_URL, 'youtubeButton')
        self.about_channel = self._external_button('Kanalı Aç  ↗', CHANNEL_URL)
        actions.addWidget(self.about_subscribe)
        actions.addWidget(self.about_channel)
        actions.addStretch()
        body.addLayout(actions)
        hero.addWidget(channel_body)
        layout.addWidget(channel)

        columns = QHBoxLayout()
        columns.setSpacing(16)
        project, p = self._card('Bu Proje Nedir?', 'RVC tabanlı, gerçek zamanlı ses değiştirme.')
        p.addWidget(self._text('RVC Studio, RVC (Retrieval-based Voice Conversion) teknolojisi üzerine kurulmuş bir ses değiştirici projesidir. Ses modellerini yönetmeyi, ayarları karşılaştırmayı ve mikrofon sesini gerçek zamanlı dönüştürmeyi tek arayüzde toplar.'))
        p.addSpacing(4)
        p.addWidget(self._text('TAMAMEN VIBE CODING', 'eyebrow'))
        p.addWidget(self._text('Bu proje profesyonel bir yazılımcı tarafından yapılmadı. Skymoon tarafından tamamen vibe coding yaklaşımıyla; yapay zekâ desteği, denemeler ve kullanıcı geri bildirimleriyle geliştirildi.'))
        p.addWidget(self._text('Ses kalitesi ve gecikme; seçilen model, ses cihazları, donanım ve ayarlara göre değişir. RVC ve kullanılan açık kaynak bileşenler kendi geliştiricilerine aittir; bu proje bağımsız bir arayüz ve topluluk çalışmasıdır.'))
        columns.addWidget(project, 3)

        store, s = self._card('Ücretli Ses Modelleri', 'Skymoon AI  /  Shopier')
        store_logo = QLabel()
        store_logo.setAlignment(Qt.AlignmentFlag.AlignLeft)
        store_logo.setPixmap(QPixmap(str(ASSETS / 'rvc_logo.png')).scaled(
            70, 70, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        s.addWidget(store_logo)
        s.addWidget(self._text('Ücretli ses modellerini almak istiyorsanız Skymoon AI mağazasını ziyaret edebilirsiniz.'))
        s.addStretch()
        self.about_shop = self._external_button('Shopier Mağazasına Git  ↗', SHOP_URL, 'primary')
        s.addWidget(self.about_shop)
        s.addWidget(self._text('shopier.com/skymoonai'))
        columns.addWidget(store, 2)
        layout.addLayout(columns)
        layout.addWidget(self._text('Yapımcı: Skymoon  ·  Bağlantılar varsayılan tarayıcınızda açılır.'))

    def _build_database(self):
        layout = self._tab(self.tabs, 'AI LAB Türkiye')
        self.database_tab_widget = self.tabs.widget(self.tabs.count() - 1)
        self.tabs.tabBar().moveTab(self.tabs.count() - 1, 1)
        layout.addWidget(self._text('AI LAB Türkiye', 'section'))
        layout.addWidget(self._text('Hugging Face model kütüphanesi · Bir model seç, dosyalarını incele ve kendi kütüphanene indir.'))
        controls = QHBoxLayout()
        self.database_search = QLineEdit()
        self.database_search.setPlaceholderText(_tr('Model ara…'))
        self.database_search.textChanged.connect(self._filter_database)
        controls.addWidget(self.database_search, 1)
        self.database_filter = QComboBox()
        self.database_filter.addItems(['Tüm paketler', 'PTH', 'PTH + INDEX', 'ZIP', 'RAR'])
        self.database_filter.currentTextChanged.connect(self._filter_database)
        controls.addWidget(self.database_filter)
        self.database_sort = QComboBox()
        self.database_sort.addItems(['Ada göre', 'En çok beğenilen', 'Son güncellenen'])
        self.database_sort.currentTextChanged.connect(self._filter_database)
        controls.addWidget(self.database_sort)
        self.database_refresh = QPushButton(_tr('Listeyi Yenile'))
        self.database_refresh.clicked.connect(self._refresh_database)
        controls.addWidget(self.database_refresh)
        layout.addLayout(controls)
        split = QHBoxLayout()
        self.database_list = QListWidget()
        self.database_list.setItemDelegate(ModelListDelegate(self.database_list))
        self.database_list.setUniformItemSizes(True)
        self.database_list.setMouseTracking(True)
        self.database_list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.database_list.setMinimumHeight(285)
        self.database_list.itemSelectionChanged.connect(self._database_selection)
        split.addWidget(self.database_list, 3)
        files_panel = QVBoxLayout()
        self.database_title = self._text('Bir model seç', 'section')
        files_panel.addWidget(self.database_title)
        self.database_license = self._text('')
        files_panel.addWidget(self.database_license)
        self.database_files = QListWidget()
        self.database_files.setMinimumWidth(250)
        self.database_files.setMinimumHeight(180)
        files_panel.addWidget(self.database_files, 1)
        self.database_link = QPushButton(_tr('Model Sayfasını Aç'))
        self.database_link.clicked.connect(self._open_database_page)
        files_panel.addWidget(self.database_link)
        split.addLayout(files_panel, 2)
        layout.addLayout(split, 1)
        actions = QHBoxLayout()
        self.database_download = QPushButton(_tr('İndir ve Kütüphaneye Ekle'))
        self.database_download.setObjectName('primary')
        self.database_download.setEnabled(False)
        self.database_download.clicked.connect(self._download_database_model)
        actions.addWidget(self.database_download)
        self.database_cancel = QPushButton(_tr('İndirmeyi İptal Et'))
        self.database_cancel.setEnabled(False)
        self.database_cancel.clicked.connect(lambda: self.database.cancel.set())
        actions.addWidget(self.database_cancel)
        self.database_progress = QProgressBar()
        self.database_progress.setRange(0, 100)
        actions.addWidget(self.database_progress, 1)
        layout.addLayout(actions)
        self.database_status = self._text('Bu sekmeyi açtığında model listesi yüklenir.')
        layout.addWidget(self.database_status)
        self.database_detail_timer = QTimer(self.root)
        self.database_detail_timer.setSingleShot(True)
        self.database_detail_timer.setInterval(250)
        self.database_detail_timer.timeout.connect(self._fetch_database_selection)
        self.tabs.currentChanged.connect(self._database_tab_changed)

    def _database_tab_changed(self, index):
        if index == self.database_tab_index and not self.database_requested:
            self._refresh_database()

    @property
    def database_tab_index(self):
        return self.tabs.indexOf(self.database_tab_widget)

    def _refresh_database(self):
        self.database_requested = True
        self.database_status.setText(_tr('Hugging Face model listesi yükleniyor…'))
        self.database.refresh()

    def _selected_database_repo(self):
        item = self.database_list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else ''

    def _filter_database(self, *_args):
        from tools.rvc_model_database import package_type
        selected = self._selected_database_repo()
        query = self.database_search.text().strip().casefold().replace('_', ' ')
        package = self.database_filter.currentData() or self.database_filter.currentText()
        rows = [row for row in self.database_rows if query in row['id'].casefold().replace('_', ' ')
                and (package == 'Tüm paketler' or package in package_type(row.get('siblings', [])))]
        ordering = self.database_sort.currentData() or self.database_sort.currentText()
        if ordering == 'En çok beğenilen':
            rows.sort(key=lambda row: (-row.get('likes', 0), row['id'].casefold()))
        elif ordering == 'Son güncellenen':
            rows.sort(key=lambda row: row.get('lastModified', ''), reverse=True)
        else:
            rows.sort(key=lambda row: row['id'].casefold())
        self.database_list.blockSignals(True)
        self.database_list.setUpdatesEnabled(False)
        self.database_list.clear()
        chosen = 0
        for index, row in enumerate(rows):
            name = QListWidgetItem(row['id'])
            name.setData(Qt.ItemDataRole.UserRole, row['id'])
            name.setData(Qt.ItemDataRole.UserRole + 1,
                         dict(row, package=package_type(row.get('siblings', []))))
            name.setToolTip(_tr(row['id']))
            self.database_list.addItem(name)
            if row['id'] == selected:
                chosen = index
        self.database_list.setUpdatesEnabled(True)
        self.database_list.blockSignals(False)
        if rows:
            self.database_list.setCurrentRow(chosen)
        else:
            self.database_detail = None
            self.database_detail_timer.stop()
            self.database_files.clear()
            self.database_title.setText(_tr('Eşleşen model yok'))
            self.database_license.setText(_tr('Arama veya paket filtresini değiştir.'))
            self.database_download.setEnabled(False)
        self.database_status.setText(_tr(f'{len(rows)} / {len(self.database_rows)} model listeleniyor.'))

    def _database_selection(self):
        repo = self._selected_database_repo()
        self.database_detail = None
        self.database_files.clear()
        self.database_download.setEnabled(False)
        if repo:
            self.database_title.setText(_tr(repo.split('/', 1)[1].replace('_', ' ')))
            self.database_license.setText(_tr('Dosyalar kontrol ediliyor…'))
            self.database_detail_timer.start()

    def _fetch_database_selection(self):
        repo = self._selected_database_repo()
        if repo:
            self.database.details(repo)

    def _open_database_page(self):
        from urllib.parse import quote
        repo = self._selected_database_repo()
        if repo:
            QDesktopServices.openUrl(QUrl('https://huggingface.co/' + quote(repo, safe='/')))

    def _download_database_model(self):
        try:
            if self.owner.studio_tools.busy:
                raise ValueError('A/B dönüştürmesi tamamlandıktan sonra model indirebilirsin.')
            if not self.database_detail:
                raise ValueError('Önce bir model seç ve dosyaların yüklenmesini bekle.')
            selected = [self.database_files.item(i).data(Qt.ItemDataRole.UserRole)
                        for i in range(self.database_files.count())
                        if self.database_files.item(i).checkState() == Qt.CheckState.Checked]
            self.database.download(self.database_detail, selected)
            self.database_status.setText(_tr('Model indiriliyor…'))
        except Exception as exc:
            _popup(str(exc), error=True)

    def _database_tick(self):
        from tools.rvc_model_database import package_type
        if self.database_pending_imports and not self.owner.studio_tools.busy and not self.close_when_done:
            repo, files = self.database_pending_imports.pop(0)
            try:
                self._import_database_model(repo, files)
            except Exception as exc:
                self.database_status.setText(_tr(str(exc)))
                _popup(str(exc), error=True)
        for _ in range(30):
            try:
                kind, data = self.database.messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'catalog':
                self.database_rows = data['rows']
                self._filter_database()
                if data.get('cached'):
                    self.database_status.setText(_tr('Bağlantı kurulamadı; son kaydedilen liste gösteriliyor. ' + data.get('error', '')))
            elif kind == 'details' and data['repo'] == self._selected_database_repo():
                detail = self.database_detail = data['data']
                self.database_files.clear()
                files = detail['files']
                direct = any(Path(f['rfilename']).suffix.lower() == '.pth' for f in files)
                zip_available = any(Path(f['rfilename']).suffix.lower() == '.zip' for f in files)
                for asset in files:
                    name = asset['rfilename']
                    size = asset.get('size', asset.get('lfs', {}).get('size', 0)) or 0
                    text = name + (f' · {size / 1024**2:.1f} MB' if size else '')
                    item = QListWidgetItem(text)
                    item.setData(Qt.ItemDataRole.UserRole, name)
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                    suffix = Path(name).suffix.lower()
                    checked = suffix in ('.pth', '.index') and not Path(name).name.lower().startswith('trained_') if direct else suffix == ('.zip' if zip_available else '.rar')
                    item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
                    self.database_files.addItem(item)
                license_name = detail.get('cardData', {}).get('license') if isinstance(detail.get('cardData'), dict) else ''
                self.database_license.setText(_tr('Paket: ' + package_type(files) + (' · Lisans: ' + str(license_name) if license_name else '')))
                if not files:
                    self.database_status.setText(_tr('Bu depoda indirilebilir PTH/INDEX/ZIP/RAR yok. Model sayfasındaki açıklamayı kontrol edebilirsin.'))
            elif kind == 'detail_error' and data['repo'] == self._selected_database_repo():
                self.database_license.setText(_tr(data['text']))
            elif kind == 'error':
                self.database_status.setText(_tr(data['text']))
            elif kind == 'progress':
                self.database_status.setText(_tr(data['text']))
                if data['total']:
                    self.database_progress.setRange(0, 100)
                    self.database_progress.setValue(min(100, int(data['done'] * 100 / data['total'])))
                else:
                    self.database_progress.setRange(0, 0)
            elif kind == 'download_done':
                self.database_progress.setRange(0, 100)
                self.database_progress.setValue(100)
                self.database_status.setText(_tr('Model dosyaları hazır.' + (' Daha önce indirilen dosyalar kullanıldı.' if data['cached'] else '')))
                if not self.close_when_done:
                    if self.owner.studio_tools.busy:
                        self.database_pending_imports.append((data['repo'], data['files']))
                        self.database_status.setText(_tr('Model indirildi; A/B işlemi tamamlanınca kütüphaneye eklenecek.'))
                    else:
                        try:
                            self._import_database_model(data['repo'], data['files'])
                        except Exception as exc:
                            self.database_status.setText(_tr(str(exc)))
                            _popup(str(exc), error=True)
            elif kind in ('download_error', 'download_cancelled'):
                self.database_progress.setRange(0, 100)
                self.database_progress.setValue(0)
                self.database_status.setText(_tr(data.get('text', 'İndirme iptal edildi.')))
        self.database_refresh.setEnabled(not self.database.loading)
        self.database_download.setEnabled(bool(self.database_detail and self.database_detail.get('files')) and not self.database.busy)
        self.database_cancel.setEnabled(self.database.busy)

    def _import_database_model(self, repo, files):
        from tools.rvc_model_database import matching_index
        from tools.rvc_model_library import add_model
        models = sorted(p for p in files if Path(p).suffix.lower() == '.pth')
        indices = sorted(p for p in files if Path(p).suffix.lower() == '.index' and not Path(p).name.lower().startswith('trained_'))
        pth = models[0]
        index = matching_index(pth, indices)
        name = repo.split('/', 1)[1].replace('_', ' ')
        if len(models) > 1 or len(indices) > 1:
            dialog = QDialog(self.root)
            dialog.setWindowTitle(_tr('İndirilen Modeli Kütüphaneye Ekle'))
            dialog.resize(620, 290)
            layout = QVBoxLayout(dialog)
            layout.addWidget(self._text('Paket birden fazla model veya indeks içeriyor. Kullanmak istediğin dosyaları seç.'))
            model_combo, index_combo = QComboBox(), QComboBox()
            for path in models:
                model_combo.addItem(Path(path).name, path)
            index_combo.addItem('İndeks kullanma', '')
            for path in indices:
                index_combo.addItem(Path(path).name, path)
            def suggest(_index=0):
                suggested = matching_index(model_combo.currentData(), indices)
                index_combo.setCurrentIndex(max(0, index_combo.findData(suggested)))
            suggest()
            model_combo.currentIndexChanged.connect(suggest)
            name_edit = QLineEdit(name)
            layout.addWidget(self._text('PTH modeli', 'eyebrow'))
            layout.addWidget(model_combo)
            layout.addWidget(self._text('İndeks · isteğe bağlı', 'eyebrow'))
            layout.addWidget(index_combo)
            layout.addWidget(name_edit)
            buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
            buttons.accepted.connect(dialog.accept)
            buttons.rejected.connect(dialog.reject)
            layout.addWidget(buttons)
            if dialog.exec() != QDialog.DialogCode.Accepted:
                self.database_status.setText(_tr('Dosyalar indirildi; kütüphaneye ekleme iptal edildi. Tekrar İndir düğmesine basınca mevcut dosyalar kullanılır.'))
                return
            pth, index, name = model_combo.currentData(), index_combo.currentData(), name_edit.text()
        running = bool(getattr(self.owner, 'stream', None) and self.owner.stream.active)
        existing = next((p for p in self.owner.profiles if p.get('pth_path') == pth and p.get('index_path', '') == index), None)
        if existing:
            self.owner.profile_choose_model(self.owner.profile_label(existing), self._values(), running)
        else:
            add_model(self.owner, pth, index, name, self._values(), running)
        self.database_status.setText(_tr('Kütüphaneye eklendi: ' + name + (' · indeks yok, indeks katkısı kapalı.' if not index else '')))

    def _clear_log(self):
        from tools.rvc_diagnostics import drain
        drain(1024)
        self.log_view.clear()

    def _log_tick(self):
        if self.log_pause.isChecked():
            return
        from tools.rvc_diagnostics import drain
        text = drain(32)
        if not text:
            return
        bar = self.log_view.verticalScrollBar()
        old = bar.value()
        cursor = QTextCursor(self.log_view.document())
        cursor.movePosition(QTextCursor.MoveOperation.End)
        cursor.insertText(text)
        bar.setValue(bar.maximum() if self.log_follow.isChecked() else old)

    def _build_media(self):
        from tools.rvc_media_ui import SoundpadPage, ConversionPage
        self.soundpad_page = SoundpadPage(self)
        self.conversion_page = ConversionPage(self)
        for widget,title in ((self.soundpad_page,'Soundpad'),(self.conversion_page,'Dosyadan Dönüştür')):
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setWidget(widget)
            scroll.setProperty('studio_tab_id',TAB_IDS[title])
            scroll.setProperty('rvc_tab_title',title)
            index=self.tabs.addTab(scroll,_tr(title))
            self.tabs.tabBar().setTabData(index,TAB_IDS[title])
        self.tabs.tabBar().moveTab(self.tabs.count()-2,2)
        self.tabs.tabBar().moveTab(self.tabs.count()-1,3)

    def tab_id(self,index):
        return self.tabs.widget(index).property('studio_tab_id') if index>=0 else 'voice'

    def reset_tab_order(self):
        for target,identifier in enumerate(DEFAULT_TAB_ORDER):
            index=next(i for i in range(self.tabs.count()) if self.tab_id(i)==identifier)
            self.tabs.tabBar().moveTab(index,target)
        self._save_ui()

    def open_settings(self,section='general'):
        index=next(i for i in range(self.tabs.count()) if self.tab_id(i)=='settings')
        self.tabs.setCurrentIndex(index)
        self.settings_page.open_section(section)

    def _restore_ui(self):
        from PySide6.QtCore import QByteArray
        saved=self.saved_ui
        names=saved.get('tabs',[]) if saved.get('version')==2 else DEFAULT_TAB_ORDER
        names=[name for name in names if name in DEFAULT_TAB_ORDER and name not in ('about','log')]
        names=list(dict.fromkeys(names+[name for name in DEFAULT_TAB_ORDER if name not in ('about','log')]))+['about','log']
        for target,identifier in enumerate(names):
            index=next(i for i in range(self.tabs.count()) if self.tab_id(i)==identifier)
            self.tabs.tabBar().moveTab(index,target)
        current=saved.get('current','voice')
        current=TAB_IDS.get(current,current)
        for i in range(self.tabs.count()):
            if self.tab_id(i)==current:self.tabs.setCurrentIndex(i)
        if isinstance(saved.get('geometry'),str):
            try:self.root.restoreGeometry(QByteArray.fromBase64(saved['geometry'].encode('ascii')))
            except (ValueError,UnicodeError):pass
        self.log_follow.setChecked(bool(saved.get('log_follow',True)))
        self._request_voice('im' if saved.get('voice')=='im' else 'vc')

    def _beta_tick(self):
        self.settings_page.tick()
        from tools.rvc_errors import CENTER,report
        from tools.rvc_user_state import preferences
        from tools.rvc_beta_ui import show_error
        if getattr(self.owner,'_studio_audio_failed',False) and not getattr(self.owner,'_studio_audio_error_stopped',False):
            self.owner._studio_audio_error_stopped=True
            try:self.owner.stop_stream()
            except Exception as exc:report(exc,'Ses akışını durdurma')
        if self.error_dialog is not None:return
        try:item=CENTER.messages.get_nowait()
        except queue.Empty:return
        if item['stop_audio']:
            try:self.owner.stop_stream()
            except Exception as exc:report(exc,'Ses akışını durdurma')
        if preferences()['error_dialogs']:show_error(self,item)

    def _save_ui(self):
        if not hasattr(self,'log_follow'):
            return
        from tools import rvc_ui_state
        try:
            rvc_ui_state.save(self)
        except OSError as exc:
            print('Arayüz ayarları kaydedilemedi: '+str(exc),flush=True)

    def open_setup_wizard(self):
        if self.closed or self._shut_down:
            return
        if self.owner.studio_tools.busy or self.owner.file_converter.busy:
            sg.popup('Dönüştürme işlemi bitince başlangıç sihirbazını açabilirsin.')
            return
        from tools.rvc_audio_setup import SetupWizard
        self.owner.studio_tools.stop_play(restart_monitor=False)
        self.owner.soundpad.stop()
        self.owner.soundpad.before_device_refresh()
        self.owner.stop_stream()
        wizard = None
        try:
            wizard = SetupWizard(self)
            wizard.exec()
        finally:
            if wizard is not None:
                wizard.deleteLater()
            self.owner.soundpad.after_device_refresh()

    def shutdown(self):
        if self._shut_down:
            return
        self._shut_down = True
        self.timer.stop()
        self.profile_service.close()
        try:self.owner.profile_save_values(self._values())
        except Exception as exc:
            from tools.rvc_errors import report
            report(exc,'Profilleri kaydetme')
        self._save_ui()
        self.noise_panel.save_timer.stop()
        self.noise_panel.save()
        self.apbwe_panel.save_timer.stop()
        self.apbwe_panel.save()
        try:
            vals = self._values()
            self.owner.output_monitor.configure(vals['monitor_enabled'], vals['monitor_device'], vals['monitor_volume'], restart=False)
        except Exception as exc:
            print('Dinleme ayarları kaydedilemedi: '+str(exc),flush=True)
        self.hotkeys.close()
        self.gpu.close()
        self.database.close()
        self.owner.file_converter.close()
        self.owner.soundpad.close()
        self.owner.studio_tools.shutdown()
        self.owner.stop_stream()
        self.owner.apbwe.close()
        self.owner.noise_filter.close()

    def _studio_tick(self):
        self._beta_tick()
        tools = self.owner.studio_tools
        for _ in range(30):
            try:
                kind, data = tools.messages.get_nowait()
            except queue.Empty:
                break
            if kind == 'hotkey':
                self._hotkey_action(data['action'])
            elif kind == 'hotkey_status':
                self.hotkey_status.setText(_tr(data['text']))
            elif kind == 'capture_done':
                tools.complete_capture(data['recording'])
            elif kind == 'ab_progress':
                tools.ab_state = data['text']
            elif kind == 'ab_done':
                tools.busy = False
                if data['cancelled']:
                    tools.ab_state = 'A/B işlemi iptal edildi.'
                elif data['error']:
                    tools.ab_state = 'A/B dönüştürme hatası: '+data['error']
                else:
                    tools.results = data['results']
                    tools.ab_state = 'A ve B aynı konuşmadan üretildi. Dinleyip karşılaştırabilirsin.'
                if data['resume'] and not self.close_when_done:
                    self.events.put('start_vc')
        now = time.monotonic()
        interval = .3 if self.root.isMinimized() or not self.root.isVisible() else .1
        if now - getattr(self, '_last_visual_tick', 0) < interval:
            return
        self._last_visual_tick = now
        self._log_tick()
        self._database_tick()
        self.soundpad_page.tick()
        self.conversion_page.tick()
        self.noise_panel.refresh()
        self.apbwe_panel.refresh()
        stream = getattr(self.owner,'stream',None)
        delay = getattr(self.owner,'delay_time',None)
        if stream is not None and stream.active and delay is not None:
            extra = 31 if self.owner.noise_filter.settings['enabled'] and not self.owner.noise_filter.error else 0
            self.widgets['delay_time'].update(round(delay*1000)+extra)
        if time.monotonic()-self._last_ui_save > 1:
            self._save_ui()
            self._last_ui_save = time.monotonic()
        self.widgets['infer_time'].update(getattr(self.owner, '_studio_infer_ms', 0))
        self.widgets['gpu_usage'].update('—' if self.gpu.value is None else f'{self.gpu.value:.0f}')
        if tools.player is not None and tools.play_done.is_set():
            tools.stop_play()
            tools.record_state = 'Dinleme tamamlandı.'
        stale = time.monotonic()-tools.level_time > max(.5, float(getattr(self.owner.gui_config, 'block_time', .25))*2)
        for name, levels in [('input', tools.input_level), ('output', tools.output_level)]:
            rms, peak, clipped = (-60., -60., False) if stale else levels
            bar, label = self._meters[name]
            bar.setValue(max(0, min(600, round((rms+60)*10))))
            if bar.property('studio_clipped') != clipped:
                bar.setProperty('studio_clipped', clipped)
                bar.setStyleSheet('QProgressBar::chunk { background: '+('#F07488' if clipped else '#52D5AD')+'; }')
            label.setText(_tr(('Sessiz' if rms <= -60 else f'{rms:.1f} dBFS · tepe {peak:.1f}') + (' · KIRPILMA' if clipped else '')))
        self.mute_button.setText(_tr('Çıkışı Aç' if tools.muted else 'Çıkışı Sustur'))
        rec = tools.recording
        self.record_button.setText(_tr('■  Kaydı Bitir' if rec else '●  Test Kaydı Al'))
        self.record_label.setText((_tr(f"Kayıt: {rec['pos']/rec['rate']:.1f} / {len(rec['raw'])/rec['rate']:.0f} saniye" if rec else tools.record_state)))
        self.ab_label.setText(_tr(tools.ab_state))
        for name, label in self.ab_labels.items():
            label.setText(_tr(tools.describe(name)))
        self.compare_button.setEnabled(not tools.busy and not self.owner.file_converter.busy)
        self.record_button.setEnabled(not tools.busy and not self.owner.file_converter.busy)
        if self.close_when_done and not tools.busy and not self.database.busy and not self.owner.file_converter.busy:
            self.root.close()

    def _hotkey_action(self, action):
        if getattr(getattr(self, 'hotkeys', None), 'paused', False):
            return
        pad = self.owner.soundpad
        try:
            if action.startswith('soundpad:'):
                pad.toggle(action.split(':',1)[1])
                pad.sync_devices()
                return
            if action.startswith('soundpad_'):
                state = pad.state()
                entry = pad.get(state['id']) if state else self.soundpad_page.selected()
                if action == 'soundpad_pause':
                    pad.pause()
                elif action == 'soundpad_stop':
                    pad.stop()
                elif entry:
                    if action == 'soundpad_selected':
                        selected = self.soundpad_page.selected()
                        if selected: pad.toggle(selected['id'])
                    elif action == 'soundpad_restart':
                        selected = self.soundpad_page.selected()
                        if selected: pad.play(selected['id'],restart=True)
                    elif action.removeprefix('soundpad_') in ('mic','head','both'):
                        pad.update(entry['id'],route=action.removeprefix('soundpad_'))
                        self.soundpad_page.rebuild()
                pad.sync_devices()
                return
            if action == 'rnnoise_toggle':
                self.noise_panel.toggle()
                return
            if action == 'apbwe_toggle':
                self.apbwe_panel.enabled.setChecked(not self.apbwe_panel.enabled.isChecked())
                return
        except Exception as exc:
            pad.status = str(exc)
            return
        tools = self.owner.studio_tools
        if tools.busy or self.owner.file_converter.busy:
            tools.ab_state = 'A/B işlemi sürüyor; kısayol işlem tamamlanınca kullanılabilir.'
            return
        if action == 'toggle_vc':
            stream = getattr(self.owner, 'stream', None)
            self.events.put('stop_vc' if stream is not None and stream.active else 'start_vc')
        elif action == 'toggle_voice':
            current = getattr(self.owner, '_studio_requested_mode', getattr(self.owner, 'function', 'vc'))
            self._request_voice('im' if current == 'vc' else 'vc')
        elif action == 'monitor':
            handle = self.widgets['monitor_enabled']
            handle.update(not handle.get())
            self.events.put('monitor_enabled')
        elif action in ('prev_model', 'next_model'):
            listing = self.widgets['profile_select'].widget
            if listing.count():
                current = listing.currentRow()
                listing.setCurrentRow((current + (-1 if action == 'prev_model' else 1)) % listing.count())
        else:
            event = {'clear_cache':'studio_clear_cache', 'mute':'studio_mute', 'record':'studio_record', 'play_test':'studio_play_test',
                     'stop_play':'studio_stop_play', 'play_a':'studio_play_A', 'play_b':'studio_play_B'}.get(action)
            if event:
                self._studio_event(event)

    def _studio_event(self, event):
        tools = self.owner.studio_tools
        try:
            if event == 'studio_clear_cache':
                message = self.owner.clear_audio_cache()
                self.clear_cache_status.setText(_tr(message))
                self.cache_toolbar_button.setToolTip(_tr(message))
                self.cache_toolbar_button.setText(_tr('Tamamlandı' if 'temizlendi' in message else 'Önce Sesi Başlat'))
                QTimer.singleShot(2000, self.root, lambda: self.cache_toolbar_button.setText(_tr('Gecikmeyi Sıfırla')))
            elif event == 'studio_mute':
                tools.muted = not tools.muted
                self.owner.apbwe.reset()
            elif event == 'studio_record':
                if self.owner.file_converter.busy:
                    raise RuntimeError('Dosya dönüşümü bitince test kaydı alabilirsin.')
                tools.record(float(self.widgets['studio_seconds'].get()), self._values())
            elif event == 'studio_stop_play':
                tools.stop_play()
            elif event.startswith('studio_play_'):
                tools.play(event.removeprefix('studio_play_'))
            elif event.startswith('studio_snapshot_'):
                tools.snapshot(event.removeprefix('studio_snapshot_'), self._values())
            elif event == 'studio_compare':
                if self.owner.file_converter.busy:
                    raise RuntimeError('Dosya dönüşümü bitince A/B karşılaştırmasını başlatabilirsin.')
                tools.compare()
            elif event.startswith('studio_export_'):
                name = event.removeprefix('studio_export_')
                path, _ = QFileDialog.getSaveFileName(self.root, _tr('Dönüştürülmüş sesi kaydet'), 'rvc_'+name+'.wav', _tr('WAV (*.wav)'))
                if path:
                    tools.export(path, name)
                    tools.record_state = 'WAV kaydedildi: '+path
            elif event == 'studio_hotkeys_apply':
                bindings = {action: self.widgets['hotkey_'+action].get() for action in self.hotkey_editors}
                codes = {action: editor.capture for action, editor in self.hotkey_editors.items() if editor.capture}
                self.hotkeys.apply(bindings, self.widgets['studio_hotkeys_enabled'].get(), self.widgets['studio_hotkeys_game'].get(), codes)
                self.clear_cache_status.setText(_tr('Kısayol: ' + (bindings['clear_cache'] or 'atanmadı') +
                                               ' · Ses tamponlarını temizler.'))
                for button in self.hotkey_save_buttons.values():
                    button.setText(_tr('Kaydedildi'))
                self.root.setFocus()
            elif event == 'studio_hotkeys_defaults':
                from tools.rvc_hotkeys import ACTIONS
                for action, (_, sequence) in ACTIONS.items():
                    self.widgets['hotkey_'+action].update(sequence)
                self.hotkey_status.setText(_tr('Varsayılan tuşlar dolduruldu. Etkinleştirmek için Kaydet ve Uygula düğmesine bas.'))
        except Exception as exc:
            tools.record_state = str(exc)
            _popup(str(exc), error=True)

    def _file_browse(self, event):
        key, filt = self._local_events[event]
        path, _ = QFileDialog.getOpenFileName(self.root, _tr('Dosya seç'), '', _tr(filt))
        if path:
            self.widgets[key].update(value=path)


def create_window(owner, data):
    window = Window(owner, data)
    finish_startup()
    return window


_startup = None
_startup_app = None
_startup_label = None


def startup_progress(message):
    global _startup, _startup_app, _startup_label
    print(message, flush=True)
    if _startup is None:
        _startup_app = QApplication.instance() or QApplication(sys.argv[:1])
        icon = configure_branding(_startup_app)
        _startup_app.setStyle('Fusion')
        _startup_app.setStyleSheet(STYLESHEET)
        _startup = QDialog()
        _startup.setWindowTitle(_tr('RVC Studio'))
        _startup.setWindowIcon(icon)
        _startup.setMinimumSize(490, 180)
        layout = QVBoxLayout(_startup)
        heading = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(QPixmap(str(ASSETS / 'rvc_logo.png')).scaled(
            58, 58, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        heading.addWidget(logo)
        brand = QLabel(_tr('RVC Studio başlatılıyor'))
        brand.setObjectName('hero')
        heading.addWidget(brand, 1)
        layout.addLayout(heading)
        _startup_label = QLabel()
        _startup_label.setWordWrap(True)
        layout.addWidget(_startup_label)
        layout.addWidget(QLabel(_tr('İlk açılışta ses motorunun yüklenmesi zaman alabilir.')))
        _startup.show()
    _startup_label.setText(_tr(message))
    _startup_app.processEvents()


def finish_startup():
    global _startup
    if _startup is not None:
        _startup.close()
        _startup = None
    import faulthandler
    faulthandler.cancel_dump_traceback_later()


def _app():
    return QApplication.instance() or QApplication(sys.argv[:1])


def _popup(message, title='RVC Studio', error=False):
    app = _app()
    box = QMessageBox()
    box.setWindowTitle(_tr(title))
    box.setText(_tr(str(message)))
    box.setIcon(QMessageBox.Icon.Critical if error else QMessageBox.Icon.Information)
    box.exec()


def _get_file(prompt, file_types=None, **kwargs):
    patterns = 'Tüm dosyalar (*)'
    if file_types:
        filters = []
        for name, ext in file_types:
            filters.append(f'{name} ({ext})')
        patterns = ';;'.join(filters)
    return QFileDialog.getOpenFileName(None, _tr(str(prompt)), '', _tr(patterns))[0]


def _get_text(prompt, default_text='', **kwargs):
    value, ok = QInputDialog.getText(None, _tr('RVC Studio'), _tr(str(prompt)), text=str(default_text))
    return value if ok else None


def _yes_no(prompt, **kwargs):
    result = QMessageBox.question(None, _tr('RVC Studio'), _tr(str(prompt)),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No)
    return 'Yes' if result == QMessageBox.StandardButton.Yes else 'No'


sg = SimpleNamespace(
    WINDOW_CLOSED='__RVC_QT_CLOSED__',
    popup=lambda message, **kwargs: _popup(message, **kwargs),
    popup_error=lambda message, **kwargs: _popup(message, error=True, **kwargs),
    popup_get_file=_get_file,
    popup_get_text=_get_text,
    popup_yes_no=_yes_no,
)
