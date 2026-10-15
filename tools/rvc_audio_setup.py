"""First-run device setup with short, local tests; no voice model is needed."""
from __future__ import annotations
from tools.rvc_i18n import t as _tr
import json
import queue
import re
import threading
from pathlib import Path
import numpy as np
import sounddevice as sd
from tools.rvc_audio_files import atomic_json


def setup_complete(root):
    try:
        return json.loads((Path(root)/'configs'/'audio_setup.json').read_text(encoding='utf-8')).get('completed') is True
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def cable_input(devices, output_index):
    """Find the matching recording endpoint; never guess across host APIs."""
    output = devices[output_index]
    base = output['name'].split('(')[0].strip()
    expected = re.sub(r'\binput\b', 'output', base, flags=re.I).casefold()
    if expected == base.casefold():
        return None
    return next((i for i,d in enumerate(devices)
                 if d['hostapi'] == output['hostapi'] and d['max_input_channels'] > 0
                 and d['name'].split('(')[0].strip().casefold() == expected), None)


def tone_detected(samples, rate):
    if len(samples) < rate*.1 or float(np.max(np.abs(samples), initial=0)) < .002:
        return False
    spectrum = np.abs(np.fft.rfft(samples*np.hanning(len(samples))))
    hz = np.fft.rfftfreq(len(samples), 1/rate)
    band = spectrum[np.abs(hz-997) < 15]
    floor = spectrum[(hz > 100) & (hz < min(4000,rate/2)) & (np.abs(hz-997)>60)]
    return bool(len(band) and band.max() > max(1e-7, float(np.sqrt(np.mean(floor**2)))*10))


def save_choices(root, choices, tests):
    """Merge device choices, preserving every model/filter setting."""
    root = Path(root)
    config_path = root/'configs'/'config.json'
    try:
        config = json.loads(config_path.read_text(encoding='utf-8-sig'))
        if not isinstance(config, dict):
            raise ValueError('RVC ayar dosyası geçersiz; üzerine yazılmadı.')
    except FileNotFoundError:
        config = {}
    config.update(sg_hostapi=choices['host'], sg_input_device=choices['mic_name'],
                  sg_output_device=choices['cable_name'], sg_wasapi_exclusive=False)
    atomic_json(config_path, config)
    # Device IDs are deliberately absent: Windows may reorder them on reboot.
    stored = {k:choices[k] for k in ('host','mic_name','cable_name','head_label')}
    atomic_json(root/'configs'/'audio_setup.json', dict(version=1,completed=True,devices=stored,tests=tests))


class AudioProbe:
    """Finite tests on one worker. All device opens/writes are off the UI thread."""
    def __init__(self):
        self.jobs, self.messages = queue.Queue(), queue.Queue()
        self.closed = threading.Event()
        self.cancel = threading.Event()
        self.streams = []
        self.busy = False
        self.clip = None
        self.thread = threading.Thread(target=self._run, name='RVC-device-test', daemon=True)
        self.thread.start()

    def submit(self, kind, choices):
        if self.busy or self.closed.is_set():
            return
        self.cancel = threading.Event()
        self.busy = True
        self.jobs.put((kind, dict(choices), self.cancel))

    def cancel_test(self):
        self.cancel.set()
        for stream in tuple(self.streams):
            try:
                stream.abort()
            except Exception:
                pass

    def _play(self, index, cancel, audio=None, source_rate=None):
        info = sd.query_devices(index)
        rate, channels = int(round(info['default_samplerate'])), min(2,int(info['max_output_channels']))
        if channels < 1:
            raise ValueError('Seçilen cihaz bir ses çıkışı değil.')
        if audio is None:
            audio = (.09*np.sin(2*np.pi*997*np.arange(rate)/rate)).astype(np.float32)
            fade = max(1,rate//50)
            audio[:fade] *= np.linspace(0,1,fade,dtype=np.float32)
            audio[-fade:] *= np.linspace(1,0,fade,dtype=np.float32)
        else:
            from math import gcd
            from scipy.signal import resample_poly
            if source_rate != rate:
                g=gcd(int(source_rate),rate)
                audio=resample_poly(audio,rate//g,int(source_rate)//g,window=('kaiser',8.6)).astype(np.float32)
            audio=np.clip(audio*.8,-.8,.8)
        with sd.OutputStream(device=index,samplerate=rate,channels=channels,dtype='float32',latency='high') as stream:
            self.streams.append(stream)
            try:
                for pos in range(0,len(audio),max(1,rate//20)):
                    if cancel.is_set():
                        raise InterruptedError()
                    block=audio[pos:pos+rate//20,None]
                    stream.write(np.ascontiguousarray(np.repeat(block,channels,axis=1)))
            finally:
                self.streams.remove(stream)

    def _microphone(self, choices, cancel):
        info=sd.query_devices(choices['mic'])
        rate=int(round(info['default_samplerate']))
        blocks=[]
        peak=0.0
        with sd.InputStream(device=choices['mic'],samplerate=rate,channels=1,dtype='float32',latency='high') as stream:
            self.streams.append(stream)
            try:
                for _ in range(60):
                    if cancel.is_set():
                        raise InterruptedError()
                    data,overflow=stream.read(max(1,rate//20))
                    if overflow:
                        raise RuntimeError('Mikrofon testinde ses tamponu taştı; cihazı veya ses API’sini değiştirip tekrar dene.')
                    x=data[:,0].copy()
                    blocks.append(x)
                    peak=max(peak,float(np.max(np.abs(x),initial=0)))
                    self.messages.put(('level',float(np.sqrt(np.mean(x*x)))))
            finally:
                self.streams.remove(stream)
        self.clip=(np.concatenate(blocks),rate)
        return {'ok':peak>.0001, 'peak':peak}

    def _cable(self, choices, cancel):
        devices=sd.query_devices()
        paired=cable_input(devices,choices['cable'])
        receiver=None
        captured=None
        position=0
        rate=0
        try:
            if paired is not None:
                rate=int(round(devices[paired]['default_samplerate']))
                captured=np.zeros(rate*3,np.float32)
                def capture(data, frames, times, status):
                    nonlocal position
                    count=min(frames,len(captured)-position)
                    if count:
                        captured[position:position+count]=data[:count,0]
                        position+=count
                receiver=sd.InputStream(device=paired,samplerate=rate,channels=1,dtype='float32',callback=capture,latency='high')
                self.streams.append(receiver)
                receiver.start()
            self._play(choices['cable'],cancel)
            if cancel.wait(.2):
                raise InterruptedError()
            detected=tone_detected(captured[:position],rate) if captured is not None else None
            return {'opened':True, 'loopback':detected, 'input_name':devices[paired]['name'] if paired is not None else ''}
        finally:
            if receiver is not None:
                receiver.abort()
                receiver.close()
                self.streams.remove(receiver)

    def _run(self):
        while not self.closed.is_set():
            job=self.jobs.get()
            if job is None:
                break
            kind, choices, cancel=job
            completion=None
            try:
                if kind=='mic':
                    result=self._microphone(choices,cancel)
                elif kind=='cable':
                    result=self._cable(choices,cancel)
                else:
                    clip=self.clip if kind=='listen' else None
                    self._play(choices['head'],cancel,*(clip or (None,None)))
                    result={'ok':True}
                if not cancel.is_set():
                    completion=(kind,result,'')
            except Exception as exc:
                if not cancel.is_set():
                    completion=(kind,{},str(exc))
            finally:
                self.busy=False
            if completion is not None and not cancel.is_set():
                self.messages.put(('done',completion))

    def close(self):
        self.cancel_test()
        self.closed.set()
        self.jobs.put(None)
        self.thread.join(timeout=1)
        self.clip=None


from PySide6.QtCore import Qt, QTimer, QSignalBlocker, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QDialog,QWidget,QVBoxLayout,QHBoxLayout,QGridLayout,
                              QLabel,QComboBox,QPushButton,QCheckBox,QProgressBar,QStackedWidget,QFrame,QScrollArea)


class SetupWizard(QDialog):
    def __init__(self, window):
        super().__init__(window.root)
        self.window=window
        self.owner=window.owner
        self.root=Path(__file__).resolve().parent.parent
        self.setWindowTitle(_tr('RVC Studio · İlk Ses Kurulumu'))
        self.resize(820,690)
        self.setMinimumSize(660,540)
        self.probe=AudioProbe()
        self.results={}
        self.index=0
        layout=QVBoxLayout(self)
        layout.setContentsMargins(25,24,25,22)
        layout.setSpacing(15)
        title=QLabel(_tr('Ses bağlantılarını hazırlayalım'))
        title.setObjectName('hero')
        layout.addWidget(title)
        self.steps=QLabel(_tr('1  Cihazlar     →     2  Ses testleri     →     3  Hazır'))
        self.steps.setObjectName('eyebrow')
        layout.addWidget(self.steps)
        self.pages=QStackedWidget()
        layout.addWidget(self.pages,1)
        self.device_page=self._page()
        self.host,self.mic,self.cable,self.head=(QComboBox() for _ in range(4))
        for caption,widget in (('Ses API’si',self.host),('Mikrofonun',self.mic),
                               ('Sanal kablo · uygulamanın ses çıkışı',self.cable),('Kulaklığın · dinleme çıkışı',self.head)):
            self.device_page.addWidget(QLabel(_tr(caption)))
            self.device_page.addWidget(widget)
        hint=QLabel(_tr('VB-CABLE kullanıyorsan burada CABLE Input seç. Oyun veya Discord mikrofonunda CABLE Output seçilir. Kulaklığı ayrı seçerek kendi sesini dinleyebilirsin.'))
        hint.setWordWrap(True)
        hint.setObjectName('subtitle')
        self.device_page.addWidget(hint)
        row=QHBoxLayout()
        refresh=QPushButton(_tr('Cihazları Yenile'))
        refresh.clicked.connect(self.refresh)
        row.addWidget(refresh)
        download=QPushButton(_tr('VB-CABLE Kurulum Sayfası'))
        download.clicked.connect(lambda:QDesktopServices.openUrl(QUrl('https://vb-audio.com/Cable/index.htm')))
        row.addWidget(download)
        row.addStretch()
        self.device_page.addLayout(row)
        self.test_page=self._page()
        intro=QLabel(_tr('Bu testlerde ses modeli kullanılmaz. Mikrofon testi 3 saniyelik kaydı yalnız bellekte tutar; kaydı kulaklığında dinleyebilirsin.'))
        intro.setWordWrap(True)
        self.test_page.addWidget(intro)
        self.test_buttons={}
        self.test_labels={}
        for kind,title,caption in (('mic','Mikrofonu Test Et','Konuşarak giriş seviyesini kontrol et.'),
                                    ('head','Kulaklığı Test Et','Kulaklıkta kısa bir test sesi çal.'),
                                    ('cable','Sanal Kabloyu Test Et','Kabloya test sesi gönder; eşleşen giriş varsa geri dönüşü ölç.')):
            card=QFrame()
            card.setObjectName('card')
            contents=QVBoxLayout(card)
            line=QHBoxLayout()
            button=QPushButton(_tr(title))
            button.clicked.connect(lambda _checked=False,k=kind:self.test(k))
            line.addWidget(button)
            status=QLabel(_tr(caption))
            status.setWordWrap(True)
            line.addWidget(status,1)
            contents.addLayout(line)
            self.test_buttons[kind]=button
            self.test_labels[kind]=status
            if kind=='mic':
                self.meter=QProgressBar()
                self.meter.setRange(0,600)
                self.meter.setTextVisible(False)
                contents.addWidget(self.meter)
                self.listen=QPushButton(_tr('Mikrofon Kaydını Kulaklıkta Dinle'))
                self.listen.setEnabled(False)
                self.listen.clicked.connect(lambda:self.test('listen'))
                contents.addWidget(self.listen)
            self.test_page.addWidget(card)
        self.heard=QCheckBox(_tr('Kulaklıktaki test sesini duydum'))
        self.heard.toggled.connect(self.update_navigation)
        self.test_page.addWidget(self.heard)
        self.manual=QCheckBox(_tr('Otomatik eşleşme bulunamadı; kablo girişini başka uygulamada kendim test ettim'))
        self.manual.setVisible(False)
        self.manual.toggled.connect(self.update_navigation)
        self.test_page.addWidget(self.manual)
        self.ready_page=self._page()
        self.summary=QLabel(_tr(''))
        self.summary.setWordWrap(True)
        self.summary.setObjectName('section')
        self.ready_page.addWidget(self.summary)
        self.ready_page.addWidget(QLabel(_tr('Seçimler kaydedilecek. Sonradan Giriş ve Çıkış bölümünden bu sihirbazı tekrar açabilirsin.')))
        self.monitor=QCheckBox(_tr('Dönüşümü başlattığımda kendi sesimi kulaklıkta dinle'))
        self.monitor.setChecked(self.owner.output_monitor.enabled)
        self.ready_page.addWidget(self.monitor)
        self.ready_page.addStretch()
        self.status=QLabel(_tr(''))
        self.status.setWordWrap(True)
        self.status.setObjectName('subtitle')
        layout.addWidget(self.status)
        footer=QHBoxLayout()
        later=QPushButton(_tr('Daha Sonra'))
        later.clicked.connect(self.reject)
        footer.addWidget(later)
        footer.addStretch()
        self.back=QPushButton(_tr('Geri'))
        self.back.clicked.connect(lambda:self.go(-1))
        footer.addWidget(self.back)
        self.next=QPushButton(_tr('Ses Testlerine Geç'))
        self.next.setObjectName('primary')
        self.next.clicked.connect(lambda:self.go(1))
        footer.addWidget(self.next)
        layout.addLayout(footer)
        self.host.currentIndexChanged.connect(self.populate_host)
        for widget in (self.mic,self.cable,self.head):
            widget.currentIndexChanged.connect(self.invalidate)
        self.refresh()
        self.timer=QTimer(self)
        self.timer.timeout.connect(self.tick)
        self.timer.start(100)
        self.finished.connect(self.cleanup)

    def _page(self):
        widget=QWidget()
        widget.setObjectName('root')
        layout=QVBoxLayout(widget)
        layout.setContentsMargins(0,0,0,0)
        layout.setSpacing(9)
        area=QScrollArea()
        area.setWidgetResizable(True)
        area.setWidget(widget)
        self.pages.addWidget(area)
        return layout

    def refresh(self):
        if self.probe.busy:
            self.status.setText(_tr('Önce devam eden testi bitir.'))
            return
        try:
            self.devices=list(sd.query_devices())
            self.apis=list(sd.query_hostapis())
            current=self.host.currentText() or self.window.widgets['sg_hostapi'].get()
            with QSignalBlocker(self.host):
                self.host.clear()
                for i,api in enumerate(self.apis):
                    if any(d['hostapi']==i and d['max_input_channels']>0 for d in self.devices):
                        self.host.addItem(api['name'],i)
                chosen=self.host.findText(current)
                if chosen<0:
                    chosen=self.host.findText('Windows WASAPI')
                self.host.setCurrentIndex(max(0,chosen))
            self.populate_host()
            with QSignalBlocker(self.head):
                self.head.clear()
                monitor=self.owner.output_monitor
                for name in monitor.refresh():
                    self.head.addItem(name,monitor.devices[name])
                self.head.setCurrentIndex(max(0,self.head.findText(monitor.selected)))
            self.invalidate()
            self.status.setText(_tr('Cihazlar hazır. Sanal kablo görünmüyorsa sürücüyü kurup uygulamayı yeniden aç.'))
        except Exception as exc:
            self.status.setText(_tr('Cihazlar okunamadı: '+str(exc)))

    def populate_host(self,*_args):
        host=self.host.currentData()
        with QSignalBlocker(self.mic),QSignalBlocker(self.cable):
            self.mic.clear()
            self.cable.clear()
            for i,device in enumerate(self.devices):
                if device['hostapi']!=host:
                    continue
                if device['max_input_channels']>0:
                    self.mic.addItem(device['name'],i)
                if device['max_output_channels']>0:
                    self.cable.addItem(device['name'],i)
            chosen=self.mic.findText(self.window.widgets['sg_input_device'].get())
            self.mic.setCurrentIndex(max(0,chosen))
            saved=self.cable.findText(self.window.widgets['sg_output_device'].get())
            virtual=next((i for i in range(self.cable.count()) if re.search(r'cable|voicemeeter|virtual',self.cable.itemText(i),re.I)),-1)
            saved_virtual=saved>=0 and re.search(r'cable|voicemeeter|virtual',self.cable.itemText(saved),re.I)
            self.cable.setCurrentIndex(saved if saved_virtual else virtual if virtual>=0 else saved)
        self.invalidate()

    def invalidate(self,*_args):
        self.probe.cancel_test()
        while True:
            try:
                self.probe.messages.get_nowait()
            except queue.Empty:
                break
        self.results={}
        self.probe.clip=None
        self.heard.setChecked(False)
        self.manual.setChecked(False)
        self.manual.setVisible(False)
        self.listen.setEnabled(False)
        self.update_navigation()

    def choices(self):
        values=dict(host=self.host.currentText(),mic=self.mic.currentData(),cable=self.cable.currentData(),
                    head=self.head.currentData(),mic_name=self.mic.currentText(),cable_name=self.cable.currentText(),head_label=self.head.currentText())
        if any(values[k] is None for k in ('mic','cable','head')):
            raise ValueError('Mikrofon, sanal kablo ve kulaklık çıkışlarını seç.')
        if self.devices[values['cable']]['name']==self.devices[values['head']]['name']:
            raise ValueError('Sanal kablo ve kulaklık için ayrı çıkışlar seç.')
        return values

    def tests_ready(self):
        cable=self.results.get('cable',{})
        return bool(self.results.get('mic',{}).get('ok') and self.results.get('head',{}).get('ok') and self.heard.isChecked()
                    and cable.get('opened') and (cable.get('loopback') is True or (cable.get('loopback') is None and self.manual.isChecked())))

    def update_navigation(self,*_args):
        self.back.setEnabled(self.index>0 and not self.probe.busy)
        try:
            self.choices()
            devices_ready=True
        except (ValueError,AttributeError,IndexError):
            devices_ready=False
        self.next.setEnabled(not self.probe.busy and (devices_ready if self.index==0 else self.tests_ready()))
        self.next.setText(_tr(('Ses Testlerine Geç','Sonuçları Gör','Kaydet ve Uygulamaya Geç')[self.index]))

    def test(self,kind):
        if self.probe.busy:
            return
        try:
            choices=self.choices()
            if kind=='head':
                self.heard.setChecked(False)
            self.probe.submit(kind,choices)
            self.status.setText(_tr('3 saniye konuş…' if kind=='mic' else 'Test sesi çalınıyor…'))
            self.update_navigation()
        except Exception as exc:
            self.status.setText(_tr(str(exc)))

    def tick(self):
        while True:
            try:
                kind,value=self.probe.messages.get_nowait()
            except queue.Empty:
                break
            if kind=='level':
                db=20*np.log10(max(1e-9,value))
                self.meter.setValue(int(max(0,min(600,(db+60)*10))))
            elif kind=='done':
                target,result,error=value
                if error:
                    self.results.pop(target,None)
                    self.status.setText(_tr('Test tamamlanamadı: '+error))
                    if target in self.test_labels:
                        self.test_labels[target].setText(_tr('Test başarısız. Cihaz seçimini kontrol et.'))
                elif target!='listen':
                    self.results[target]=result
                    if target=='mic':
                        self.test_labels[target].setText(_tr('Mikrofon sinyali alındı.' if result['ok'] else 'Ses alınamadı. Mikrofon iznini, fiziksel susturmayı ve giriş seviyesini kontrol et.'))
                        self.listen.setEnabled(result['ok'])
                    elif target=='head':
                        self.test_labels[target].setText(_tr('Test sesi gönderildi. Duyduysan aşağıdaki kutuyu işaretle.'))
                    else:
                        check=result.get('loopback')
                        self.test_labels[target].setText(_tr('Kablo geri dönüşü doğrulandı.' if check is True else
                            'Çıkış açıldı; eşleşen giriş bulunamadı. Başka uygulamada test ederek doğrula.' if check is None else
                            'Kablo girişinde test sesi bulunamadı. CABLE Input / Output eşleşmesini kontrol et.'))
                        self.manual.setVisible(check is None)
                    self.status.setText(_tr('Test tamamlandı.'))
                else:
                    self.status.setText(_tr('Mikrofon kaydı kulaklıkta oynatıldı.'))
        for button in self.test_buttons.values():
            button.setEnabled(not self.probe.busy)
        self.listen.setEnabled(not self.probe.busy and self.probe.clip is not None)
        self.update_navigation()

    def go(self,direction):
        if self.probe.busy:
            return
        if direction>0 and self.index==2:
            self.commit()
            return
        if direction>0:
            try:
                choices=self.choices()
            except ValueError as exc:
                self.status.setText(_tr(str(exc)))
                return
            if self.index==1:
                if not self.tests_ready():
                    return
                cable=self.results['cable']
                input_name=cable.get('input_name') or 'Sanal kablonun kayıt / mikrofon ucu'
                self.summary.setText(_tr('Ses bağlantıları hazır.\n\nMikrofon: '+choices['mic_name']+'\nSanal kablo: '+choices['cable_name']+
                    '\nKulaklık: '+choices['head_label']+'\n\nOyun / Discord mikrofonunda seç: '+input_name+
                    '\n\nSes modelini uygulamada seçip SESİ BAŞLAT düğmesine basabilirsin.'))
        self.index=max(0,min(2,self.index+direction))
        self.pages.setCurrentIndex(self.index)
        if self.index==1:
            self.status.setText(_tr('Mikrofon testi sırasında konuş. Kulaklık ve sanal kablo testlerini sırayla çalıştır.'))
        elif self.index==2:
            self.status.setText(_tr('Ses testleri tamamlandı. Seçimlerini kaydedebilirsin.'))
        self.update_navigation()

    def commit(self):
        try:
            choices=self.choices()
            if not self.tests_ready():
                raise ValueError('Önce ses testlerini tamamla.')
            self.owner.gui_config.sg_hostapi=choices['host']
            self.owner.gui_config.sg_input_device=choices['mic_name']
            self.owner.gui_config.sg_output_device=choices['cable_name']
            self.owner.update_devices(hostapi_name=choices['host'])
            for key,values,selected in (('sg_hostapi',self.owner.hostapis,choices['host']),
                                        ('sg_input_device',self.owner.input_devices,choices['mic_name']),
                                        ('sg_output_device',self.owner.output_devices,choices['cable_name'])):
                self.window.widgets[key].update(values=values,value=selected)
            self.window.widgets['sg_wasapi_exclusive'].update(False)
            monitor=self.owner.output_monitor
            monitor.configure(self.monitor.isChecked(),choices['head_label'],monitor.volume*100,restart=False)
            self.window.widgets['monitor_device'].update(values=list(monitor.devices),value=choices['head_label'])
            self.window.widgets['monitor_enabled'].update(self.monitor.isChecked())
            self.window._save_ui()
            save_choices(self.root,choices,self.results)
            self.accept()
        except Exception as exc:
            self.status.setText(_tr('Ayarlar kaydedilemedi: '+str(exc)))

    def cleanup(self,*_args):
        self.timer.stop()
        self.probe.close()
