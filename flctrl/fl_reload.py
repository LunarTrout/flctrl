"""fl_reload.py -- развернуть контроллер в FL и перезапустить её без участия человека.

Что делает:
  1. копирует свежий device_FLStudioMCP.py из репозитория в папку FL
  2. чистит __pycache__
  3. мягко закрывает FL (WM_CLOSE, затем force через таймаут)
  4. открывает FL с нужным проектом
  5. ждёт, пока мост ответит, и печатает вердикт

Запуск:
    python tools-local/fl_reload.py [путь к .flp]
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time

REPO = r"C:\Users\epicc\mcp\flstudio-mcp"
SRC = os.path.join(REPO, "fl_controller", "FLStudioMCP", "device_FLStudioMCP.py")
DST_DIR = r"C:\Users\epicc\Documents\Image-Line\FL Studio\Settings\Hardware\FLStudioMCP"
DST = os.path.join(DST_DIR, "device_FLStudioMCP.py")
FL = r"C:\Programs\Fl Studio 2025\FL64.exe"
DEFAULT_FLP = r"C:\Users\epicc\Desktop\PROJECTS\vers1\vers1.flp"
DAEMON_BAT = os.path.join(REPO, "start-daemon.bat")
TCP = ("127.0.0.1", 9787)


def run_ps(cmd: str) -> None:
    subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                   capture_output=True, text=True)


def deploy() -> None:
    os.makedirs(DST_DIR, exist_ok=True)
    shutil.copy2(SRC, DST)
    pyc = os.path.join(DST_DIR, "__pycache__")
    if os.path.isdir(pyc):
        shutil.rmtree(pyc, ignore_errors=True)
    print("контроллер развёрнут:", DST)


def fl_running() -> bool:
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-Process FL64 -ErrorAction SilentlyContinue | Measure-Object).Count"],
                         capture_output=True, text=True).stdout.strip()
    return out not in ("", "0")


def close_fl(wait_s: int = 25) -> None:
    if not fl_running():
        return
    # мягко: WM_CLOSE главному окну
    run_ps("Get-Process FL64 -ErrorAction SilentlyContinue | "
           "ForEach-Object { $_.CloseMainWindow() | Out-Null }")
    t0 = time.time()
    while time.time() - t0 < wait_s:
        if not fl_running():
            print("FL закрыт мягко")
            return
        time.sleep(1)
    run_ps("Get-Process FL64 -ErrorAction SilentlyContinue | Stop-Process -Force")
    time.sleep(3)
    print("FL закрыт принудительно")


def start_fl(flp: str) -> None:
    run_ps("Start-Process '%s' -ArgumentList '%s'" % (FL, flp))
    print("FL запущен с", os.path.basename(flp))


def ensure_daemon() -> None:
    try:
        s = socket.create_connection(TCP, timeout=3)
        s.close()
        return
    except OSError:
        pass
    run_ps("Start-Process -FilePath '%s' -WindowStyle Hidden" % DAEMON_BAT)
    for _ in range(20):
        time.sleep(1)
        try:
            s = socket.create_connection(TCP, timeout=2)
            s.close()
            print("демон поднят")
            return
        except OSError:
            continue
    print("демон не поднялся")


def ping(timeout_s: int = 150) -> dict | None:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        try:
            s = socket.create_connection(TCP, timeout=5)
            s.sendall((json.dumps({"op": "ping"}) + "\n").encode())
            data = s.recv(8192)
            s.close()
            msg = json.loads(data.decode("utf-8", "replace").strip().splitlines()[0])
            return msg
        except Exception:
            time.sleep(3)
    return None


def main() -> int:
    flp = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_FLP
    deploy()
    close_fl()
    ensure_daemon()
    start_fl(flp)
    print("жду ответ моста...")
    msg = ping()
    if msg:
        print("МОСТ ЖИВ:", json.dumps(msg, ensure_ascii=False)[:300])
        return 0
    print("мост не ответил")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
