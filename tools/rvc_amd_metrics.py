"""Report GPU Engine utilization for the *vc-rs child PID*, never the Python PID."""
from __future__ import annotations
import ctypes
import math
import os
import threading

class ValueUnion(ctypes.Union):
    _fields_ = [("longValue", ctypes.c_int32), ("doubleValue", ctypes.c_double),
                ("largeValue", ctypes.c_int64)]
class CounterValue(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = [("status", ctypes.c_uint32), ("value", ValueUnion)]
class CounterItem(ctypes.Structure):
    _fields_ = [("name", ctypes.c_wchar_p), ("value", CounterValue)]

class ChildGpuMeter:
    def __init__(self, pid_getter):
        self.pid_getter = pid_getter
        self.value = None
        self._stop = threading.Event()
        self._thread = None

    def start(self):
        if os.name == "nt" and not self._thread:
            self._thread = threading.Thread(target=self._run,
                                            name="RVC-AMD-GPU-counter", daemon=True)
            self._thread.start()

    def close(self):
        self._stop.set()

    def _run(self):
        pdh = ctypes.WinDLL("pdh")
        handle = ctypes.c_void_p
        status = ctypes.c_int32
        defs = {
            "PdhOpenQueryW": [ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(handle)],
            "PdhAddEnglishCounterW": [handle, ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(handle)],
            "PdhCollectQueryData": [handle],
            "PdhGetFormattedCounterArrayW": [handle, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
                                            ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p],
            "PdhCloseQuery": [handle],
        }
        for key, signature in defs.items():
            fn = getattr(pdh, key)
            fn.argtypes, fn.restype = signature, status
        query = handle()
        counter = handle()
        try:
            if pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)):
                return
            if pdh.PdhAddEnglishCounterW(query, r"\GPU Engine(*)\Utilization Percentage", 0, ctypes.byref(counter)):
                return
            pdh.PdhCollectQueryData(query)
            while not self._stop.wait(1.0):
                if pdh.PdhCollectQueryData(query):
                    self.value = None
                    continue
                length = ctypes.c_uint32()
                count = ctypes.c_uint32()
                code = pdh.PdhGetFormattedCounterArrayW(counter, 0x200, ctypes.byref(length),
                                                       ctypes.byref(count), None)
                if code & 0xffffffff != 0x800007d2 or not length.value:
                    self.value = None
                    continue
                raw = ctypes.create_string_buffer(length.value)
                if pdh.PdhGetFormattedCounterArrayW(counter, 0x200, ctypes.byref(length),
                                                   ctypes.byref(count), raw):
                    self.value = None
                    continue
                pid = self.pid_getter()
                if not pid:
                    self.value = None
                    continue
                items = ctypes.cast(raw, ctypes.POINTER(CounterItem))
                prefix = "pid_" + str(pid) + "_"
                vals = [float(items[i].value.doubleValue) for i in range(count.value)
                        if (items[i].name or "").startswith(prefix)
                        and items[i].value.status in (0, 1)
                        and math.isfinite(items[i].value.doubleValue)]
                self.value = max(0.0, min(100.0, max(vals))) if vals else None
        except (OSError, AttributeError):
            self.value = None
        finally:
            if query:
                pdh.PdhCloseQuery(query)
