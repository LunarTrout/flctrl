"""bandcompare.py -- сравнение нашего микса с референсами по 240 полосам Барка.

Не сводка и не «полосы в процентах», а прямое сравнение удельной громкости
N′(z) в каждой из 240 полос: где именно мы громче или тише референса.

  ../.venv313/Scripts/python.exe bandcompare.py mix.wav ref1.mp3 ref2.mp3 [--top 20]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import listen_layer as L  # noqa: E402
import numpy as np        # noqa: E402

BARK = np.arange(240) * 0.1 + 0.05


def bark_to_hz(z: np.ndarray) -> np.ndarray:
    """Обратное преобразование Барка в Гц (Zwicker)."""
    return 600.0 * np.sinh(z / 6.0)


def profile(path: str) -> np.ndarray:
    """Медианный профиль N′(z) по кадрам, где реально есть звук."""
    c = L.curve(path, None)
    spec = c["spec"]
    active = c["N"] >= 1.0
    if active.sum() < 10:
        active = np.ones(len(c["N"]), bool)
    return np.median(spec[active], axis=0)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    top = 20
    for a in sys.argv[1:]:
        if a.startswith("--top"):
            top = int(a.split("=")[-1]) if "=" in a else 20
    if len(args) < 2:
        print("нужен микс и хотя бы один референс")
        return 2

    mine = profile(args[0])
    refs = np.vstack([profile(r) for r in args[1:]])
    ref_med = np.median(refs, axis=0)
    ref_lo = refs.min(axis=0)
    ref_hi = refs.max(axis=0)

    diff = mine - ref_med
    scale = np.maximum(ref_med, 1e-9)
    rel = 100.0 * diff / scale

    order = np.argsort(-np.abs(rel))
    print("микс: %s" % os.path.basename(args[0]))
    print("референсы: %s" % ", ".join(os.path.basename(r)[:18] for r in args[1:]))
    print()
    print("%6s %9s %10s %10s %10s %8s" % ("Барк", "Гц", "наш", "медиана реф", "разница", "%"))
    print("-" * 60)
    shown = 0
    for i in order:
        if ref_med[i] <= 0 and mine[i] <= 0:
            continue
        inside = ref_lo[i] <= mine[i] <= ref_hi[i]
        mark = "" if inside else "  <- вне коридора"
        print("%6.1f %9.0f %10.1f %10.1f %+10.1f %+8.0f%s" % (
            BARK[i], bark_to_hz(BARK[i]), mine[i], ref_med[i], diff[i], rel[i], mark))
        shown += 1
        if shown >= top:
            break
    print()
    outside = int(np.sum((mine < ref_lo) | (mine > ref_hi)))
    print("вне коридора референсов: %d из 240 полос" % outside)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
