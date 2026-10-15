"""Local playback of the completed RVC output. No model inference or file recording."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

MONITOR_GAIN = 3.0


class AudioRing:
    """Bounded mono ring with continuous linear resampling for the monitor only."""
    def __init__(self, source_rate, target_rate, capacity):
        self.samples = np.zeros(int(capacity), dtype=np.float32)
        self.capacity = len(self.samples)
        self.read_pos = 0
        self.count = 0
        self.fraction = 0.0
        self.ratio = float(source_rate) / float(target_rate)
        self.lock = threading.Lock()

    def push(self, block):
        # Neither audio callback waits for the other callback.
        if not self.lock.acquire(blocking=False):
            return
        try:
            mono = block[:, 0] if block.ndim == 2 else block
            if len(mono) >= self.capacity:
                mono = mono[-self.capacity:]
                self.count = 0
                self.fraction = 0.0
            overflow = max(0, self.count + len(mono) - self.capacity)
            if overflow:
                self.read_pos = (self.read_pos + overflow) % self.capacity
                self.count -= overflow
                self.fraction = 0.0
            pos = (self.read_pos + self.count) % self.capacity
            first = min(len(mono), self.capacity - pos)
            self.samples[pos:pos + first] = mono[:first]
            self.samples[:len(mono) - first] = mono[first:]
            self.count += len(mono)
        finally:
            self.lock.release()

    def render(self, out, gain):
        out.fill(0)
        if not self.lock.acquire(blocking=False):
            return
        try:
            if self.ratio == 1.0 and self.fraction == 0:
                n = min(len(out), self.count)
                first = min(n, self.capacity-self.read_pos)
                out[:first] = self.samples[self.read_pos:self.read_pos+first, None]
                if n > first:
                    out[first:n] = self.samples[:n-first, None]
                if gain != 1:
                    out[:n] *= gain
                self.read_pos = (self.read_pos+n) % self.capacity
                self.count -= n
                return
            if self.count < 2:
                return
            # Keep one source sample for interpolation across block boundaries.
            available = max(0, int(np.ceil((self.count - 1 - self.fraction) / self.ratio)))
            n = min(len(out), available)
            if n <= 0:
                return
            positions = self.fraction + np.arange(n) * self.ratio
            lower = positions.astype(np.int64)
            delta = positions - lower
            a = self.samples[(self.read_pos + lower) % self.capacity]
            b = self.samples[(self.read_pos + lower + 1) % self.capacity]
            values = (a + (b - a) * delta) * gain
            out[:n] = values[:, None]
            advance = self.fraction + n * self.ratio
            consumed = min(int(advance), self.count - 1)
            self.fraction = advance - consumed
            self.read_pos = (self.read_pos + consumed) % self.capacity
            self.count -= consumed
        finally:
            self.lock.release()


class OutputMonitor:
    def __init__(self, owner):
        self.owner = owner
        self.stream = None
        self.ring = None
        self.enabled = False
        self.volume = 0.5
        self.selected = ''
        self.devices = {}
        self.error = None
        self.path = Path(__file__).resolve().parent.parent / 'configs' / 'monitor_settings.json'
        try:
            data = json.loads(self.path.read_text(encoding='utf-8'))
            self.selected = str(data.get('device', ''))
            self.enabled = data.get('enabled', False) is True
            self.volume = min(1, max(0, float(data.get('volume', 0.5))))
        except (OSError, ValueError, TypeError):
            pass

    def status(self, text):
        window = getattr(self.owner, 'window', None)
        if window and 'monitor_status' in window.widgets:
            window['monitor_status'].update(text)

    def refresh(self):
        hostapis = sd.query_hostapis()
        devices = sd.query_devices()
        self.devices = {}
        default_output = sd.default.device[1]
        default_label = ''
        for index, device in enumerate(devices):
            if device['max_output_channels'] <= 0:
                continue
            host = hostapis[device['hostapi']]['name']
            label = f"{device['name']} · {host}"
            # Device indices are refreshed each time, never persisted.
            if label in self.devices:
                label += f' [{index}]'
            self.devices[label] = index
            if index == default_output:
                default_label = label
        if self.selected not in self.devices:
            self.selected = default_label or next(iter(self.devices), '')
        return list(self.devices)

    def save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.json.tmp')
        temp.write_text(json.dumps({'device': self.selected, 'volume': self.volume, 'enabled': self.enabled},
                                   ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(temp, self.path)

    def configure(self, enabled, selected, volume, *, restart=True):
        reopen = bool(enabled) != self.enabled or selected != self.selected
        self.enabled = bool(enabled)
        self.selected = str(selected)
        self.volume = min(1, max(0, float(volume) / 100))
        self.save()
        if reopen and restart:
            self.restart()

    def start(self):
        self.stop()
        self.error = None
        if not self.enabled:
            self.status('Dinleme kapalı.')
            return
        main = getattr(self.owner, 'stream', None)
        if main is None or not main.active:
            self.status('Hazır. Ses dönüşümünü başlattığında kulaklık dinlemesi açılır.')
            return
        try:
            index = self.devices.get(self.selected)
            if index is None:
                raise RuntimeError('Dinleme cihazını seç ve cihaz listesini yenile.')
            info = sd.query_devices(index)
            primary = sd.query_devices(main.device[1])
            if index == main.device[1] or info['name'] == primary['name']:
                self.status('Ana çıkış zaten bu cihazda. Ayrı dinleme için ana çıkışı sanal kabloya ayarla.')
                return
            source_rate = int(round(main.samplerate))
            channels = min(2, int(info['max_output_channels']))
            target_rate = source_rate
            try:
                sd.check_output_settings(device=index, channels=channels, dtype='float32',
                                         samplerate=target_rate)
            except sd.PortAudioError:
                target_rate = int(round(info['default_samplerate']))
                sd.check_output_settings(device=index, channels=channels, dtype='float32',
                                         samplerate=target_rate)
            capacity = max(2 * self.owner.block_frame + 2, int(source_rate * .15))
            ring = AudioRing(source_rate, target_rate, capacity)

            def callback(out, frames, times, status):
                try:
                    ring.render(out, self.volume * MONITOR_GAIN)
                    np.clip(out, -1, 1, out=out)
                except Exception as exc:
                    out.fill(0)
                    self.error = str(exc)
                    raise sd.CallbackAbort

            stream = sd.OutputStream(device=index, samplerate=target_rate, channels=channels,
                                     dtype='float32', blocksize=0, latency='low', callback=callback)
            try:
                stream.start()
            except Exception:
                stream.close()
                raise
            self.stream = stream
            self.ring = ring
            self.status('Dönüştürülmüş ses dinleniyor · ' + self.selected)
        except Exception as exc:
            self.stop()
            self.status('Dinleme açılamadı: ' + str(exc) + ' · Ana ses çıkışı çalışmaya devam eder.')
            print('Monitor:', exc, flush=True)

    def restart(self):
        self.start()

    def stop(self):
        self.ring = None
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.abort()
            except Exception:
                pass
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

    def feed(self, outdata):
        ring = self.ring
        if ring is not None:
            try:
                ring.push(outdata)
            except Exception as exc:
                # Monitor errors must never cancel RVC's main output callback.
                self.ring = None
                self.error = str(exc)


def install_engine_hooks(gui_class):
    """Attach after GUI definition, before instantiation. Original DSP remains intact."""
    if getattr(gui_class, '_rvc_monitor_installed', False):
        return
    original_init = gui_class.__init__
    original_start = gui_class.start_stream
    original_stop = gui_class.stop_stream
    original_audio = gui_class.audio_callback
    original_devices = gui_class.update_devices
    gui_class._studio_audio = original_audio
    gui_class._studio_start_vc = gui_class.start_vc

    def init(self, *args, **kwargs):
        self._studio_requested_mode = 'vc'
        self._studio_previous_mode = 'vc'
        self.output_monitor = OutputMonitor(self)
        from tools.rvc_studio_tools import StudioTools
        self.studio_tools = StudioTools(self)
        from tools.rvc_noise import NoiseFilter
        from tools.rvc_soundpad import Soundpad
        from tools.rvc_file_convert import FileConverter
        self.noise_filter = NoiseFilter()
        from tools.rvc_apbwe import RealtimeAPBWE
        self.apbwe = RealtimeAPBWE(self)
        self.soundpad = Soundpad(self)
        self.file_converter = FileConverter(self)
        return original_init(self, *args, **kwargs)

    def start(self, *args, **kwargs):
        self.soundpad.before_main_start()
        self.noise_filter.reset()
        if not self.studio_tools.busy and not self.file_converter.busy:
            self.apbwe.bind()
        else:
            self.apbwe.reset()
        result = original_start(self, *args, **kwargs)
        if self.studio_tools.player is None:
            self.output_monitor.start()
        return result

    def stop(self, *args, **kwargs):
        self.output_monitor.stop()
        result = original_stop(self, *args, **kwargs)
        self.apbwe.reset()
        self.studio_tools.on_stream_stop()
        self.output_monitor.status('Ses dönüşümü durdu. Dinleme bekliyor.' if self.output_monitor.enabled
                                   else 'Dinleme kapalı.')
        return result

    def clear_audio_cache(self):
        """Discard queued audio without loading the voice model again.

        PortAudio has no public queue-flush API for a duplex stream. Abort/close
        it first, then clear history in place (CUDA graphs retain these storage
        addresses), and reopen the same configured devices.
        """
        tools = self.studio_tools
        if tools.busy:
            raise RuntimeError('A/B dönüşümü bitince gecikmeyi sıfırlayabilirsin.')
        stream = getattr(self, 'stream', None)
        if stream is None or not stream.active:
            return 'Ses akışı kapalı. Önce sesi başlat.'
        tools.stop_play(restart_monitor=False)
        self.stop_stream()  # abort joins the callback before any buffer is touched
        try:
            for owner, names in ((self, ('input_wav', 'input_wav_denoise', 'input_wav_res',
                                         'output_buffer', 'sola_buffer', 'nr_buffer', 'rms_buffer')),
                                 (getattr(self, 'rvc', None), ('cache_pitch', 'cache_pitchf'))):
                for name in names:
                    buffer = getattr(owner, name, None)
                    if buffer is not None:
                        if hasattr(buffer, 'zero_'):
                            buffer.zero_()
                        elif isinstance(buffer, np.ndarray):
                            buffer.fill(0)
            self.start_stream()
            if self.stream is None or not self.stream.active:
                raise RuntimeError('Ses akışı yeniden başlatılamadı.')
        except Exception:
            # Upstream marks its global running flag before opening PortAudio.
            self.stop_stream()
            raise
        text = 'Ses tamponları temizlendi; akış yeniden başlatıldı.'
        print('Gecikme sıfırlama: ' + text, flush=True)
        return text

    def audio(self, indata, outdata, frames, times, status):
        mode = getattr(self, '_studio_requested_mode', getattr(self, 'function', 'vc'))
        # Apply the choice once at a callback boundary, never halfway through conversion.
        self.function = mode
        if mode != getattr(self, '_studio_previous_mode', mode):
            for name in ('sola_buffer', 'nr_buffer'):
                buffer = getattr(self, name, None)
                if buffer is not None and hasattr(buffer, 'zero_'):
                    buffer.zero_()
            if mode == 'vc':
                # Normal mic mode does no RVC/GPU work. Discard stale context
                # only when returning to conversion; model weights stay loaded.
                for name in ('input_wav', 'input_wav_denoise', 'input_wav_res', 'output_buffer'):
                    buffer = getattr(self, name, None)
                    if buffer is not None and hasattr(buffer, 'zero_'):
                        buffer.zero_()
                if hasattr(self, 'rms_buffer'):
                    self.rms_buffer.fill(0)
                for name in ('cache_pitch', 'cache_pitchf'):
                    buffer = getattr(getattr(self, 'rvc', None), name, None)
                    if buffer is not None and hasattr(buffer, 'zero_'):
                        buffer.zero_()
            self._studio_previous_mode = mode
            self.apbwe.reset()
        clean = self.noise_filter.process(indata, self.gui_config.samplerate)
        if mode == 'im':
            # PortAudio input/output already share a rate. No tensor copies,
            # feature extraction, SOLA or resampling are needed for passthrough.
            started = time.perf_counter()
            mono = np.mean(clean, axis=1, dtype=np.float32) if clean.ndim > 1 else clean
            outdata[:] = mono[:, None] if outdata.ndim > 1 else mono
            self._studio_infer_ms = int((time.perf_counter() - started) * 1000)
            result = None
        else:
            result = original_audio(self, clean, outdata, frames, times, status)
        if self.studio_tools.muted:
            outdata.fill(0)
        # Keep Soundpad routing explicit: mic-only clips do not leak into monitoring.
        self.output_monitor.feed(outdata)
        self.soundpad.render_main(outdata,self.gui_config.samplerate)
        voice = self.soundpad.voice
        if voice and not voice['paused']:
            self.soundpad.protect_output(outdata, 'mic', self.gui_config.samplerate)
        else:
            np.clip(outdata, -1, 1, out=outdata)
        self.studio_tools.audio(indata, outdata, self.gui_config.samplerate)
        return result

    def devices(self, *args, **kwargs):
        # RVC reinitializes PortAudio: close both streams before it does so.
        self.soundpad.before_device_refresh()
        self.studio_tools.stop_play(restart_monitor=False)
        stop(self)
        try:
            result = original_devices(self, *args, **kwargs)
        finally:
            self.soundpad.after_device_refresh()
        labels = self.output_monitor.refresh()
        window = getattr(self, 'window', None)
        if window and 'monitor_device' in window.widgets:
            window['monitor_device'].update(values=labels, value=self.output_monitor.selected)
        return result

    gui_class.__init__ = init
    gui_class.start_stream = start
    gui_class.stop_stream = stop
    gui_class.audio_callback = audio
    gui_class.clear_audio_cache = clear_audio_cache
    gui_class.update_devices = devices
    gui_class._rvc_monitor_installed = True
