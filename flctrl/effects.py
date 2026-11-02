"""effects.py — объективные метрики эффектов, три класса пробников.

Один тест на всё даёт мусор: у линейных эффектов меряют импульсный отклик,
у нелинейных — искажения синуса, у время-зависимых — поведение огибающей.
Здесь три набора, каждый проверен на синтетике с известным ответом.

    python -m flctrl.effects ir  impulse.wav      # RT60, EDT, C50
    python -m flctrl.effects thd sine.wav          # THD, THD+N
    python -m flctrl.effects duck ducked.wav       # глубина и частота дакинга
"""
from __future__ import annotations

import argparse
import json

import numpy as np


def _read(path: str):
    import soundfile as sf

    x, fs = sf.read(path, dtype="float64", always_2d=True)
    return x.mean(axis=1), fs


def _db(v: float) -> float:
    return 10 * np.log10(max(v, 1e-30))


# --------------------------------------------------------------- линейные
def ir_metrics(x: np.ndarray, fs: int) -> dict:
    """RT60/EDT/C50 по импульсному отклику (метод Шрёдера).

    x — импульсный отклик целиком (не музыка). Пик считается прямым звуком.
    """
    x = np.asarray(x, dtype=float)
    if x.ndim > 1:
        x = x.mean(axis=1)
    peak_i = int(np.argmax(np.abs(x)))
    tail = x[peak_i:] ** 2
    if tail.sum() <= 0:
        return {"rt60": 0.0, "edt": 0.0, "c50": 0.0, "note": "тишина"}

    # энергия, накопленная с конца (Шрёдер)
    edc = np.cumsum(tail[::-1])[::-1]
    edc_db = 10 * np.log10(np.maximum(edc / edc[0], 1e-30))
    t = np.arange(len(edc_db)) / fs

    def t_at(level_db: float) -> float:
        idx = np.argmax(edc_db <= level_db)
        return float(t[idx]) if edc_db[idx] <= level_db else float(t[-1])

    def slope_t60(lo: float, hi: float) -> float:
        m = (edc_db <= hi) & (edc_db >= lo)
        if m.sum() < 8:
            return 0.0
        a = np.polyfit(t[m], edc_db[m], 1)[0]
        return float(-60.0 / a) if a < 0 else 0.0

    rt60 = slope_t60(-25, -5)
    edt = slope_t60(-10, 0)

    early = tail[: int(0.05 * fs)].sum()
    late = tail[int(0.05 * fs):].sum()
    c50 = float(10 * np.log10((early + 1e-30) / (late + 1e-30)))
    return {"rt60": round(rt60, 3), "edt": round(edt, 3), "c50": round(c50, 2),
            "peak_ms": round(peak_i / fs * 1000, 2)}


# ------------------------------------------------------------ нелинейные
def thd(x: np.ndarray, fs: int, f0: float | None = None) -> dict:
    """THD и THD+N по синусу. f0 ищется по спектру, если не задан."""
    x = np.asarray(x, dtype=float)
    if x.ndim > 1:
        x = x.mean(axis=1)
    n = len(x)
    win = np.hanning(n)
    spec = np.abs(np.fft.rfft(x * win)) ** 2
    freqs = np.fft.rfftfreq(n, 1 / fs)
    if f0 is None:
        f0 = float(freqs[np.argmax(spec[1:]) + 1])
    # основная и гармоники
    def power_at(f, width_bins=3):
        i = int(np.argmin(np.abs(freqs - f)))
        lo, hi = max(0, i - width_bins), min(len(spec), i + width_bins + 1)
        return float(spec[lo:hi].sum())

    p1 = power_at(f0)
    if p1 <= 0:
        return {"f0": f0, "thd_pct": 0.0, "thd_n_pct": 0.0}
    harm = sum(power_at(f0 * k) for k in range(2, 11))
    total = float(spec.sum())
    noise = max(total - p1 - harm, 0.0)
    return {"f0": round(f0, 1),
            "thd_pct": round(100 * np.sqrt(harm / p1), 3),
            "thd_n_pct": round(100 * np.sqrt((harm + noise) / p1), 3)}


# -------------------------------------------------------- время-зависимые
def ducking(x: np.ndarray, fs: int, win_s: float = 0.01) -> dict:
    """Глубина и частота дакинга по огибающей.

    Ищем периодические провалы: сглаженная огибающая, затем автокорреляция её
    колебаний. Возвращаем глубину (дБ) и частоту (Гц).
    """
    x = np.asarray(x, dtype=float)
    if x.ndim > 1:
        x = x.mean(axis=1)
    hop = max(1, int(win_s * fs))
    frames = len(x) // hop
    if frames < 8:
        return {"depth_db": 0.0, "rate_hz": 0.0}
    env = np.sqrt(np.mean(x[: frames * hop].reshape(frames, hop) ** 2, axis=1))
    env = np.maximum(env, 1e-9)
    e_db = 20 * np.log10(env)
    e_db = e_db - np.mean(e_db)
    depth = float(np.percentile(e_db, 95) - np.percentile(e_db, 5))

    ac = np.correlate(e_db, e_db, mode="full")[len(e_db) - 1:]
    ac = ac / (ac[0] + 1e-12)
    min_lag = max(1, int(0.15 / win_s))     # не чаще ~7 Гц
    max_lag = min(len(ac) - 1, int(2.0 / win_s))
    rate = 0.0
    if max_lag > min_lag:
        lag = int(np.argmax(ac[min_lag:max_lag]) + min_lag)
        if ac[lag] > 0.2:
            rate = float(1.0 / (lag * win_s))
    return {"depth_db": round(depth, 2), "rate_hz": round(rate, 2)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Метрики эффектов")
    ap.add_argument("mode", choices=["ir", "thd", "duck"])
    ap.add_argument("path")
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    x, fs = _read(a.path)
    if a.mode == "ir":
        res = ir_metrics(x, fs)
    elif a.mode == "thd":
        res = thd(x, fs)
    else:
        res = ducking(x, fs)
    print(json.dumps(res, ensure_ascii=False, indent=1))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
