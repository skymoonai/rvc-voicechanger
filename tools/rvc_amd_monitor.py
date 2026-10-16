"""Optional monitoring of the *converted* vc-rs signal through a virtual cable.

vc-rs owns its own WASAPI output. RVC Studio's original OutputMonitor listens
to a Python callback that does not exist in AMD mode. For AMD we explicitly
capture the recording half of a user-selected loopback/virtual-cable output.
No hidden echo route from the physical microphone is allowed.
"""
from __future__ import annotations

import re


def normalize_device(value: str) -> str:
    x = re.sub(r"\s+", " ", value.lower()).strip()
    x = re.sub(r"\b(input|output|speakers|microphone|line|cable)\b", "", x)
    return re.sub(r"[^a-z0-9]+", "", x)


def choose_loopback(cable_name: str, devices: list[dict]) -> int | None:
    """Only match a recording endpoint if the output looks like a virtual cable."""
    needle = cable_name.lower()
    if not any(word in needle for word in ("virtual audio cable", "vb-audio", "vb-cable", "cable input", "line 1", "voicemeeter")):
        return None
    possibilities = []
    for i, d in enumerate(devices):
        if not int(d.get("max_input_channels", 0)):
            continue
        name = str(d.get("name", ""))
        if normalize_device(name) == normalize_device(cable_name):
            possibilities.append((i, 2))
        elif "cable" in needle and "cable" in name.lower():
            possibilities.append((i, 1))
    best = [index for index, score in possibilities if score == max((v for _, v in possibilities), default=-1)]
    return best[0] if len(best) == 1 else None


class ConvertedVoiceMonitor:
    def __init__(self, status=lambda _: None):
        self.status = status
        self.stream = None

    def stop(self):
        old, self.stream = self.stream, None
        if old is not None:
            try:
                old.abort()
                old.close()
            except Exception as exc:
                self.status("AMD dinleme kapatılırken hata: " + str(exc))

    def configure(self, enabled: bool, rust_output: str, headphones: str, volume: float = 1.0):
        self.stop()
        if not enabled or not rust_output:
            return
        if rust_output.casefold() == headphones.casefold():
            self.status("AMD çıkışı zaten kulaklıkta; ikinci bir dinleme akışı gerekmiyor.")
            return
        import sounddevice as sd
        devices = list(sd.query_devices())
        index = choose_loopback(rust_output, devices)
        if index is None:
            self.status("Dönüştürülmüş sesin dinlenmesi için sanal kablonun kayıt ucu bulunamadı. " 
                        "VB-CABLE çıkışı seç veya ses aygıtlarını kontrol et.")
            return
        output = [i for i, d in enumerate(devices)
                  if int(d.get("max_output_channels", 0)) and str(d.get("name", "")).casefold() == headphones.casefold()]
        if len(output) != 1:
            self.status("AMD dinleme kulaklığı seçili değil veya aynı isimli birden fazla cihaz var.")
            return
        rate = int(float(devices[index].get("default_samplerate", 48000)))
        gain = max(0.0, min(3.0, float(volume)))

        def copy_audio(indata, outdata, frames, time_info, callback_status):
            if callback_status:
                pass  # callback must never block the real-time audio thread
            outdata.fill(0)
            outdata[:, 0] = indata[:, 0] * gain
            if outdata.shape[1] > 1:
                outdata[:, 1] = outdata[:, 0]

        try:
            stream = sd.Stream(
                device=(index, output[0]),
                channels=(1, 2), samplerate=rate, blocksize=0,
                dtype="float32", latency="low", callback=copy_audio
            )
            stream.start()
            self.stream = stream
            self.status("AMD: dönüştürülmüş ses sanal kablodan kulaklığa yönlendirildi.")
        except Exception as exc:
            self.status("AMD dinleme açılamadı: " + str(exc))
