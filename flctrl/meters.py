"""meters.py — считывание каналов микшера через собственные метры FL.

Почему так: буферы дорожек опросом читаются неровно (FL переиспользует пулы
буферов, блоки идут не по порядку), а хук в аудио-колбэке вешает RPC. Зато FL
сам считает пики каждой дорожки на каждом блоке и отдаёт их через
`mixer.getTrackPeaks(i, ch)`.

Все дорожки забираются ОДНИМ выражением за ~8 мс (а не 64 вызова по 2 мс), то
есть опрос 30–60 Гц по всему микшеру — реальность.

    python -m flctrl.meters --seconds 3 --rate 20
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import numpy as np


def _bridge(expr, attempts: int = 2):
    lib = os.path.dirname(os.path.abspath(__file__))
    if lib not in sys.path:
        sys.path.insert(0, lib)
    import fl_lib
    return fl_lib._State._live(expr, attempts=attempts)


def _num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _parse_list(text: str) -> list[float]:
    """Ответ моста — строка вида '[0.1, 0.2, ...]'."""
    body = str(text).strip().strip("[]")
    if not body:
        return []
    out = []
    for part in body.split(","):
        out.append(_num(part.strip(), 0.0))
    return out


def track_count() -> int:
    return int(_num(_bridge("mixer.getTrackCount()"), 0)) or 0


def channels() -> int:
    """Сколько каналов отдаёт метр (1 или 2)."""
    probe = _bridge("mixer.getTrackPeaks(0, 1)")
    return 2 if probe not in (None, "", "None") else 1


def poll(tracks: list[int] | None = None, seconds: float = 5.0,
         rate_hz: float = 20.0) -> dict:
    """Опрос пиков дорожек. Возвращает ряды по времени и сводку."""
    total = track_count()
    if tracks is None:
        tracks = list(range(total))
    tracks = [int(t) for t in tracks if int(t) < total]
    chans = channels()
    period = 1.0 / max(rate_hz, 1.0)

    # одним выражением нельзя безгранично: мост не переваривает сотни вызовов.
    # Режем на пачки по 16 дорожек (16*ch вызовов) — 3 обхода на круг для 48.
    step = max(1, 16 // max(chans, 1))
    groups = [tracks[i:i + step] for i in range(0, len(tracks), step)]
    exprs = []
    for grp in groups:
        items = []
        for t in grp:
            for ch in range(chans):
                items.append("mixer.getTrackPeaks(%d,%d)" % (t, ch))
        exprs.append("[" + ",".join(items) + "]")
    names_expr = "[" + ",".join("mixer.getTrackName(%d)" % t for t in tracks) + "]"

    try:
        names = [s.strip().strip("'\"") for s in str(_bridge(names_expr)).strip("[]").split(",")]
    except Exception:  # noqa: BLE001
        names = ["" for _ in tracks]

    playing = _num(_bridge("transport.isPlaying()")) > 0
    t0 = time.time()
    series: list[list[float]] = []
    while time.time() - t0 < seconds:
        vals = []
        for e in exprs:
            vals.extend(_parse_list(_bridge(e)))
        if len(vals) == len(tracks) * chans:
            series.append(vals)
        time.sleep(period)
    if not series:
        return {"error": "нет ответа от метра", "tracks": len(tracks), "playing": playing}

    arr = np.asarray(series, dtype=float)                # (T, n*ch)
    arr = arr.reshape(len(arr), len(tracks), chans)
    peak = arr.max(axis=2)                               # (T, n)
    db = 20 * np.log10(np.maximum(peak, 1e-9))
    out_tracks = {}
    for i, t in enumerate(tracks):
        col = peak[:, i]
        out_tracks[t] = {
            "name": names[i] if i < len(names) else "",
            "peak": round(float(col.max()), 4),
            "peak_db": round(float(db[:, i].max()), 1),
            "mean_db": round(float(db[:, i].mean()), 1),
            "silent": bool(col.max() < 1e-6),
            "series_db": [round(float(x), 1) for x in db[:, i]],
        }
    live = {t: v for t, v in out_tracks.items() if not v["silent"]}
    return {"playing_at_start": playing, "rounds": len(series),
            "seconds": round(time.time() - t0, 2), "channels": chans,
            "tracks": out_tracks, "live": len(live),
            "loudest": sorted(live.items(), key=lambda kv: -kv[1]["peak"])[:8]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=3.0)
    ap.add_argument("--rate", type=float, default=20.0)
    ap.add_argument("--tracks", default="")
    a = ap.parse_args(argv)

    tracks = None
    if a.tracks:
        tracks = []
        for part in a.tracks.split(","):
            if "-" in part:
                lo, hi = part.split("-")
                tracks.extend(range(int(lo), int(hi) + 1))
            elif part.strip():
                tracks.append(int(part))

    res = poll(tracks, a.seconds, a.rate)
    if res.get("error"):
        print("ошибка:", res["error"])
        return 3
    print("кругов %d за %.1f с (%.0f Гц) | каналов %d | со звуком %d из %d | играло %s"
          % (res["rounds"], res["seconds"], res["rounds"] / max(res["seconds"], 1e-9),
             res["channels"], res["live"], len(res["tracks"]), res["playing_at_start"]))
    print("\nдорожка | имя | пик | дБ")
    for t, v in res["loudest"]:
        print("%7d | %-20s | %.4f | %5.1f" % (t, v["name"][:20], v["peak"], v["peak_db"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
