"""roles.py — привести любой проект к ролям инструментов.

Зачем: чтобы числа были сравнимы между разными проектами, дорожки надо
приводить не по именам, а по РОЛИ: барабаны, бас, гитары, клавиши, вокал.
Имена в чужих проектах врут («GTR 2» может быть синтом), поэтому роль
определяется по трём признакам: имя → спектр → запасное «other».

Роли намеренно крупные: жанровая модель баланса живёт на уровне ролей, а не
конкретных инструментов, иначе она не переносится между проектами.
"""
from __future__ import annotations

import os
import re
from typing import Iterable

ROLES = ("drums", "bass", "guitar", "keys", "vocals", "other")

PATTERNS = {
    "drums": r"drum|kick|snare|\bhat|cymbal|\bcym|perc|tom\b|sd3|superior|бочк|мал(ый|ая)|тарел|желез",
    "bass": r"\bbass\b|sub\b|бас(?!ян)|низ\b|808|midi bass|ample",
    "guitar": r"\bgtr|guitar|гитар|rhythm|ритм|lead|лид|riff|bariton|бари",
    "keys": r"key|piano|synth|bayan|баян|accord|organ|pad\b|atmo|атмо|str(ings)?\b|скрип",
    "vocals": r"vox|vocal|вокал|scream|скрим|voice|голос|choir|хор|ann\b|lead vox",
}

# Порядок важен: «bass» проверяется до «guitar» (bass guitar = бас),
# «bayan» до «bass» (баян не бас — на этом уже ошибались).
ORDER = ("drums", "vocals", "keys", "bass", "guitar")


def by_name(name: str) -> str | None:
    low = (name or "").lower()
    for role in ORDER:
        if re.search(PATTERNS[role], low):
            return role
    return None


def by_spectrum(path: str) -> str | None:
    """Грубая страховка, когда имя ничего не говорит.

    Смотрим, где сосредоточена энергия: ниже 120 Гц — бас/бочка, широкая
    середина — гитары/клавиши, верх без низа — тарелки.
    """
    try:
        import numpy as np
        import soundfile as sf

        x, fs = sf.read(path, dtype="float64", always_2d=True)
        if len(x) == 0:
            return None
        mono = x.mean(axis=1)
        spec = np.abs(np.fft.rfft(mono * np.hanning(len(mono)))) ** 2
        freqs = np.fft.rfftfreq(len(mono), 1.0 / fs)
        total = spec.sum() or 1e-12
        low = spec[freqs < 120].sum() / total
        high = spec[freqs > 6000].sum() / total
        if low > 0.55:
            return "bass"
        if high > 0.45:
            return "drums"
        return "guitar"
    except Exception:
        return None


def role_of(name: str, path: str | None = None) -> str:
    """Роль дорожки: по имени, затем по спектру, затем «other»."""
    named = by_name(name)
    if named:
        return named
    if path and os.path.exists(path):
        guessed = by_spectrum(path)
        if guessed:
            return guessed
    return "other"


def group(names_paths: Iterable[tuple[str, str | None]]) -> dict[str, list[str]]:
    """Сгруппировать дорожки по ролям (несколько дорожек на роль — норма)."""
    out: dict[str, list[str]] = {}
    for name, path in names_paths:
        role = role_of(name, path)
        out.setdefault(role, []).append(name)
    return out
