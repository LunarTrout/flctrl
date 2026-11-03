"""proc_loopback.py — НЕ РАБОТАЕТ (оставлено как запись попытки).

`ActivateAudioInterfaceAsync` с процессным loopback возвращает E_UNEXPECTED:
comtypes-объект обработчика не маршалится в поток, откуда COM его вызывает.
Нужен обработчик, зарегистрированный в GIT, или реализация на C.

Рабочая замена — loopback.py (системный выход) или маршрутизация FL на отдельное
устройство и захват его loopback.

--- исходное описание ---
Захват звука ОДНОГО процесса (Windows 10 2004+).

Отличие от loopback.py: там пишется весь системный выход (FL + видео + браузер),
здесь — только дерево процессов указанного PID, через
`ActivateAudioInterfaceAsync` с `AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`.

    python -m flctrl.proc_loopback --pid 1234 --seconds 5 --out C:/tmp/fl.wav

Формат: float32, 2 канала, 48 кГц (процессный loopback других не поддерживает).
"""
from __future__ import annotations

import argparse
import ctypes
import os
import struct
import subprocess
import sys
import threading
import time
from ctypes import POINTER, byref, c_void_p, cast, wintypes

import comtypes
from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

# --------------------------------------------------------------------- константы
VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"
IID_IAudioClient = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
IID_IAudioCaptureClient = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
IID_IActivateAudioInterfaceCompletionHandler = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
IID_IActivateAudioInterfaceAsyncOperation = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")

AUDCLNT_SHAREMODE_SHARED = 0
AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
AUDCLNT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0

WAVE_FORMAT_IEEE_FLOAT = 3
VT_BLOB = 65


class WAVEFORMATEX(ctypes.Structure):
    _fields_ = [("wFormatTag", wintypes.WORD), ("nChannels", wintypes.WORD),
                ("nSamplesPerSec", wintypes.DWORD), ("nAvgBytesPerSec", wintypes.DWORD),
                ("nBlockAlign", wintypes.WORD), ("wBitsPerSample", wintypes.WORD),
                ("cbSize", wintypes.WORD)]


class BLOB(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.ULONG), ("pBlobData", c_void_p)]


class PROPVARIANT(ctypes.Structure):
    _fields_ = [("vt", wintypes.USHORT), ("wReserved1", wintypes.USHORT),
                ("wReserved2", wintypes.USHORT), ("wReserved3", wintypes.USHORT),
                ("blob", BLOB)]


class AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS(ctypes.Structure):
    _fields_ = [("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]


class AUDIOCLIENT_ACTIVATION_PARAMS(ctypes.Structure):
    _fields_ = [("ActivationType", ctypes.c_int),
                ("ProcessLoopbackParams", AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS)]


# ------------------------------------------------------------------- интерфейсы
class IActivateAudioInterfaceAsyncOperation(IUnknown):
    _iid_ = IID_IActivateAudioInterfaceAsyncOperation
    _methods_ = [
        COMMETHOD([], HRESULT, "GetActivateResult",
                  (["out"], POINTER(HRESULT), "activateResult"),
                  (["out"], POINTER(POINTER(IUnknown)), "activatedInterface")),
    ]


class IActivateAudioInterfaceCompletionHandler(IUnknown):
    _iid_ = IID_IActivateAudioInterfaceCompletionHandler
    _methods_ = [
        COMMETHOD([], HRESULT, "ActivateCompleted",
                  (["in"], POINTER(IActivateAudioInterfaceAsyncOperation), "operation")),
    ]


class IAudioCaptureClient(IUnknown):
    _iid_ = IID_IAudioCaptureClient
    _methods_ = [
        COMMETHOD([], HRESULT, "GetBuffer",
                  (["out"], POINTER(POINTER(ctypes.c_ubyte)), "data"),
                  (["out"], POINTER(wintypes.UINT), "frames"),
                  (["out"], POINTER(wintypes.DWORD), "flags"),
                  (["out"], POINTER(ctypes.c_ulonglong), "devicePosition"),
                  (["out"], POINTER(ctypes.c_ulonglong), "qpcPosition")),
        COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], wintypes.UINT, "frames")),
        COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], POINTER(wintypes.UINT), "frames")),
    ]


class IAudioClient(IUnknown):
    _iid_ = IID_IAudioClient
    _methods_ = [
        COMMETHOD([], HRESULT, "Initialize",
                  (["in"], ctypes.c_int, "shareMode"),
                  (["in"], wintypes.DWORD, "streamFlags"),
                  (["in"], ctypes.c_longlong, "bufferDuration"),
                  (["in"], ctypes.c_longlong, "periodicity"),
                  (["in"], POINTER(WAVEFORMATEX), "format"),
                  (["in"], c_void_p, "audioSessionGuid")),
        COMMETHOD([], HRESULT, "GetBufferSize", (["out"], POINTER(wintypes.UINT), "frames")),
        COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], POINTER(ctypes.c_longlong), "latency")),
        COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], POINTER(wintypes.UINT), "frames")),
        COMMETHOD([], HRESULT, "IsFormatSupported",
                  (["in"], ctypes.c_int, "shareMode"),
                  (["in"], POINTER(WAVEFORMATEX), "format"),
                  (["out"], POINTER(POINTER(WAVEFORMATEX)), "closest")),
        COMMETHOD([], HRESULT, "GetMixFormat", (["out"], POINTER(POINTER(WAVEFORMATEX)), "format")),
        COMMETHOD([], HRESULT, "GetDevicePeriod",
                  (["out"], POINTER(ctypes.c_longlong), "defaultPeriod"),
                  (["out"], POINTER(ctypes.c_longlong), "minimumPeriod")),
        COMMETHOD([], HRESULT, "Start"),
        COMMETHOD([], HRESULT, "Stop"),
        COMMETHOD([], HRESULT, "Reset"),
        COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "handle")),
        COMMETHOD([], HRESULT, "GetService",
                  (["in"], POINTER(GUID), "iid"),
                  (["out"], POINTER(POINTER(IUnknown)), "service")),
    ]


class CompletionHandler(comtypes.COMObject):
    _com_interfaces_ = [IActivateAudioInterfaceCompletionHandler]

    def __init__(self):
        super().__init__()
        self.done = threading.Event()
        self.operation = None

    def ActivateCompleted(self, operation):
        self.operation = operation
        self.done.set()
        return 0


def _activate(pid: int, timeout: float = 5.0):
    """Получить IAudioClient для процессного loopback."""
    params = AUDIOCLIENT_ACTIVATION_PARAMS()
    params.ActivationType = AUDCLNT_ACTIVATION_TYPE_PROCESS_LOOPBACK
    params.ProcessLoopbackParams.TargetProcessId = pid
    params.ProcessLoopbackParams.ProcessLoopbackMode = \
        PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE

    prop = PROPVARIANT()
    prop.vt = VT_BLOB
    prop.blob.cbSize = ctypes.sizeof(params)
    prop.blob.pBlobData = cast(byref(params), c_void_p)

    handler = CompletionHandler()
    op_ptr = POINTER(IActivateAudioInterfaceAsyncOperation)()
    mmdev = ctypes.WinDLL("Mmdevapi.dll")
    fn = mmdev.ActivateAudioInterfaceAsync
    fn.argtypes = [wintypes.LPCWSTR, POINTER(GUID), POINTER(PROPVARIANT),
                   POINTER(IActivateAudioInterfaceCompletionHandler),
                   POINTER(POINTER(IActivateAudioInterfaceAsyncOperation))]
    fn.restype = HRESULT

    handler_iface = handler.QueryInterface(IActivateAudioInterfaceCompletionHandler)
    hr = fn(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK, byref(IID_IAudioClient), byref(prop),
            handler_iface, byref(op_ptr))
    if hr != 0:
        raise RuntimeError("ActivateAudioInterfaceAsync hr=0x%08x" % (hr & 0xFFFFFFFF))

    if not handler.done.wait(timeout):
        raise RuntimeError("обработчик не ответил")

    op = handler.operation or op_ptr
    result = HRESULT()
    iface = POINTER(IUnknown)()
    op.GetActivateResult(byref(result), byref(iface))
    if result.value != 0:
        raise RuntimeError("GetActivateResult hr=0x%08x" % (result.value & 0xFFFFFFFF))
    client = cast(iface, POINTER(IAudioClient))
    return client, handler


def record(pid: int, seconds: float = 5.0, path: str | None = None,
           rate: int = 48000, channels: int = 2) -> dict:
    """Запись в отдельном MTA-потоке: ActivateAudioInterfaceAsync требует MTA."""
    box: dict = {}

    def worker():
        ole = ctypes.windll.ole32
        ole.CoInitializeEx(None, 0x0)          # COINIT_MULTITHREADED
        try:
            box["value"] = _record_inner(pid, seconds, path, rate, channels)
        except Exception as e:                  # noqa: BLE001
            box["error"] = e
        finally:
            try:
                ole.CoUninitialize()
            except Exception:                   # noqa: BLE001
                pass

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    thread.join(seconds + 10.0)
    if "error" in box:
        raise box["error"]
    if "value" not in box:
        raise RuntimeError("запись не завершилась")
    return box["value"]


def _record_inner(pid: int, seconds: float, path: str | None,
                  rate: int, channels: int) -> dict:
    if True:
        client, _handler = _activate(pid)

        fmt = WAVEFORMATEX()
        fmt.wFormatTag = WAVE_FORMAT_IEEE_FLOAT
        fmt.nChannels = channels
        fmt.nSamplesPerSec = rate
        fmt.wBitsPerSample = 32
        fmt.nBlockAlign = channels * 4
        fmt.nAvgBytesPerSec = rate * fmt.nBlockAlign
        fmt.cbSize = 0

        # 1 секунда буфера
        client.Initialize(AUDCLNT_SHAREMODE_SHARED,
                          AUDCLNT_STREAMFLAGS_LOOPBACK,
                          10_000_000, 0, byref(fmt), None)

        cap = client.GetService(byref(IID_IAudioCaptureClient))
        capture = cast(cap, POINTER(IAudioCaptureClient))
        client.Start()

        frames = int(rate * seconds)
        out = bytearray()
        deadline = time.time() + seconds + 1.0
        while len(out) < frames * fmt.nBlockAlign and time.time() < deadline:
            need = ctypes.c_uint(0)
            capture.GetNextPacketSize(byref(need))
            if not need.value:
                time.sleep(0.005)
                continue
            data = POINTER(ctypes.c_ubyte)()
            nframes = wintypes.UINT(0)
            flags = wintypes.DWORD(0)
            pos = ctypes.c_ulonglong(0)
            qpc = ctypes.c_ulonglong(0)
            capture.GetBuffer(byref(data), byref(nframes), byref(flags), byref(pos), byref(qpc))
            size = nframes.value * fmt.nBlockAlign
            if size:
                out.extend(ctypes.string_at(data, size))
            capture.ReleaseBuffer(nframes.value)
        client.Stop()

        path = path or os.path.join(os.environ.get("TEMP", "."), "proc_loopback.wav")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        raw = bytes(out)
        header = b"RIFF" + struct.pack("<I", 36 + len(raw)) + b"WAVE"
        header += b"fmt " + struct.pack("<IHHIIHH", 16, 3, channels, rate,
                                        rate * channels * 4, channels * 4, 32)
        header += b"data" + struct.pack("<I", len(raw))
        with open(path, "wb") as fh:
            fh.write(header)
            fh.write(raw)

        import numpy as np
        a = np.frombuffer(raw, dtype="<f4").reshape(-1, channels)
        mono = a.mean(axis=1)
        return {"path": path, "seconds": round(len(a) / rate, 2), "rate": rate,
                "channels": channels, "pid": pid,
                "rms": round(float(np.sqrt((mono ** 2).mean())), 5),
                "peak": round(float(np.abs(mono).max()), 4)}


def pid_of(name: str = "FL64.exe"):
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq %s" % name, "/FO", "CSV", "/NH"],
                         capture_output=True)
    for line in out.stdout.decode("cp866", "replace").splitlines():
        p = [x.strip('"') for x in line.split('","')]
        if p and p[0].lower() == name.lower():
            try:
                return int(p[1])
            except (ValueError, IndexError):
                return None
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, default=0)
    ap.add_argument("--seconds", type=float, default=5.0)
    ap.add_argument("--out", default=os.path.join(os.environ.get("TEMP", "."), "proc_loopback.wav"))
    a = ap.parse_args(argv)
    pid = a.pid or pid_of()
    if not pid:
        print("FL не запущена")
        return 2
    try:
        res = record(pid, a.seconds, a.out)
    except Exception as e:  # noqa: BLE001
        print("ошибка:", e)
        return 3
    print("записано %.2f с, rms %.5f, пик %.3f -> %s"
          % (res["seconds"], res["rms"], res["peak"], res["path"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
