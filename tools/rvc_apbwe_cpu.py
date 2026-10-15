"""One request at a time CPU inference; isolated Torch threads, bounded timeout.

There is no audio job queue. A late worker is bypassed for the current block
and suspended; stale results are never played. Windows uses no console window.
"""
from __future__ import annotations
import json
from pathlib import Path
import queue
import struct
import subprocess
import sys
import threading
import time
import numpy as np


def read_exact(stream, size):
    pieces = []
    while size:
        data = stream.read(size)
        if not data:
            raise EOFError('AP-BWE çalışma süreci kapandı.')
        pieces.append(data)
        size -= len(data)
    return b''.join(pieces)


def send_packet(stream, data, audio=b''):
    meta = json.dumps(data).encode('utf-8')
    pending = memoryview(struct.pack('<I',len(meta))+meta+audio)
    while pending:
        count = stream.write(pending)
        if not count:
            raise EOFError('AP-BWE çalışma sürecine yazılamadı.')
        pending = pending[count:]
    stream.flush()


def receive_packet(stream):
    size = struct.unpack('<I',read_exact(stream,4))[0]
    if size > 16384:
        raise RuntimeError('AP-BWE protokol başlığı geçersiz.')
    data = json.loads(read_exact(stream,size))
    frames = int(data.get('frames',0))
    if frames < 0 or frames > 576000:
        raise RuntimeError('AP-BWE protokol ses boyutu geçersiz.')
    return data, read_exact(stream,frames*4)


class CPUAPBWERuntime:
    device = 'cpu'
    def __init__(self, checkpoint, config, cancel=None):
        self.broken = False
        self._closed = False
        self._reset = True
        self.replies = queue.Queue(maxsize=2)
        executable = Path(sys.executable)
        if executable.name.lower() == 'pythonw.exe':
            executable = executable.with_name('python.exe')
        root = Path(__file__).resolve().parents[1]
        logs = root/'logs'
        logs.mkdir(parents=True,exist_ok=True)
        self.log = (logs/'apbwe_cpu_worker.log').open('ab')
        self.process_handle = subprocess.Popen(
            [str(executable),'-u',str(Path(__file__).with_name('rvc_apbwe_cpu_worker.py')),
             '--checkpoint',str(checkpoint),'--config',str(config)],
            stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),bufsize=0)
        def reader():
            try:
                while not self._closed:
                    packet = receive_packet(self.process_handle.stdout)
                    self.replies.put(packet,timeout=1)
            except Exception as exc:
                if not self._closed:
                    try:
                        self.replies.put(({'ok':False,'error':str(exc)},b''),timeout=1)
                    except queue.Full:
                        pass
        self.reader = threading.Thread(target=reader,name='APBWE-CPU-reader',daemon=True)
        self.reader.start()
        try:
            limit = time.monotonic()+90
            while True:
                if cancel is not None and cancel.is_set():
                    raise InterruptedError('AP-BWE CPU hazırlığı iptal edildi.')
                if time.monotonic() > limit:
                    raise TimeoutError('AP-BWE CPU hazırlığı süre sınırını aştı.')
                try:
                    reply,_ = self.replies.get(timeout=.1)
                    break
                except queue.Empty:
                    pass
            if not reply.get('ok') or not reply.get('ready'):
                raise RuntimeError(reply.get('error','AP-BWE CPU hazırlanamadı.'))
        except Exception:
            self.close()
            raise

    def reset(self):
        self._reset = True

    def process(self, audio, rate, block_frames, strength, deadline=None):
        if self.broken or self._closed:
            raise RuntimeError('AP-BWE CPU süreci yeniden hazırlanmalı.')
        import torch
        source = audio.detach().to(device='cpu',dtype=torch.float32).contiguous().numpy()
        if source.ndim != 1 or len(source) > 576000:
            raise RuntimeError('AP-BWE ses penceresi geçersiz.')
        timeout = max(.005,min(.8,block_frames/rate*.7))
        if deadline is not None:
            timeout = min(timeout,max(.001,deadline-time.perf_counter()))
        send_packet(self.process_handle.stdin,
                    {'frames':len(source),'rate':rate,'block_frames':block_frames,
                     'strength':strength,'reset':self._reset}, source.astype('<f4',copy=False).tobytes())
        self._reset = False
        try:
            reply,raw = self.replies.get(timeout=timeout)
        except queue.Empty:
            self.broken = True
            raise TimeoutError('AP-BWE CPU süre sınırına yetişemedi; normal RVC korundu.')
        if not reply.get('ok'):
            self.broken = True
            raise RuntimeError(reply.get('error','AP-BWE CPU hatası.'))
        if int(reply.get('frames',0)) != len(source):
            self.broken = True
            raise RuntimeError('AP-BWE çıktı uzunluğu değişti; normal RVC korundu.')
        return torch.from_numpy(np.frombuffer(raw,dtype='<f4').copy()).to(device=audio.device,dtype=audio.dtype)

    def close(self):
        if self._closed:
            return
        self._closed = True
        process = self.process_handle
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        for stream in (process.stdin,process.stdout):
            if stream:
                stream.close()
        self.log.close()
