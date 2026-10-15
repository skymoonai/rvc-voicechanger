"""AP-BWE postprocessor for an already overlapping RVC inference window.

The existing RVC right context is reused: no new audio queue or chunk delay.
The 24→48 kHz generator restores the upper band; original low frequencies stay.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from pathlib import Path
import torch
import torch.nn.functional as F
from torchaudio.transforms import Resample
from tools.apbwe_model import APNet_BWE_Model


class APBWERuntime:
    def __init__(self, checkpoint, config, device='cpu'):
        self.device = torch.device(device)
        params = json.loads(Path(config).read_text(encoding='utf-8'))
        self.h = SimpleNamespace(**params)
        if (self.h.n_fft, self.h.hop_size, self.h.win_size,
                self.h.lr_sampling_rate, self.h.hr_sampling_rate) != (1024,80,320,24000,48000):
            raise RuntimeError('AP-BWE model yapılandırması uyumsuz.')
        # Meta construction avoids random initialization of 29M unused weights.
        with torch.device('meta'):
            self.model = APNet_BWE_Model(self.h)
        state = torch.load(checkpoint, map_location='cpu', weights_only=True)
        self.model.load_state_dict(state['generator'], strict=True, assign=True)
        self.model = self.model.eval().to(self.device)
        self.window = torch.hann_window(320, device=self.device)
        self.resamplers = {}
        for rate in (24000,32000,40000,44100,48000,88200,96000):
            for a,b in ((rate,24000),(24000,48000),(rate,48000),(48000,rate),(48000,24000)):
                if a != b and (a,b) not in self.resamplers:
                    self.resamplers[a,b] = Resample(a,b).to(self.device)
        self.history = None
        self.history_rate = None
        self.last_strength = 0.0
        self.calls = 0
        self.last_ms = 0.0
        with torch.inference_mode():
            self._enhance(torch.zeros(7200,device=self.device))
        if self.device.type == 'cuda':
            torch.cuda.synchronize(self.device)

    def reset(self):
        self.history = None
        self.history_rate = None
        self.last_strength = 0.0

    @staticmethod
    def fit(audio, length):
        if len(audio) < length:
            audio = F.pad(audio, (0,length-len(audio)))
        return audio[:length]

    def resample(self, audio, source, target):
        if source == target:
            return audio
        if (source,target) not in self.resamplers:
            raise RuntimeError('AP-BWE için desteklenmeyen çıkış hızı: '+str(source))
        return self.resamplers[source,target](audio)

    def _enhance(self, audio48):
        spec = torch.stft(audio48[None],1024,hop_length=80,win_length=320,
                          window=self.window,center=True,pad_mode='reflect',return_complex=True)
        mag, pha, _ = self.model(torch.log(spec.abs()+1e-4), torch.angle(spec))
        complex_spec = torch.polar(torch.exp(mag),pha)
        return torch.istft(complex_spec,1024,hop_length=80,win_length=320,
                           window=self.window,center=True,length=len(audio48))[0]

    @torch.inference_mode()
    def process(self, audio, rate, block_frames, strength, deadline=None):
        if audio.ndim != 1 or rate < 24000:
            raise RuntimeError('AP-BWE, en az 24 kHz tek kanallı RVC çıkışı gerektirir.')
        if not len(audio) or strength <= 0:
            self.reset()
            return audio
        start = time.perf_counter()
        if self.history_rate != rate:
            self.reset()
            self.history_rate = rate
        source_device, source_dtype = audio.device, audio.dtype
        dry = audio.to(device=self.device,dtype=torch.float32)
        if not bool(torch.isfinite(dry).all()):
            raise RuntimeError('RVC çıkışında geçersiz ses örneği var.')
        if float(dry.abs().max()) < 1e-5:
            self.reset()
            return audio
        # 120 ms left context is longer than the generator's receptive field.
        left = int(round(rate*.12))
        previous = self.history
        if previous is None:
            previous = torch.zeros(left,device=self.device)
        joined = torch.cat((previous,dry))
        # Save only newly advanced samples, never the overlapping right tail.
        self.history = joined[:left+min(block_frames,len(dry))][-left:].clone()
        low24 = self.resample(joined,rate,24000)
        low48 = self.resample(low24,24000,48000)
        predicted = self._enhance(low48)
        dry48 = self.fit(self.resample(joined,rate,48000),len(predicted))
        # Complementary upper-band residual keeps the original voice/body.
        residual = predicted-dry48
        residual_low = self.fit(self.resample(self.resample(residual,48000,24000),24000,48000),len(residual))
        high = residual-residual_low
        delta = self.fit(self.resample(high,48000,rate),len(joined))[left:left+len(dry)]
        # Taper the artificial right boundary instead of buffering future blocks.
        # RVC's existing SOLA crossfade then handles boundaries and effect switches.
        guard = min(len(delta),int(round(rate*.065)))
        if guard:
            delta[-guard:] *= torch.linspace(1,0,guard,device=self.device)
        target = min(1.0,max(0.0,float(strength)))
        ramp = min(len(dry),int(round(rate*.03)))
        gain = torch.full_like(dry,target)
        if ramp:
            gain[:ramp] = torch.linspace(self.last_strength,target,ramp,device=self.device)
        self.last_strength = target
        out = dry+delta*gain
        if not bool(torch.isfinite(out).all()):
            raise RuntimeError('AP-BWE geçersiz ses üretti; normal RVC korundu.')
        # Limit the added component, retaining the original waveform when possible.
        peak = float(out.abs().max())
        if peak > .99:
            out *= .99/peak
        out = out.to(device=source_device,dtype=source_dtype)
        if self.device.type == 'cuda':
            torch.cuda.synchronize(self.device)
        self.last_ms = (time.perf_counter()-start)*1000
        self.calls += 1
        return out
