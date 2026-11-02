"""Ожидание готовности FL по факту, а не по часам.

Опрос вместо фиксированных пауз: ждём, пока окно получит имя проекта и мост
ответит на простой запрос. Возвращает время ожидания и состояние.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import subprocess
import time

MAIN_CLASS = "TFruityLoopsMainForm"
user32 = ctypes.windll.user32


def fl_pid() -> int:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-Process FL64 -EA SilentlyContinue).Id"],
        capture_output=True, text=True, timeout=30).stdout.strip()
    try:
        return int(out.splitlines()[0])
    except Exception:
        return 0


def window_title(pid: int) -> str:
    found = []

    def cb(hwnd, _):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid:
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value == MAIN_CLASS:
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            found.append(buf.value)
        return True

    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb)
    user32.EnumWindows(proc, 0)
    return found[0] if found else ""


def bridge_alive() -> bool:
    import q
    try:
        return int(q.live("mixer.trackCount()")) > 0
    except Exception:
        return False


def wait_ready(timeout: float = 180.0, need_bridge: bool = True) -> dict:
    """Ждать, пока откроется проект и (если надо) ответит контроллер."""
    t0 = time.time()
    title = ""
    while time.time() - t0 < timeout:
        pid = fl_pid()
        if pid:
            title = window_title(pid)
            loaded = title.lower().endswith(".flp - fl studio 2025")
            if loaded and (not need_bridge or bridge_alive()):
                return {"ready": True, "pid": pid, "title": title,
                        "seconds": round(time.time() - t0, 1)}
        time.sleep(2.0)
    return {"ready": False, "title": title,
            "seconds": round(time.time() - t0, 1), "timeout": timeout}


if __name__ == "__main__":
    print(wait_ready())
