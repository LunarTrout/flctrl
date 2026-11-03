"""cache.py — кэш измерений, чтобы повторный отчёт не пересчитывал референсы.

Зачем: замер одного трека — секунды, корпуса из шести референсов — минуты.
Пока числа не изменились, пересчитывать их бессмысленно, а ждать каждый раз
неприятно. Ключ включает путь, время правки, размер и окно: правка файла или
смена окна автоматически сбрасывает запись.

Отключается переменной окружения FLCTRL_NO_CACHE=1.

    import cache
    res = cache.memo("audit", [path, a, b], lambda: audit.measure(path, a, b))
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_DIR = os.path.join(os.path.dirname(HERE), "_cache", "hear")


def _dir() -> str:
    d = os.environ.get("FLCTRL_CACHE", DEFAULT_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def enabled() -> bool:
    return os.environ.get("FLCTRL_NO_CACHE", "") not in ("1", "true", "yes")


def key(scope: str, parts: list) -> str:
    h = hashlib.sha256()
    h.update(scope.encode("utf-8"))
    for p in parts:
        if isinstance(p, str) and os.path.exists(p):
            st = os.stat(p)
            h.update(("%s|%d|%.0f" % (os.path.basename(p), st.st_size, st.st_mtime)).encode("utf-8"))
        else:
            h.update(json.dumps(p, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()[:32]


def get(scope: str, parts: list):
    if not enabled():
        return None
    path = os.path.join(_dir(), "%s_%s.json" % (scope, key(scope, parts)))
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def put(scope: str, parts: list, value) -> None:
    if not enabled():
        return
    path = os.path.join(_dir(), "%s_%s.json" % (scope, key(scope, parts)))
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        # кэш — удобство, а не условие работы: не смогли записать — считаем заново
        pass


def memo(scope: str, parts: list, fn):
    hit = get(scope, parts)
    if hit is not None:
        return hit
    val = fn()
    try:
        put(scope, parts, val)
    except TypeError:
        pass          # не сериализуется — просто не кэшируем
    return val


def clear(scope: str | None = None) -> dict:
    d = _dir()
    removed = 0
    if scope:
        for f in os.listdir(d):
            if f.startswith(scope + "_"):
                os.unlink(os.path.join(d, f))
                removed += 1
    else:
        shutil.rmtree(d, ignore_errors=True)
        os.makedirs(d, exist_ok=True)
    return {"removed": removed, "dir": d}


def stats() -> dict:
    d = _dir()
    files = [f for f in os.listdir(d) if f.endswith(".json")]
    size = sum(os.path.getsize(os.path.join(d, f)) for f in files)
    return {"files": len(files), "bytes": size, "dir": d, "enabled": enabled()}
