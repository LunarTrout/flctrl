"""realtime.py — движение параметров в реальном времени, без автоматизации FL.

Зачем: API автоматизации (`mixer.automateEvent`) требует внутренних id событий,
которые нигде не описаны, а ошибки на них глухие. Зато писать параметр вживую
можно (проверено: 2–3 мс на вызов), и если писать его 20 раз в секунду по
кривой, панорама реально едет — так же, как если бы её нарисовали мышью.

Ограничения честные:
* движение живёт, пока жив процесс, который пишет; после остановки остаётся
  последнее значение;
* при 20 Гц шаг между точками 50 мс: для панорамы этого достаточно, для
  резких скачков — нет;
* запись идёт через мост, поэтому при занятой FL возможны пропуски — они
  считаются и возвращаются в статистике.

    python -m flctrl.realtime --track 12 --from -0.6 --to 0.6 --seconds 8
"""
from __future__ import annotations

import argparse
import threading
import time

import numpy as np


class Mover:
    """Поток, который ведёт параметр по кривой и считает свои ошибки."""

    def __init__(self, write, rate_hz: float = 20.0):
        self.write = write
        self.period = 1.0 / max(rate_hz, 0.5)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.stats = {"writes": 0, "errors": 0, "last_error": "", "max_lag_ms": 0.0}

    def run(self, curve, duration_s: float, verify_every: int = 10) -> dict:
        """Кривая — функция времени, возвращающая значение параметра."""
        self._stop.clear()
        self.stats.update({"writes": 0, "errors": 0, "last_error": "", "max_lag_ms": 0.0})
        t0 = time.perf_counter()

        def loop():
            planned = t0
            while not self._stop.is_set():
                now = time.perf_counter()
                elapsed = now - t0
                if elapsed >= duration_s:
                    break
                try:
                    self.write(float(curve(elapsed)))
                    self.stats["writes"] += 1
                except Exception as exc:
                    self.stats["errors"] += 1
                    self.stats["last_error"] = str(exc)[:120]
                planned += self.period
                lag = (planned - time.perf_counter()) * 1000
                self.stats["max_lag_ms"] = max(self.stats["max_lag_ms"], -lag if lag < 0 else 0.0)
                if lag > 0:
                    self._stop.wait(lag / 1000.0)

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        return {"started": True, "duration_s": duration_s, "rate_hz": 1.0 / self.period}

    def stop(self, timeout_s: float = 5.0) -> dict:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout_s)
        return dict(self.stats)


def lerp_curve(points: list[tuple[float, float]]):
    """Кривая по точкам [(время, значение)]: линейно между, константа по краям."""
    pts = sorted(points)

    def curve(t: float) -> float:
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for (t0, v0), (t1, v1) in zip(pts, pts[1:]):
            if t0 <= t <= t1:
                if t1 - t0 < 1e-9:
                    return v1
                k = (t - t0) / (t1 - t0)
                return v0 + (v1 - v0) * k
        return pts[-1][1]

    return curve


def move_pan(fl, track: int, points: list[tuple[float, float]],
             duration_s: float, rate_hz: float = 20.0) -> dict:
    """Провести панораму дорожки по точкам (время в секундах)."""
    mover = Mover(lambda v: fl.act.pan_live(track, max(-1.0, min(1.0, v))), rate_hz)
    curve = lerp_curve(points)
    mover.run(curve, duration_s)
    return {"mover": mover, "curve": curve}


def move_value(fl, track: int, slot: int, index: int,
               points: list[tuple[float, float]], duration_s: float,
               rate_hz: float = 20.0) -> dict:
    """Провести параметр плагина по точкам."""
    mover = Mover(lambda v: fl.act.param_live(track, slot, index,
                                              max(0.0, min(1.0, v)), verify=False),
                  rate_hz)
    curve = lerp_curve(points)
    mover.run(curve, duration_s)
    return {"mover": mover, "curve": curve}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Движение панорамы в реальном времени")
    ap.add_argument("--track", type=int, required=True)
    ap.add_argument("--from", dest="start", type=float, default=-0.6)
    ap.add_argument("--to", dest="end", type=float, default=0.6)
    ap.add_argument("--seconds", type=float, default=8.0)
    ap.add_argument("--rate", type=float, default=20.0)
    a = ap.parse_args(argv)

    import fl_lib as fl
    res = move_pan(fl, a.track, [(0.0, a.start), (a.seconds, a.end)],
                   a.seconds, a.rate)
    time.sleep(a.seconds)
    stats = res["mover"].stop()
    print("записей:", stats["writes"], "ошибок:", stats["errors"],
          "худшее опоздание: %.1f мс" % stats["max_lag_ms"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
