"""Connect AMD Edition Soundpad microphone lane to vc-rs WASAPI output.

The stock Soundpad already supports a separate sounddevice.OutputStream
whenever the old Python RVC stream is inactive. Native vc-rs also opens its
own WASAPI output; in shared mode, Windows mixes both onto the same endpoint.
This routes clips to the same *virtual microphone cable* as converted voice
without pretending to mix into vc-rs internal DSP.

Exclusive WASAPI sessions and certain virtual sound devices can reject a second
shared stream. The Soundpad status UI will report those failures.
"""
from __future__ import annotations

from types import MethodType


def wasapi_device_index(name: str, devices, apis) -> int | None:
    if not name:
        return None
    matched = []
    for i, entry in enumerate(devices):
        if (int(entry.get("max_output_channels", 0)) > 0
                and str(entry.get("name", "")).casefold() == name.casefold()):
            api = apis[entry.get("hostapi", 0)]["name"].casefold()
            matched.append((i, "wasapi" in api))
    preferred = [i for i, wasapi in matched if wasapi]
    if len(preferred) == 1:
        return preferred[0]
    return matched[0][0] if len(matched) == 1 else None


def install_soundpad(pad, edition):
    original = pad._output_device

    def amd_device(self, lane):
        if lane == "mic":
            try:
                import sounddevice as sd
                label = str(edition.read_value("sg_output_device", "") or "")
                match = wasapi_device_index(label, sd.query_devices(), sd.query_hostapis())
                if match is not None:
                    return match
                if label:
                    self.error = "AMD Soundpad: seçili WASAPI çıkışı bulunamadı: " + label
                    return None
            except Exception as exc:
                self.error = "AMD Soundpad aygıt sorgulama hatası: " + str(exc)
                return None
        return original(lane)

    pad._output_device = MethodType(amd_device, pad)
    pad.device_cache.clear()
