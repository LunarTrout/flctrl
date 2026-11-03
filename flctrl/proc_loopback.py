"""proc_loopback.py — захват звука ТОЛЬКО указанного процесса (Windows 10 2004+).

Использует WASAPI process loopback: виртуальное устройство `VAD\\Process_Loopback`
с режимом INCLUDE — в запись попадает только дерево процессов нужного PID.

Работает на ctypes-реализации COM-обработчика (`_wasapi_proc.py`): прежняя
попытка на comtypes падала с E_UNEXPECTED, потому что обработчик не маршалился
в чужой поток.

    python -m flctrl.proc_loopback --seconds 3 --out C:/tmp/fl_only.wav
"""
from __future__ import annotations

import argparse
import os
import struct
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _wasapi_proc as W  # noqa: E402


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


def capability() -> dict:
    cap = W.get_process_loopback_capability()
    return {"available": cap.available, "reason": cap.reason,
            "windows_build": cap.windows_build}


def record(seconds: float = 5.0, path: str | None = None, pid: int | None = None,
           sample_rate: int = 48000, channels: int = 2) -> dict:
    """Писать звук только указанного процесса (по умолчанию FL64.exe)."""
    target = pid or pid_of()
    if not target:
        return {"error": "FL не запущена"}

    chunks: list[bytes] = []

    def on_data(data: bytes, frames: int, flags: int) -> None:
        if data and (flags & W._AUDCLNT_BUFFERFLAGS_SILENT) == 0:
            chunks.append(bytes(data))

    capture = W.ProcessLoopbackCapture(on_data, sample_rate=sample_rate, channels=channels)
    capture.target_pid = target
    capture.include = True
    try:
        capture.start()
    except Exception as e:  # noqa: BLE001
        return {"error": str(e)[:300], "pid": target}

    time.sleep(seconds)
    capture.stop()

    raw = b"".join(chunks)
    if not raw:
        return {"error": "процесс не отдал звук", "pid": target}
    path = path or os.path.join(os.environ.get("TEMP", "."), "proc_only.wav")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    header = b"RIFF" + struct.pack("<I", 36 + len(raw)) + b"WAVE"
    header += b"fmt " + struct.pack("<IHHIIHH", 16, 3, channels, sample_rate,
                                    sample_rate * channels * 4, channels * 4, 32)
    header += b"data" + struct.pack("<I", len(raw))
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(raw)

    import numpy as np
    x = np.frombuffer(raw, dtype="<f4")
    if x.size % channels:
        x = x[:x.size // channels * channels]
    a = x.reshape(-1, channels)
    mono = a.mean(axis=1)
    return {"path": path, "pid": target, "seconds": round(len(a) / sample_rate, 2),
            "rms": round(float(np.sqrt((mono ** 2).mean())), 5),
            "peak": round(float(np.abs(mono).max()), 4),
            "silent": bool(np.abs(mono).max() < 1e-5)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--out", default=os.path.join(os.environ.get("TEMP", "."), "proc_only.wav"))
    ap.add_argument("--pid", type=int, default=0)
    a = ap.parse_args(argv)
    print("возможность:", capability())
    res = record(a.seconds, a.out, a.pid or None)
    print(res)
    return 0 if not res.get("error") else 3


if __name__ == "__main__":
    raise SystemExit(main())
