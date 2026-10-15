"""Read this process's busiest Windows GPU engine without blocking the GUI."""
from __future__ import annotations
import ctypes
import math
import os
import threading


class ValueUnion(ctypes.Union):
    _fields_ = [('longValue', ctypes.c_int32), ('doubleValue', ctypes.c_double), ('largeValue', ctypes.c_int64)]


class CounterValue(ctypes.Structure):
    _anonymous_ = ('value',)
    _fields_ = [('status', ctypes.c_uint32), ('value', ValueUnion)]


class CounterItem(ctypes.Structure):
    _fields_ = [('name', ctypes.c_wchar_p), ('value', CounterValue)]


def process_usage(items, pid):
    prefix = f'pid_{pid}_'
    values = [float(value) for name, status, value in items
              if name.startswith(prefix) and status in (0, 1) and math.isfinite(value)]
    return max(0., min(100., max(values, default=0.)))


class GpuUsage:
    def __init__(self):
        self.value = None
        self.error = ''
        self.stop_event = threading.Event()
        self.thread = None

    def start(self):
        if os.name == 'nt':
            self.thread = threading.Thread(target=self._run, name='RVC-GPU-meter', daemon=True)
            self.thread.start()

    def close(self):
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=1.5)

    def _run(self):
        pdh = ctypes.WinDLL('pdh')
        handle = ctypes.c_void_p
        dword = ctypes.c_uint32
        status = ctypes.c_int32
        functions = {
            'PdhOpenQueryW': [ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(handle)],
            'PdhAddEnglishCounterW': [handle, ctypes.c_wchar_p, ctypes.c_size_t, ctypes.POINTER(handle)],
            'PdhCollectQueryData': [handle],
            'PdhGetFormattedCounterArrayW': [handle, dword, ctypes.POINTER(dword), ctypes.POINTER(dword), ctypes.c_void_p],
            'PdhCloseQuery': [handle],
        }
        for name, args in functions.items():
            function = getattr(pdh, name)
            function.argtypes, function.restype = args, status
        query, counter = handle(), handle()
        try:
            if pdh.PdhOpenQueryW(None, 0, ctypes.byref(query)):
                raise RuntimeError('GPU sayacı açılamadı.')
            if pdh.PdhAddEnglishCounterW(query, r'\GPU Engine(*)\Utilization Percentage', 0, ctypes.byref(counter)):
                raise RuntimeError('GPU motoru sayacı bu sürücüde kullanılamıyor.')
            pdh.PdhCollectQueryData(query)
            while not self.stop_event.wait(1.):
                if pdh.PdhCollectQueryData(query):
                    self.value = None
                    continue
                size, count = dword(), dword()
                code = pdh.PdhGetFormattedCounterArrayW(counter, 0x200, ctypes.byref(size), ctypes.byref(count), None)
                if (code & 0xFFFFFFFF) != 0x800007D2 or not size.value:
                    self.value = None
                    continue
                buffer = ctypes.create_string_buffer(size.value)
                code = pdh.PdhGetFormattedCounterArrayW(counter, 0x200, ctypes.byref(size), ctypes.byref(count), buffer)
                if code:
                    self.value = None
                    continue
                items = ctypes.cast(buffer, ctypes.POINTER(CounterItem))
                self.value = process_usage([(items[i].name or '', items[i].value.status, items[i].value.doubleValue)
                                            for i in range(count.value)], os.getpid())
        except Exception as exc:
            self.error = str(exc)
            self.value = None
            print('[GPU göstergesi] ' + self.error, flush=True)
        finally:
            if query:
                pdh.PdhCloseQuery(query)