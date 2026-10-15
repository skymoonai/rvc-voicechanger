"""Queue errors from worker/audio threads; dialogs are created only on the UI thread."""
from __future__ import annotations
import logging
from logging.handlers import RotatingFileHandler
import queue
import sys
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from tools.rvc_user_state import data_root, now

class ErrorCenter:
    def __init__(self):
        self.messages=queue.Queue(maxsize=32)
        self.lock=threading.Lock()
        self.recent=OrderedDict()
        self.logger=None
        self.installed=False
        self.last=None

    def _log(self):
        if self.logger is None:
            logger=logging.getLogger('rvcstudio.errors')
            logger.propagate=False
            logger.setLevel(logging.ERROR)
            try:
                path=data_root()/'logs'/'errors.log'
                path.parent.mkdir(parents=True,exist_ok=True)
                handler=RotatingFileHandler(path,maxBytes=2*1024*1024,backupCount=3,encoding='utf-8')
            except OSError:handler=logging.StreamHandler(sys.__stderr__)
            handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
            logger.addHandler(handler)
            self.logger=logger
        return self.logger

    def report(self,exc,context='İşlem',*,stop_audio=False):
        if isinstance(exc,(KeyboardInterrupt,SystemExit)):return
        key=(str(context),type(exc).__name__,str(exc)[:250])
        with self.lock:
            stamp=time.monotonic()
            if stamp-self.recent.get(key,-1000)<8:return
            self.recent[key]=stamp
            self.recent.move_to_end(key)
            while len(self.recent)>64:self.recent.popitem(last=False)
        item={'id':uuid.uuid4().hex[:10],'at':now(),'context':str(context),
              'type':type(exc).__name__,'message':str(exc)[:2000],
              'detail':''.join(traceback.format_exception(type(exc),exc,exc.__traceback__))[-16000:],
              'stop_audio':bool(stop_audio)}
        self.last=item
        try:self._log().error('%s · %s\n%s',item['id'],item['context'],item['detail'])
        except Exception:pass
        try:self.messages.put_nowait(item)
        except queue.Full:
            try:self.messages.get_nowait()
            except queue.Empty:pass
            try:self.messages.put_nowait(item)
            except queue.Full:pass

    def install(self):
        if self.installed:return
        self.installed=True
        self.old_sys=sys.excepthook
        self.old_thread=threading.excepthook
        def system(kind,error,tb):
            if issubclass(kind,(KeyboardInterrupt,SystemExit)):return self.old_sys(kind,error,tb)
            self.report(error.with_traceback(tb),'Arayüz işlemi')
        def worker(args):
            if args.exc_type is SystemExit:return
            self.report(args.exc_value.with_traceback(args.exc_traceback),'Arka plan işlemi')
        sys.excepthook=system
        threading.excepthook=worker

    def uninstall(self):
        if self.installed:
            sys.excepthook=self.old_sys
            threading.excepthook=self.old_thread
            self.installed=False

CENTER=ErrorCenter()

def report(exc,context='İşlem',**options):CENTER.report(exc,context,**options)

def install_engine_guards(cls):
    if getattr(cls,'_studio_error_guards',False):return
    original_audio,original_start,original_events=cls.audio_callback,cls.start_stream,cls.event_handler
    cls._studio_original_event_handler=original_events
    def audio(owner,indata,outdata,*args):
        if getattr(owner,'_studio_audio_failed',False):
            outdata.fill(0);return
        try:return original_audio(owner,indata,outdata,*args)
        except Exception as exc:
            outdata.fill(0)
            owner._studio_audio_failed=True
            CENTER.report(exc,'Canlı ses dönüşümü',stop_audio=True)
    def start(owner,*args,**kwargs):
        owner._studio_audio_failed=False
        owner._studio_audio_error_stopped=False
        return original_start(owner,*args,**kwargs)
    def events(owner,*args,**kwargs):
        while True:
            try:return original_events(owner,*args,**kwargs)
            except Exception as exc:
                CENTER.report(exc,'Ses / model işlemi',stop_audio=True)
                try:owner.stop_stream()
                except Exception as stop_error:CENTER.report(stop_error,'Ses akışını durdurma')
                if getattr(getattr(owner,'window',None),'closed',False):return
                time.sleep(.025)
    cls.audio_callback,cls.start_stream,cls.event_handler=audio,start,events
    cls._studio_error_guards=True
