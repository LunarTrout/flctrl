"""safe_fl.py -- единственный разрешённый способ запустить FL для тестов.

Почему: FL при запуске без аргументов переоткрывает последний проект, и так
случайно открывался рабочий проект пользователя. Этот скрипт:

  1. убивает любой уже запущенный FL, у которого в заголовке не тестовый проект;
  2. запускает FL ТОЛЬКО с тестовым проектом;
  3. проверяет заголовок окна и падает, если открылся не тот проект.

Использование:
    python safe_fl.py            # запустить FL с тестовым проектом
    python safe_fl.py --check    # только проверить, что открыт тестовый проект
    python safe_fl.py --kill     # закрыть FL, не сохраняя
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

FL = r"C:\Programs\Fl Studio 2025\FL64.exe"
TEST_PROJECT = r"C:\Users\epicc\tools\fltest\small.flp"
TEST_MARK = "small"


def _ps(cmd: str) -> str:
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-Command", cmd],
                             capture_output=True, text=True, timeout=60)
        return (out.stdout or "") + (out.stderr or "")
    except Exception as e:
        return "ERR %s" % e


def windows() -> list[tuple[int, str]]:
    raw = _ps("Get-Process FL64 -ErrorAction SilentlyContinue | "
              "ForEach-Object { \"$($_.Id)|$($_.MainWindowTitle)\" }")
    out = []
    for line in raw.splitlines():
        line = line.strip()
        if "|" in line:
            pid, _, title = line.partition("|")
            if pid.isdigit():
                out.append((int(pid), title))
    return out


def close_graceful(timeout_s: int = 25) -> bool:
    """Закрыть FL нормально, чтобы она сохранила настройки MIDI.

    Принудительное убийство ломает контроллер: FL пишет конфиг устройств
    только при штатном выходе. Поэтому сначала вежливое закрытие и ожидание.
    """
    for pid, _title in windows():
        _ps("(New-Object -ComObject WScript.Shell).AppActivate(%d) | Out-Null; "
            "(Get-Process -Id %d).CloseMainWindow() | Out-Null" % (pid, pid))
    for _ in range(int(timeout_s / 1.5)):
        time.sleep(1.5)
        if not windows():
            return True
    return not windows()


def kill_all() -> None:
    """Закрыть FL. ТОЛЬКО если это тестовый экземпляр или разрешено явно.

    Защита от потери работы: чужой проект (пользовательский) не закрывается
    никогда — ни агентом, ни стендом. Обойти можно только переменной
    окружения FL_ALLOW_KILL=1, и это осознанное действие, а не случайность.
    """
    if os.environ.get("FL_ALLOW_KILL") == "1":
        _ps("Get-Process FL64 -ErrorAction SilentlyContinue | Stop-Process -Force")
        return
    foreign = [w for w in windows() if TEST_MARK not in w[1].lower()]
    if foreign:
        raise RuntimeError(
            "FL открыта с чужим проектом, не закрываю: %s. "
            "Работа пользователя не должна теряться. Если закрыть всё же надо — "
            "FL_ALLOW_KILL=1." % ", ".join("%d %s" % (p, t or "?") for p, t in foreign))
    _ps("Get-Process FL64 -ErrorAction SilentlyContinue | Stop-Process -Force")


def kill_foreign() -> list[str]:
    """Закрыть всё, где в заголовке не тестовый проект. Вернуть список закрытых."""
    killed = []
    for pid, title in windows():
        if TEST_MARK not in title.lower():
            if os.environ.get("FL_ALLOW_KILL") != "1":
                raise RuntimeError(
                    "FL открыта с чужим проектом, не закрываю: %d %s. "
                    "Работа пользователя не должна теряться." % (pid, title or "?"))
            _ps("Stop-Process -Id %d -Force" % pid)
            killed.append("%d %s" % (pid, title or "(без заголовка)"))
    return killed


def launch() -> int:
    if not os.path.exists(TEST_PROJECT):
        print("НЕТ тестового проекта:", TEST_PROJECT)
        return 2
    subprocess.Popen([FL, TEST_PROJECT], close_fds=True)
    for _ in range(90):
        time.sleep(2)
        for _pid, title in windows():
            if TEST_MARK in title.lower():
                print("FL запущен с тестовым проектом:", title)
                return 0
    print("FL не открыл тестовый проект за отведённое время")
    return 3


def check() -> int:
    ws = windows()
    if not ws:
        print("FL не запущен")
        return 1
    bad = [w for w in ws if TEST_MARK not in w[1].lower()]
    if bad:
        print("ВНИМАНИЕ: открыт НЕ тестовый проект:", bad)
        return 4
    print("ок, открыт тестовый проект:", ws[0][1])
    return 0


def frida_spawn(extra_args: list[str] | None = None):
    """Запустить FL с тестовым проектом в ПРИОСТАНОВЛЕННОМ виде (для Frida).

    Возвращает (pid, session-фабрика) — вызывающий сам делает attach/resume.
    Все проверки те же, что у CLI: посторонние экземпляры закрываются,
    проект — только тестовый, заголовок проверяется после resume.
    """
    import frida

    killed = kill_foreign()
    if killed:
        print("закрыты посторонние экземпляры FL:", killed, flush=True)
    if not os.path.exists(TEST_PROJECT):
        raise RuntimeError("НЕТ тестового проекта: %s" % TEST_PROJECT)
    argv = [FL, TEST_PROJECT] + list(extra_args or [])
    pid = frida.spawn(argv, cwd=os.path.dirname(FL))
    print("frida spawn pid=%d -> %s" % (pid, TEST_PROJECT), flush=True)
    return pid


def wait_title(timeout_s: int = 120) -> bool:
    """Дождаться, пока в заголовке появится тестовый проект."""
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        for _pid, title in windows():
            if TEST_MARK in title.lower():
                return True
        time.sleep(1.0)
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--kill", action="store_true")
    a = ap.parse_args()

    if a.kill:
        kill_all()
        print("FL закрыт")
        return 0
    if a.check:
        return check()

    killed = kill_foreign()
    if killed:
        print("закрыты посторонние экземпляры FL:", killed)
    return launch()


if __name__ == "__main__":
    raise SystemExit(main())
