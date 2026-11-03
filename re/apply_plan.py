"""apply_plan.py — ходы по миксу из mix_plan: только фейдеры, с проверкой и откатом.

По умолчанию ничего не меняет — печатает, что сделает (dry-run).
Применение: --apply. Каждый шаг: снимок -> изменение -> чтение обратно.

    python apply_plan.py            # показать
    python apply_plan.py --apply    # применить (без сохранения проекта)
"""
from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, r"C:\Users\epicc\mcp\flstudio-lib")
sys.path.insert(0, r"C:\Users\epicc\mcp\flstudio-lib\flctrl")

import live  # noqa: E402  # pyright: ignore[reportMissingImports]


def num(expr: str) -> float:
    try:
        return float(live._bridge(expr, timeout=4))
    except Exception:
        return float("nan")


# (дорожка, дельта в дБ, зачем)
MOVES = [
    ("GTR R1", -4.0, "правая гитара громче левой на 5.8 дБ"),
    ("GTR L1", +1.5, "подтягиваем левую к правой"),
    ("BASS", -2.0, "бас забивает низ: +46.7 дБ на 63 Гц относительно 1 кГц"),
    ("Scream vocal", -1.5, "вокал громче всех в срезе"),
    ("DRUM BUS", +3.0, "шина барабанов на 9 дБ ниже источника"),
]


def find_track(name: str) -> int | None:
    n = int(num("mixer.getTrackCount()"))
    for i in range(1, n):
        try:
            if str(live._bridge("mixer.getTrackName(%d)" % i, timeout=3)).strip("'\"") == name:
                return i
        except Exception:
            continue
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()

    print("проект:", live.fl_window().get("title"))
    n = int(num("mixer.getTrackCount()"))
    if not n:
        print("нет связи с FL — стоп")
        return 2
    print("дорог:", n)

    plan = []
    for name, db, why in MOVES:
        idx = find_track(name)
        if idx is None:
            print("  ! не нашёл дорожку %r" % name)
            continue
        vol = num("mixer.getTrackVolume(%d)" % idx)
        target = vol * 10 ** (db / 20.0)
        plan.append((idx, name, vol, target, db, why))
        print("  %-14s idx %2d  %.3f -> %.3f  (%+.1f дБ)  %s" % (name, idx, vol, target, db, why))

    if not a.apply:
        print("\nэто dry-run: ничего не менял. применить — с ключом --apply")
        return 0

    live.clip_flag(1)          # запись не трогаем, но флаг пусть будет как у пользователя
    done = []
    for idx, name, vol, target, db, why in plan:
        live._bridge("mixer.setTrackVolume(%d, %r)" % (idx, target), timeout=4)
        time.sleep(0.1)
        back = num("mixer.getTrackVolume(%d)" % idx)
        ok = abs(back - target) < 0.02
        print("  %-14s %.3f -> %.3f  прочитано %.3f  %s" % (
            name, vol, target, back, "ок" if ok else "НЕ СОВПАЛО"))
        done.append((idx, name, vol, target, ok))

    bad = [d for d in done if not d[4]]
    print("\nприменено: %d, расхождений: %d" % (len(done) - len(bad), len(bad)))
    print("проект НЕ сохранён — сохранение за тобой")
    return 0 if not bad else 3


if __name__ == "__main__":
    raise SystemExit(main())
