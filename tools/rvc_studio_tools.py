"""Bounded test capture, meters, local playback and same-input A/B rendering."""
from __future__ import annotations
import copy
import gc
import math
import queue
import threading
import time
from types import SimpleNamespace
import numpy as np
import sounddevice as sd
from tools.rvc_monitor import AudioRing

SETTINGS=('pth_path','index_path','pitch','formant','index_rate','rms_mix_rate','threhold',
          'block_time','crossfade_length','extra_time','I_noise_reduce','O_noise_reduce',
          'pm','rmvpe','fcpe','sr_model','sr_device')


def level(samples):
    if samples.size==0: return -60.,-60.,False
    x=samples[:,0] if samples.ndim==2 else samples
    peak=float(np.max(np.abs(x)))
    rms=float(np.sqrt(np.einsum('i,i->',x,x,dtype=np.float64,optimize=False)/len(x)))
    return max(-60.,20*math.log10(max(rms,1e-9))),max(-60.,20*math.log10(max(peak,1e-9))),peak>=.999


class NullWindow:
    def __getitem__(self,key): return self
    def update(self,*args,**kwargs): pass


class StudioTools:
    def __init__(self,owner):
        self.owner=owner; self.messages=queue.Queue()
        self.muted=False; self.input_level=(-60.,-60.,False); self.output_level=(-60.,-60.,False)
        self.level_time=0.; self.recording=None; self.capture_lock=threading.Lock(); self.clip=None
        self.snapshots={}; self.results={}; self.result_signature=None
        self.busy=False; self.cancel=threading.Event(); self.worker=None
        self.player=None; self.play_name=''; self.play_done=threading.Event()
        self.record_state='Önce ses dönüşümünü başlat. Test kaydı bellekte tutulur.'
        self.ab_state='A ve B için ayarları sakla, bir mikrofon kaydı al, sonra karşılaştır.'

    def post(self,kind,**data): self.messages.put((kind,data))

    def audio(self,indata,outdata,samplerate):
        now=time.monotonic()
        if now-self.level_time >= .08:
            self.input_level=level(indata); self.output_level=level(outdata); self.level_time=now
        if self.recording is None: return
        if not self.capture_lock.acquire(blocking=False): return
        try:
            rec=self.recording
            if rec is None: return
            n=min(len(outdata),len(rec['raw'])-rec['pos'])
            if n>0:
                p=rec['pos']
                rec['raw'][p:p+n]=indata[:n,0] if indata.ndim==2 else indata[:n]
                rec['output'][p:p+n]=outdata[:n,0] if outdata.ndim==2 else outdata[:n]
                rec['pos']+=n
            if rec['pos']>=len(rec['raw']):
                self.recording=None
                self.post('capture_done',recording=rec)
        finally: self.capture_lock.release()

    def record(self,seconds,settings):
        if self.recording is not None:
            self.end_record(); return
        if self.busy:
            self.record_state='A/B işlemi bitince kayıt alabilirsin.'; return
        main=getattr(self.owner,'stream',None)
        if main is None or not main.active or self.owner.function!='vc':
            self.record_state='Ses Dönüştürme modunu seç ve SESİ BAŞLAT düğmesine bas.'; return
        self.stop_play()
        rate=int(round(main.samplerate)); frames=int(rate*max(1,min(30,float(seconds))))
        rec={'raw':np.zeros(frames,np.float32),'output':np.zeros(frames,np.float32),
             'rate':rate,'pos':0,'settings':copy.deepcopy(settings),'time':time.monotonic()}
        with self.capture_lock: self.recording=rec
        self.record_state='Kayıt alınıyor… Konuş; süre dolunca otomatik bitecek.'

    def end_record(self):
        with self.capture_lock: rec,self.recording=self.recording,None
        if rec is not None: self.complete_capture(rec)

    def complete_capture(self,rec):
        if rec['pos']<rec['rate']*.15:
            self.record_state='Kayıt çok kısa. Tekrar dene.'; return
        self.clip={'raw':rec['raw'][:rec['pos']].copy(),'output':rec['output'][:rec['pos']].copy(),
                   'rate':rec['rate'],'settings':rec['settings']}
        self.results={}; self.result_signature=None
        self.record_state=f"Test hazır · {rec['pos']/rec['rate']:.1f} saniye · Sonucu dinleyebilirsin."
        self.ab_state='Yeni kayıt hazır. A/B sonuçlarını bu kayıtla oluştur.'

    def on_stream_stop(self):
        if self.recording is not None: self.end_record()
        self.input_level=(-60.,-60.,False); self.output_level=(-60.,-60.,False)

    def snapshot(self,name,settings):
        if self.busy: return
        if not settings.get('pth_path'):
            self.ab_state='Önce bir model seç.'; return
        self.snapshots[name]={k:copy.deepcopy(settings[k]) for k in SETTINGS if k in settings}
        if not self.snapshots[name].get('index_path', '').strip():
            self.snapshots[name]['index_rate'] = 0.
        self.results={}; self.result_signature=None
        self.ab_state=name+' ayarları saklandı. Diğer ayarları seçip diğer yuvayı da sakla.'

    def describe(self,name):
        item=self.snapshots.get(name)
        if not item: return name+' · henüz saklanmadı'
        from pathlib import Path
        f0=next((k.upper() for k in ('pm','rmvpe','fcpe') if item.get(k)), 'RMVPE')
        return f"{name} · {Path(item['pth_path']).stem} · Pitch {item.get('pitch',0):g} · Formant {item.get('formant',0):g} · Index {item.get('index_rate',0):g} · {f0}"

    def compare(self):
        if self.busy: return
        if self.recording is not None: self.end_record()
        if self.clip is None or not all(k in self.snapshots for k in ('A','B')):
            self.ab_state='Bir test kaydı ve hem A hem B ayarları gerekli.'; return
        self.stop_play()
        main=getattr(self.owner,'stream',None)
        resume=main is not None and main.active
        self.busy=True; self.cancel.clear(); self.owner.stop_stream()
        self.results={}
        raw=self.clip['raw'].copy(); rate=self.clip['rate']; snapshots=copy.deepcopy(self.snapshots)
        self.ab_state='A/B dönüştürülüyor… Canlı dönüşüm işlem boyunca duraklatıldı.'
        self.worker=threading.Thread(target=self._render_job,args=(raw,rate,snapshots,resume),
                                     name='RVC-AB-render',daemon=True)
        self.worker.start()

    def _render_job(self,raw,rate,snapshots,resume):
        results={}; error=None
        try:
            for name in ('A','B'):
                if self.cancel.is_set(): break
                self.post('ab_progress',text=name+' ayarları ile aynı kayıt dönüştürülüyor…')
                results[name]=self._render(raw,rate,snapshots[name])
        except Exception as exc:
            import traceback
            traceback.print_exc(); error=str(exc)
        finally:
            gc.collect()
            self.post('ab_done',results=results,error=error,cancelled=self.cancel.is_set(),resume=resume)

    def _render(self,raw,input_rate,settings):
        # Independent engine state; original realtime DSP, but no hardware stream is opened.
        clone=object.__new__(type(self.owner))
        clone.config=self.owner.config
        clone.gui_config=type(self.owner.gui_config)()
        mapping={'crossfade_length':'crossfade_time'}
        for key in SETTINGS:
            if key in settings and key not in ('pm','rmvpe','fcpe','sr_model','sr_device'):
                setattr(clone.gui_config,mapping.get(key,key),settings[key])
        clone.gui_config.f0method=next((k for k in ('pm','rmvpe','fcpe') if settings.get(k)), 'rmvpe')
        clone.gui_config.sr_type='sr_device' if settings.get('sr_device') else 'sr_model'
        clone.get_device_samplerate=lambda:input_rate
        clone.get_device_channels=lambda:1
        clone.start_stream=lambda:None
        clone.function='vc'; clone.stream=None; clone.window=NullWindow()
        try:
            type(self.owner)._studio_start_vc(clone)
            rate=int(clone.gui_config.samplerate)
            if rate!=input_rate:
                from scipy.signal import resample_poly
                factor=math.gcd(input_rate,rate)
                audio=np.asarray(resample_poly(raw,rate//factor,input_rate//factor),dtype=np.float32)
            else: audio=raw
            step=clone.block_frame
            # Flush model lookahead/crossfade after the final spoken block.
            tail=max(2*step,clone.crossfade_frame+clone.sola_search_frame)
            count=int(math.ceil((len(audio)+tail)/step))
            outputs=[]
            for index in range(count):
                if self.cancel.is_set(): return None
                chunk=np.zeros((step,1),np.float32)
                start=index*step; n=min(step,max(0,len(audio)-start))
                if n: chunk[:n,0]=audio[start:start+n]
                out=np.zeros_like(chunk)
                type(self.owner)._studio_audio(clone,chunk,out,step,None,None)
                outputs.append(out[:,0].copy())
            return {'audio':np.concatenate(outputs),'rate':rate,'settings':settings}
        finally:
            # Do not call clone.stop_stream: original global flag belongs to the real GUI.
            clone.__dict__.clear()
            gc.collect()

    def play(self,name):
        if self.busy:
            self.record_state='A/B işlemi tamamlanmadan dinleme başlatılamaz.'; return
        if name=='test':
            data=self.clip
            if not data:
                self.record_state='Önce kısa bir test kaydı al.'; return
            audio=data['output']; rate=data['rate']
        else:
            data=self.results.get(name)
            if not data:
                self.ab_state='Önce A/B sonuçlarını oluştur.'; return
            audio=data['audio']; rate=data['rate']
        self.stop_play(restart_monitor=False)
        monitor=self.owner.output_monitor
        monitor.stop()
        try:
            index=monitor.devices.get(monitor.selected)
            if index is None: raise RuntimeError('Kendi Sesimi Dinle sekmesinden kulaklık seç.')
            info=sd.query_devices(index)
            main=getattr(self.owner,'stream',None)
            if main is not None and main.active:
                primary=sd.query_devices(main.device[1])
                if index==main.device[1] or primary['name']==info['name']:
                    raise RuntimeError('Canlı çıkış bu kulaklığı kullanıyor. Önce dönüşümü durdur veya ana çıkışı sanal kabloya ayarla.')
            channels=min(2,int(info['max_output_channels'])); target=rate
            try: sd.check_output_settings(device=index,channels=channels,dtype='float32',samplerate=target)
            except sd.PortAudioError: target=int(round(info['default_samplerate']))
            ring=AudioRing(rate,target,len(audio)+4)
            ring.push(np.r_[audio,np.zeros(3,np.float32)])
            remaining=int(math.ceil(len(audio)*target/rate)); self.play_done.clear()
            def callback(out,frames,times,status):
                nonlocal remaining
                n=min(frames,remaining); out.fill(0)
                if n: ring.render(out[:n],monitor.volume)
                remaining-=n
                if remaining<=0: raise sd.CallbackStop
            player=sd.OutputStream(device=index,samplerate=target,channels=channels,dtype='float32',
                                   blocksize=0,callback=callback,finished_callback=self.play_done.set)
            self.player=player; self.play_name=name
            player.start()
            self.record_state=('Son test' if name=='test' else name+' sonucu')+' kulaklıkta oynatılıyor.'
        except Exception as exc:
            self.stop_play(); self.record_state='Dinleme açılamadı: '+str(exc)

    def stop_play(self,restart_monitor=True):
        player,self.player=self.player,None; self.play_name=''; self.play_done.clear()
        if player is not None:
            try: player.abort()
            except Exception: pass
            try: player.close()
            except Exception: pass
        if restart_monitor and hasattr(self.owner,'output_monitor'):
            self.owner.output_monitor.start()

    def export(self,path,name='test'):
        if name=='test':
            if not self.clip: raise RuntimeError('Önce bir test kaydı al.')
            audio=self.clip['output']; rate=self.clip['rate']
        else:
            item=self.results.get(name)
            if not item: raise RuntimeError('Önce A/B karşılaştırmasını oluştur.')
            audio=item['audio']; rate=item['rate']
        import soundfile as sf
        sf.write(path,audio,rate,subtype='PCM_16')

    def shutdown(self):
        self.cancel.set(); self.end_record(); self.stop_play(restart_monitor=False)
