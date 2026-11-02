"""Проверка, что копии модулей библиотеки не разошлись.

Зачем: код библиотеки живёт в репозитории (`flstudio-lib/flctrl`), но в рабочей
папке исторически лежат копии с теми же именами. Однажды fl_lib.py разошёлся на
435 строк, и правки уходили не в тот файл.

Скрипт сравнивает все одноимённые .py и печатает расхождения. Ненулевой код
возврата — копии разошлись, их надо синхронизировать (или удалить).

    python tools/check_duplicates.py
"""
from __future__ import annotations

import hashlib
import os
import sys

LIB = os.environ.get("FLCTRL_LIB", r"C:\Users\epicc\mcp\flstudio-lib\flctrl")
WORK = os.environ.get("FLCTRL_WORK", r"C:\Users\epicc\mcp\flstudio-mcp\tools-local")

# Файлы, которые обязаны совпадать побайтно (это код библиотеки, а не скрипты).
CANON = ("fl_lib.py", "hear_cli.py", "listen_layer.py", "perceptual.py",
         "judge.py", "predict.py", "sandbox.py", "stems.py", "bandprofile.py",
         "profile_cache.py", "describe.py", "mixstyle.py", "q.py", "safe_fl.py",
         "flsave.py", "flwait.py", "track_capture.py")


def digest(path: str) -> str | None:
    try:
        with open(path, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except OSError:
        return None


def main() -> int:
    if not os.path.isdir(LIB) or not os.path.isdir(WORK):
        print("нет папок:", LIB, WORK)
        return 2
    same, differ, missing = [], [], []
    for name in CANON:
        a = digest(os.path.join(LIB, name))
        b = digest(os.path.join(WORK, name))
        if a is None:
            missing.append(name)
        elif b is None:
            differ.append(name + " (в рабочей папке нет)")
        elif a == b:
            same.append(name)
        else:
            differ.append(name)
    print("совпадают: %d" % len(same))
    if missing:
        print("нет в библиотеке:", ", ".join(missing))
    if differ:
        print("РАСХОДЯТСЯ:", ", ".join(differ))
        print("лечение: cp %s/<файл> %s/<файл>" % (LIB, WORK))
        return 1
    print("все копии совпадают")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
