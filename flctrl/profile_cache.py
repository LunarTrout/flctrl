"""profile_cache.py -- один расчёт на файл, дальше всё мгновенно.

Первый проход по файлу считается ~10-20 с и сохраняется в .npz рядом с кэшем:
  N, фоны, резкость, N′(z) 240×кадры, уровень по полосам, ширина по полосам,
  модуляция по полосам, время.

Любой последующий запрос (полосы, секции, сравнение, брифинг) читает готовый
массив за десятые доли секунды.

  ../.venv313/Scripts/python.exe profile_cache.py build mix.wav
  ../.venv313/Scripts/python.exe profile_cache.py info mix.wav
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import listen_layer as L  # noqa: E402
import bandprofile as B   # noqa: E402

CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cache", "profile")


def _paths(path: str):
    os.makedirs(CACHE, exist_ok=True)
    mt = os.path.getmtime(path) if os.path.exists(path) else 0
    tag = hashlib.sha1(("%s|%s" % (os.path.abspath(path), mt)).encode()).hexdigest()[:16]
    base = os.path.join(CACHE, tag)
    return base + ".npz", base + ".json"


def build(path: str, force: bool = False) -> dict:
    """Считает полный профиль и кладёт на диск. Возвращает словарь массивов."""
    npz, meta = _paths(path)
    if os.path.exists(npz) and not force:
        d = np.load(npz)
        return {k: d[k] for k in d.files}
    t0 = time.time()
    c = L.curve(path, None)
    ax = B.band_axes(path)
    out = {
        "t": c["t"], "N": c["N"], "phon": c["phon"], "sharp": c["sharp"],
        "spec": c["spec"], "level_db": c["level_db"],
        "band_level": ax["level"], "band_width": ax["width"], "band_mod": ax["mod"],
    }
    np.savez_compressed(npz, **out)
    with open(meta, "w", encoding="utf-8") as f:
        json.dump({"file": os.path.abspath(path), "built_s": round(time.time() - t0, 1),
                   "frames": int(len(c["N"]))}, f)
    return out


def info(path: str) -> dict:
    npz, meta = _paths(path)
    if not os.path.exists(meta):
        return {"built": False}
    with open(meta, encoding="utf-8") as f:
        d = json.load(f)
    d["built"] = True
    d["size_kb"] = round(os.path.getsize(npz) / 1024)
    return d


def main() -> int:
    if len(sys.argv) < 3:
        print("build|info <файл>")
        return 2
    cmd, path = sys.argv[1], sys.argv[2]
    if cmd == "info":
        print(json.dumps(info(path), ensure_ascii=False, indent=2))
        return 0
    t0 = time.time()
    d = build(path, force=True)
    print("считано за %.1f с: кадров %d, полос %d, кэш %.0f КБ"
          % (time.time() - t0, len(d["N"]), d["spec"].shape[1],
             os.path.getsize(_paths(path)[0]) / 1024))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
