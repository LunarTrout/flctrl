"""session_state.py — маленькое состояние сессии, которое не относится к FL.

Сейчас это рабочее окно (/section): отрезок в секундах, к которому привязаны
замеры. Хранится в памяти процесса: это не свойство проекта, а выбор того, кто
сейчас работает.
"""
from __future__ import annotations

_SECTION = (0.0, 0.0)


def get_section() -> tuple:
    return _SECTION


def set_section(a: float, b: float) -> tuple:
    global _SECTION
    _SECTION = (float(a), float(b))
    return _SECTION


def reset() -> None:
    global _SECTION
    _SECTION = (0.0, 0.0)
