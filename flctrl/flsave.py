"""Сохранение проекта FL без кликов мышью.

FL не имеет скриптового API сохранения, но штатно реагирует на Ctrl+S.
Прежняя версия слала сочетание через SendKeys — он работает только когда окно
реально активно, поэтому сохранение молча не происходило. Здесь окно сначала
поднимается на передний план (SetForegroundWindow с обходом ограничения через
AttachThreadInput), затем идут настоящие нажатия клавиш через keybd_event.
Результат проверяется по времени изменения файла.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os
import shutil
import subprocess
import time

PROJECT = r"C:\Users\epicc\Desktop\PROJECTS\vers1\vers1.flp"
BACKUP_DIR = r"C:\Users\epicc\Backups\flstudio"
MAIN_CLASS = "TFruityLoopsMainForm"

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

VK_CONTROL = 0x11
VK_S = 0x53
KEYEVENTF_KEYUP = 0x0002
SW_RESTORE = 9


def _pid() -> int:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "$p = Get-Process FL64 -EA SilentlyContinue; if ($p) { $p.Id }"],
        capture_output=True, text=True, timeout=30,
    ).stdout.strip()
    try:
        return int(out.splitlines()[0].strip())
    except Exception:
        return 0


def _find_main(pid: int) -> int:
    """Главное окно FL по классу TFruityLoopsMainForm."""
    found = []

    def cb(hwnd, _):
        p = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
        if p.value != pid or not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value == MAIN_CLASS:
            found.append(hwnd)
        return True

    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb)
    user32.EnumWindows(proc, 0)
    return found[0] if found else 0


def _focus(hwnd: int) -> None:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    fg = user32.GetForegroundWindow()
    fg_thread = user32.GetWindowThreadProcessId(fg, None)
    this_thread = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(fg_thread, this_thread, True)
    user32.SetForegroundWindow(hwnd)
    user32.BringWindowToTop(hwnd)
    user32.AttachThreadInput(fg_thread, this_thread, False)
    time.sleep(0.35)


def _ctrl_s() -> None:
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_S, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_S, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def backup(project: str = PROJECT, why: str = "pre-save") -> str | None:
    """Копия проекта перед сохранением: дешёвая страховка от порчи."""
    if not os.path.exists(project):
        return None
    os.makedirs(BACKUP_DIR, exist_ok=True)
    dst = os.path.join(BACKUP_DIR, "%s_%s_%s.flp" % (
        os.path.splitext(os.path.basename(project))[0], why,
        time.strftime("%H%M%S")))
    try:
        shutil.copy2(project, dst)
        return dst
    except OSError:
        return None


def current_project(default: str = PROJECT) -> str:
    """Путь открытого проекта: берём из заголовка окна FL.

    Имя в заголовке — только имя файла, поэтому ищем его рядом с известными
    проектами; если не нашли, возвращаем default.
    """
    import ctypes.wintypes as wt
    title = ""
    pid = _pid()
    if pid:
        found = []

        def cb(hwnd, _):
            p = wt.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(p))
            if p.value != pid or not user32.IsWindowVisible(hwnd):
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            if cls.value == MAIN_CLASS:
                length = user32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                found.append(buf.value)
            return True

        proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb)
        user32.EnumWindows(proc, 0)
        title = found[0] if found else ""
    name = title.split(" - ")[0].strip() if " - " in title else ""
    if not name.lower().endswith(".flp"):
        return default
    base = os.path.dirname(default)
    candidate = os.path.join(base, name)
    if os.path.exists(candidate):
        return candidate
    for root, _dirs, files in os.walk(base):
        if name in files:
            return os.path.join(root, name)
    return default


def save(project: str | None = None, make_backup: bool = False,
         timeout: float = 25.0) -> dict:
    """Сохранить проект и подтвердить это временем изменения файла."""
    if project is None:
        project = current_project()
    pid = _pid()
    if not pid:
        return {"ok": False, "error": "FL64 не запущена"}
    hwnd = _find_main(pid)
    if not hwnd:
        return {"ok": False, "error": "главное окно FL не найдено"}
    before = _mtime(project)
    copy = backup(project, "pre-save") if make_backup else None
    _focus(hwnd)
    _ctrl_s()
    deadline = time.time() + timeout
    while time.time() < deadline:
        now = _mtime(project)
        if now > before:
            return {"ok": True, "hwnd": hwnd, "file": project,
                    "before": before, "after": now, "backup": copy}
        time.sleep(0.5)
    return {"ok": False, "hwnd": hwnd, "file": project, "before": before,
            "after": _mtime(project), "backup": copy,
            "error": "файл не обновился (модальный диалог или окно не активно)"}


if __name__ == "__main__":
    print(save(make_backup=True))
