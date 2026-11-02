"""Поиск окружений и программ: ничего не должно быть вбито жёстко.

Логика простая: переменная окружения, потом соседние папки, потом реестр
и стандартные места. Если не нашлось — говорим что именно и где искать.
"""
from __future__ import annotations

import os
import sys


def _first(paths):
    for p in paths:
        if p and os.path.exists(p):
            return p
    return None


def package_dir() -> str:
    """Каталог пакета flctrl (здесь лежат fl_lib.py и hear_cli.py)."""
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.join(here, "flctrl"),
                 os.path.join(os.path.dirname(here), "flctrl"),
                 here):
        if os.path.exists(os.path.join(cand, "fl_lib.py")):
            return cand
    return os.path.join(os.path.dirname(here), "flctrl")


def _venv_python(base: str, tag: str) -> str | None:
    for sub in ("Scripts/python.exe", "bin/python"):
        p = os.path.join(base, tag, sub)
        if os.path.exists(p):
            return p
    return None


def hear_python() -> str:
    """Интерпретатор для слоя слуха (нужна phonometry, обычно 3.13)."""
    env = os.environ.get("FLCTRL_PY_HEAR")
    if env and os.path.exists(env):
        return env
    root = os.path.dirname(package_dir())
    cands = []
    for base in (root, os.path.dirname(root), os.getcwd()):
        for tag in (".venv313", ".venv", "venv"):
            cands.append(_venv_python(base, tag))
    found = _first(cands)
    if found and found != sys.executable:
        return found
    return sys.executable


def daemon() -> str | None:
    """Посредник до FL (fl-studio-mcp-daemon)."""
    env = os.environ.get("FLCTRL_DAEMON")
    if env and os.path.exists(env):
        return env
    names = ("fl-studio-mcp-daemon.exe", "fl-studio-mcp-daemon")
    cands = []
    for base in (r"C:\Users\%s\mcp\flstudio-mcp" % os.environ.get("USERNAME", ""),
                 os.path.expanduser("~/mcp/flstudio-mcp"),
                 os.path.expanduser("~/flstudio-mcp")):
        for tag in (".venv312", ".venv", ""):
            for n in names:
                cands.append(os.path.join(base, tag, "Scripts", n) if tag
                             else os.path.join(base, n))
    return _first(cands)


def fl_exe() -> str | None:
    """Исполняемый файл FL Studio."""
    env = os.environ.get("FLCTRL_FL_EXE")
    if env and os.path.exists(env):
        return env
    cands = [r"C:\Programs\Fl Studio 2025\FL64.exe",
             r"C:\Program Files\Image-Line\FL Studio 2025\FL64.exe",
             r"C:\Program Files\Image-Line\FL Studio 21\FL64.exe"]
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            r"SOFTWARE\Image-Line\Shared") as k:
            for i in range(winreg.QueryInfoKey(k)[1]):
                name, val, _ = winreg.EnumValue(k, i)
                if isinstance(val, str) and os.path.isdir(val):
                    cands.append(os.path.join(val, "FL Studio 2025", "FL64.exe"))
                    cands.append(os.path.join(val, "FL64.exe"))
    except Exception:
        pass
    return _first(cands)


def fl_install_dir() -> str | None:
    exe = fl_exe()
    return os.path.dirname(exe) if exe else None


def summary() -> dict:
    return {"package": package_dir(), "hear_python": hear_python(),
            "daemon": daemon(), "fl_exe": fl_exe(), "python": sys.executable}
