"""Immutable audio views, bounded streaming edits and a single editor worker.

Undo stores views and effect parameters, never complete audio arrays. Rendering
uses one-second blocks and happens off the audio and Qt threads.
"""
from __future__ import annotations
from dataclasses import dataclass, replace
from pathlib import Path
import math
import os
import queue
import shutil
import tempfile
import threading
import uuid
import numpy as np
from tools.rvc_audio_files import RATE
from tools.rvc_audio_quality import pcm_view


@dataclass(frozen=True)
class Audio:
    kind: str
    frames: int
    channels: int
    args: tuple

    @classmethod
    def source(cls, path, frames, channels):
        if frames < 1 or channels not in (1, 2):
            raise ValueError('Ses verisi boş veya kanal sayısı geçersiz.')
        return cls('source', int(frames), int(channels), (str(Path(path)),))

    @classmethod
    def silence(cls, frames, channels):
        return cls('silence', int(frames), int(channels), ())

    def slice(self, start, end):
        start, end = int(start), int(end)
        if not 0 <= start < end <= self.frames:
            raise ValueError('Seçim ses sınırları içinde ve sıfırdan uzun olmalı.')
        if start == 0 and end == self.frames:
            return self
        if self.kind == 'slice':
            parent, offset = self.args
            return parent.slice(offset+start, offset+end)
        return Audio('slice', end-start, self.channels, (self, start))

    def effect(self, kind, *args):
        return Audio(kind, self.frames, self.channels, (self, *args))

    def read(self, first, count, reader):
        if not 0 <= first <= first+count <= self.frames:
            raise ValueError('Ses tamponu aralığı geçersiz.')
        reader.check()
        if self.kind == 'source':
            pcm = reader.map(self.args[0], self.channels)
            if len(pcm) < self.frames:
                raise ValueError('Sesin yönetilen kopyası eksik veya bozuk.')
            return np.array(pcm[first:first+count], dtype=np.float32, copy=True)
        if self.kind == 'silence':
            return np.zeros((count, self.channels), np.float32)
        if self.kind == 'concat':
            output = np.empty((count, self.channels), np.float32)
            offset = written = 0
            for part in self.args:
                begin, end = max(first,offset), min(first+count,offset+part.frames)
                if end > begin:
                    values = part.read(begin-offset,end-begin,reader)
                    if part.channels < self.channels:
                        values = np.repeat(values, self.channels, axis=1)
                    output[written:written+len(values)] = values
                    written += len(values)
                offset += part.frames
                if written == count:
                    break
            return output
        child = self.args[0]
        if self.kind == 'slice':
            return child.read(self.args[1]+first,count,reader)
        if self.kind == 'reverse':
            return child.read(self.frames-first-count,count,reader)[::-1].copy()
        values = child.read(first,count,reader)
        if self.kind == 'gain':
            values *= np.float32(10**(self.args[1]/20))
        elif self.kind == 'dc':
            values -= np.asarray(self.args[1],np.float32)[None,:]
        elif self.kind == 'fade':
            direction,length = self.args[1:]
            at = np.arange(first,first+count,dtype=np.float64)
            ramp = at/max(1,length-1) if direction == 'in' else (self.frames-1-at)/max(1,length-1)
            values *= np.clip(ramp,0,1).astype(np.float32)[:,None]
        else:
            raise ValueError('Bilinmeyen ses işlemi: '+self.kind)
        return values


def concat(*parts):
    parts = tuple(part for part in parts if part is not None and part.frames)
    if not parts:
        raise ValueError('Klibin tamamı silinemez. Süreyi korumak için Sustur kullan.')
    if len(parts) == 1:
        return parts[0]
    return Audio('concat',sum(p.frames for p in parts),max(p.channels for p in parts),parts)


def region(audio, start, end):
    return audio.slice(start,end) if end > start else None


def splice(audio, start, end, replacement=None):
    if not 0 <= start <= end <= audio.frames:
        raise ValueError('Seçim sınırları geçersiz.')
    return concat(region(audio,0,start),replacement,region(audio,end,audio.frames))


class Reader:
    def __init__(self,cancel=None):
        self.cancel,self.maps = cancel,{}
    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise InterruptedError('Düzenleme iptal edildi.')
    def map(self,path,channels):
        key=(path,channels)
        if key not in self.maps:
            self.maps[key]=pcm_view(path,channels)
        return self.maps[key]
    def __enter__(self):return self
    def __exit__(self,*args):
        for pcm in self.maps.values():pcm._mmap.close()
        self.maps.clear()


def render(audio,target,cancel=None):
    """Canonical float32 PCM and 10 ms stereo-aware waveform bins."""
    target=Path(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    temp=target.with_suffix('.part')
    peaks,peak=[],0.
    try:
        with Reader(cancel) as reader,temp.open('wb') as output:
            for first in range(0,audio.frames,RATE):
                values=audio.read(first,min(RATE,audio.frames-first),reader)
                if not np.isfinite(values).all():
                    raise ValueError('İşlem sonucu ses seviyesi geçersiz; daha düşük kazanç kullan.')
                values.astype('<f4',copy=False).tofile(output)
                peak=max(peak,float(np.max(np.abs(values),initial=0)))
                full=len(values)//480
                if full:
                    bins=values[:full*480].reshape(full,-1)
                    peaks.append(np.stack((bins.min(axis=1),bins.max(axis=1)),axis=1))
                if full*480<len(values):
                    tail=values[full*480:]
                    peaks.append(np.array([[tail.min(),tail.max()]],np.float32))
            reader.check()
        np.save(target.with_suffix('.peaks.npy'),np.concatenate(peaks).astype(np.float32))
        os.replace(temp,target)
        return dict(frames=audio.frames,channels=audio.channels,peak=peak,rate=RATE,quality_version=2)
    except Exception:
        temp.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        target.with_suffix('.peaks.npy').unlink(missing_ok=True)
        raise


@dataclass(frozen=True)
class State:
    audio: Audio
    start: int
    end: int
    gain_db: float = 0.
    fade_in: float = 0.
    fade_out: float = 0.

    def selected(self):
        return self.audio.slice(self.start,self.end)

    def playable_selection(self):
        audio=self.selected()
        if self.gain_db:audio=audio.effect('gain',self.gain_db)
        for direction,seconds in (('in',self.fade_in),('out',self.fade_out)):
            if seconds:audio=audio.effect('fade',direction,round(seconds*RATE))
        return audio


class History:
    def __init__(self,state):
        self.states,self.index=[state],0
    @property
    def current(self):return self.states[self.index]
    def selection(self,start,end):
        self.states[self.index]=replace(self.current,start=int(start),end=int(end))
    def push(self,state):
        self.states[self.index+1:]=[state]
        self.index+=1
        # Audio remains referenced by immutable views; history does not retain
        # rendered PCM copies or one large NumPy buffer per operation.
        if len(self.states)>100:
            self.states.pop(0);self.index-=1


def transform(state,operation,cancel=None,**options):
    audio,start,end=state.audio,state.start,state.end
    selected=state.selected() if end>start else None
    if operation in ('delete','cut'):
        result=splice(audio,start,end)
        return replace(state,audio=result,start=min(start,result.frames),end=min(start,result.frames))
    if operation=='keep':
        if selected is None:raise ValueError('Önce bir alan seç.')
        return replace(state,audio=selected,start=0,end=selected.frames)
    if operation=='duplicate':
        if selected is None:raise ValueError('Önce bir alan seç.')
        return replace(state,audio=splice(audio,end,end,selected),start=end,end=end+selected.frames)
    if operation in ('paste','paste_before','paste_after','insert'):
        clip=options['audio']
        at=end if operation=='paste_after' else start
        stop=end if operation=='paste' else at
        return replace(state,audio=splice(audio,at,stop,clip),start=at,end=at+clip.frames)
    if operation=='silence':
        clip=Audio.silence(max(1,round(options['seconds']*RATE)),audio.channels)
        return replace(state,audio=splice(audio,start,start,clip),end=start+clip.frames)
    if operation=='trim_silence':
        first,last=None,None
        threshold=10**(float(options.get('threshold',-50))/20)
        with Reader(cancel) as reader:
            for pos in range(0,audio.frames,RATE):
                values=audio.read(pos,min(RATE,audio.frames-pos),reader)
                indices=np.flatnonzero(np.max(np.abs(values),axis=1)>threshold)
                if len(indices):
                    if first is None:first=pos+int(indices[0])
                    last=pos+int(indices[-1])+1
        if first is None:raise ValueError('Ses eşiğin altında; klibin tamamı silinmedi.')
        result=audio.slice(first,last)
        return replace(state,audio=result,start=0,end=result.frames)
    if selected is None:raise ValueError('Önce bir alan seç.')
    if operation=='mute':
        replacement=Audio.silence(selected.frames,selected.channels)
    elif operation=='reverse':
        replacement=selected.effect('reverse')
    elif operation=='gain':
        gain=float(options['db'])
        if not math.isfinite(gain) or not -60<=gain<=36:raise ValueError('Kazanç -60 ile +36 dB arasında olmalı.')
        replacement=selected.effect('gain',gain)
    elif operation=='fade':
        replacement=selected.effect('fade',options['direction'],max(2,round(options['seconds']*RATE)))
    elif operation in ('normalize','dc'):
        peak,total=0.,np.zeros(selected.channels,np.float64)
        with Reader(cancel) as reader:
            for pos in range(0,selected.frames,RATE):
                values=selected.read(pos,min(RATE,selected.frames-pos),reader)
                peak=max(peak,float(np.max(np.abs(values),initial=0)))
                if operation=='dc':total+=values.sum(axis=0,dtype=np.float64)
        if operation=='normalize':
            if peak<=1e-9:raise ValueError('Sessiz bir alanın ses seviyesi dengelenemez.')
            db=float(options.get('target',-1))-state.gain_db-20*math.log10(peak)
            replacement=selected.effect('gain',db)
        else:replacement=selected.effect('dc',tuple(total/selected.frames))
    else:
        raise ValueError('Bilinmeyen düzenleme işlemi: '+operation)
    return replace(state,audio=splice(audio,start,end,replacement))


class EditWorker:
    def __init__(self,root):
        self.directory=Path(tempfile.mkdtemp(prefix='editor-',dir=root))
        self.cancel=threading.Event()
        self.jobs,self.messages=queue.Queue(),queue.Queue()
        self.thread=threading.Thread(target=self._run,name='Soundpad-editor',daemon=True)
        self.thread.start()
    def submit(self,kind,func):
        if not self.cancel.is_set():self.jobs.put((kind,func))
    def retire(self,path):
        if path and Path(path).parent==self.directory:
            def remove():
                for _ in range(20):
                    try:
                        Path(path).unlink(missing_ok=True)
                        Path(path).with_suffix('.peaks.npy').unlink(missing_ok=True)
                        return
                    except OSError:
                        if self.cancel.wait(.05):return
            self.submit('retire',remove)
    def _run(self):
        try:
            while not self.cancel.is_set():
                job=self.jobs.get()
                if job is None:break
                kind,func=job
                try:
                    result=func()
                    if not self.cancel.is_set():self.messages.put((kind,result,''))
                except Exception as exc:
                    if not self.cancel.is_set():self.messages.put((kind,None,str(exc)))
        finally:
            for path in tuple(getattr(self,'pending_paths',())):
                try:
                    Path(path).unlink(missing_ok=True)
                    Path(path).with_suffix('.peaks.npy').unlink(missing_ok=True)
                except OSError:
                    pass
            shutil.rmtree(self.directory,ignore_errors=True)
    def close(self):
        self.cancel.set()
        self.jobs.put(None)
        self.thread.join(timeout=.5)
