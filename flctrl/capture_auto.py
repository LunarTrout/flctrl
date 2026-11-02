"""Захват без угадывания устройства: слушаем все кандидаты и берём живой.

После перезапуска FL может писать в кабель, в карту или никуда — при этом
запись «с устройства по умолчанию» даёт тишину, и это выглядит как поломка
аудио. Здесь сначала короткая проба с каждого устройства, потом запись с того,
где реально есть сигнал.
"""
from __future__ import annotations

import time

import numpy as np

CANDIDATES = (
    "Line 1 (Virtual Audio Cable)",
    "Динамики (Focusrite USB Audio)",
)


def _probe(device: str, seconds: float = 1.0, fs: int = 48000) -> float:
    import soundcard as sc
    try:
        mic = sc.get_microphone(id=device, include_loopback=True)
        with mic.recorder(samplerate=fs, channels=2) as rec:
            data = rec.record(numframes=int(seconds * fs))
        arr = np.asarray(data, dtype=np.float64)
        rms = float(np.sqrt(np.mean(arr ** 2))) if arr.size else 0.0
        return 20 * np.log10(rms + 1e-12)
    except Exception:
        return -999.0


def find_active(seconds: float = 1.0, fs: int = 48000,
                candidates=CANDIDATES) -> tuple[str | None, dict]:
    """Какое устройство сейчас реально отдаёт звук (дБ по каждому)."""
    levels = {}
    best, best_db = None, -60.0
    for dev in candidates:
        db = _probe(dev, seconds, fs)
        levels[dev] = round(db, 1)
        if db > best_db:
            best, best_db = dev, db
    return best, levels


def capture_auto(seconds: float, fs: int = 48000, play=None,
                 settle: float = 0.0) -> dict:
    """Записать с живого устройства.

    play — функция, запускающая воспроизведение (нужна, пока идёт проба).
    settle — пауза между пробой и записью.
    """
    import track_capture as T

    dev, levels = find_active(1.0, fs)
    if dev is None:
        return {"device": None, "levels": levels, "audio": None,
                "error": "ни одно устройство не даёт сигнала"}
    if settle:
        time.sleep(settle)
    audio = T.capture(seconds, fs, device=dev)
    return {"device": dev, "levels": levels, "audio": np.asarray(audio)}
