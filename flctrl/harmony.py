"""harmony.py — гармония как измеримые числа, а не как мнение.

Что здесь считается:

  chroma      12 долей (C, C#, …) — сколько энергии в каждой ступени
  key         самые вероятные тональности по профилям Крамхансль-Кесслера
  tuning_cents  насколько строй ушёл от равномерной темперации (в центах)
  harmonicity доля энергии в гармониках найденных основных тонов против
              энергии между ними: 1.0 — чистый тон, 0.2 — шум/каша
  inharm_cents среднее отклонение второй гармоники от ровно октавы

Смысл: два инструмента могут занимать одну полосу и не мешать, если они
гармонично друг другу; и наоборот, «стена» из расстроенных гитар читается
как грязь. Здесь это видно числами, а решение принимает вызывающий.
"""
from __future__ import annotations

import numpy as np

N_FFT = 8192
HOP = 2048
# профили Крамхансль-Кесслера
MAJOR = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88])
MINOR = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17])
NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")


def _load(path: str, a: float | None = None, b: float | None = None):
    import soundfile as sf

    info = sf.info(path)
    start = int((a or 0.0) * info.samplerate)
    frames = -1 if b is None else int((b - (a or 0.0)) * info.samplerate)
    x, fs = sf.read(path, dtype="float64", start=start, frames=frames, always_2d=True)
    if x.shape[1] > 1:
        x = x.mean(axis=1)
    else:
        x = x[:, 0]
    return x, int(fs)


def _spectra(x: np.ndarray, fs: int):
    if len(x) < N_FFT:
        x = np.pad(x, (0, N_FFT - len(x)))
    win = np.hanning(N_FFT)
    frames = 1 + (len(x) - N_FFT) // HOP
    mags = np.empty((frames, N_FFT // 2 + 1))
    for i in range(frames):
        seg = x[i * HOP:i * HOP + N_FFT] * win
        mags[i] = np.abs(np.fft.rfft(seg))
    freqs = np.fft.rfftfreq(N_FFT, 1 / fs)
    return mags, freqs


def _tuning_cents(mags: np.ndarray, freqs: np.ndarray, fmin: float = 55.0,
                  fmax: float = 2000.0) -> float:
    """Сдвиг строя: на сколько центов пики ушли от сетки равномерной темперации."""
    band = (freqs >= fmin) & (freqs <= fmax)
    spec = mags[:, band].mean(axis=0)
    f = freqs[band]
    if not len(spec) or spec.max() <= 0:
        return 0.0
    ref = 440.0
    midi = 69 + 12 * np.log2(np.maximum(f, 1e-9) / ref)
    # ищем сдвиг, при котором энергия ближе всего к целым полутонам
    best, best_off = -1.0, 0.0
    for off in np.arange(-50.0, 50.1, 1.0):
        r = (midi * 100.0 + off) % 100.0
        d = np.minimum(r, 100.0 - r) / 50.0        # 0 ровно на полутоне
        w = spec * np.exp(-4.0 * d ** 2)
        val = float(w.sum())
        if val > best:
            best, best_off = val, float(off)
    # знак: положительное — инструменты выше строя. Сетку мы двигали навстречу,
    # поэтому итог — со знаком минус.
    return round(-best_off, 1)


def _chroma(mags: np.ndarray, freqs: np.ndarray, cents: float,
            fmin: float = 55.0, fmax: float = 4000.0) -> np.ndarray:
    band = (freqs >= fmin) & (freqs <= fmax)
    spec = mags[:, band].mean(axis=0) ** 0.5      # сжатие: верх не должен доминировать
    f = freqs[band]
    if not len(spec) or spec.max() <= 0:
        return np.zeros(12)
    midi = 69 + 12 * np.log2(np.maximum(f, 1e-9) / 440.0) + cents / 100.0
    pc = np.round(midi).astype(int) % 12
    out = np.zeros(12)
    for i in range(12):
        out[i] = float(spec[pc == i].sum())
    n = np.linalg.norm(out)
    return out / n if n else out


def _key(chroma: np.ndarray) -> list:
    """Кандидаты в тональности: корреляция с профилями мажора и минора."""
    res = []
    for i in range(12):
        rotated = np.roll(chroma, -i)
        res.append((float(np.corrcoef(rotated, MAJOR)[0, 1]), NAMES[i], "мажор"))
        res.append((float(np.corrcoef(rotated, MINOR)[0, 1]), NAMES[i], "минор"))
    res.sort(reverse=True)
    return [{"key": "%s %s" % (n, m), "r": round(r, 3)} for r, n, m in res[:3]]


def _harmonics(mags: np.ndarray, freqs: np.ndarray, cents: float,
               fmin: float = 60.0, fmax: float = 1200.0, top: int = 8) -> dict:
    """Гармоничность: энергия в гармониках против энергии между ними."""
    spec = mags.mean(axis=0)
    band = (freqs >= fmin) & (freqs <= fmax)
    f, s = freqs[band], spec[band]
    if not len(s) or s.max() <= 0:
        return {"harmonicity": None, "inharm_cents": None, "f0": None}
    # основные тоны: локальные максимумы
    idx = [i for i in range(1, len(s) - 1) if s[i] > s[i - 1] and s[i] > s[i + 1]]
    idx.sort(key=lambda i: -s[i])
    peaks = idx[:top]
    if not peaks:
        return {"harmonicity": None, "inharm_cents": None, "f0": None}

    def at(hz: float) -> float:
        j = int(np.argmin(np.abs(f - hz)))
        return float(s[max(0, j - 1):j + 2].max())

    # считаем по самому сильному основному тону: гармоники против середины
    # между соседними гармониками — так шум даёт 0.5, тон уходит к 1.0
    f0 = float(f[peaks[0]])
    tune = 2 ** (cents / 1200.0)
    in_h, between, inharm = 0.0, 0.0, []
    for h in range(1, 7):
        hz = f0 * h * tune
        if hz < f[0] or hz * 1.5 > f[-1]:
            continue
        in_h += at(hz)
        between += at(hz * 1.5)          # ровно между гармониками h и h+1
    h2 = f0 * 2 * tune
    if h2 <= f[-1]:
        j2 = int(np.argmin(np.abs(f - h2)))
        lo, hi = max(0, j2 - 40), min(len(f), j2 + 40)
        if hi > lo:
            k = lo + int(np.argmax(s[lo:hi]))
            inharm.append(1200 * np.log2(max(f[k], 1e-9) / h2))
    harm = in_h / (in_h + between) if (in_h + between) > 0 else None
    return {"harmonicity": round(float(harm), 3) if harm is not None else None,
            "inharm_cents": round(float(np.median(inharm)), 1) if inharm else None,
            "f0": round(f0, 1)}


def analyse(path: str, a: float | None = None, b: float | None = None,
            max_seconds: float = 45.0) -> dict:
    x, fs = _load(path, a, b)
    if max_seconds and len(x) > fs * max_seconds:
        x = x[:int(fs * max_seconds)]
    if not len(x) or np.max(np.abs(x)) < 1e-9:
        return {"ok": False, "error": "тишина"}
    mags, freqs = _spectra(x, fs)
    cents = _tuning_cents(mags, freqs)
    chroma = _chroma(mags, freqs, cents)
    out = {"ok": True, "seconds": round(len(x) / fs, 2),
           "tuning_cents": cents,
           "chroma": [round(float(c), 4) for c in chroma],
           "top_pitch_classes": [NAMES[i] for i in np.argsort(chroma)[::-1][:4]],
           "key": _key(chroma)}
    out.update(_harmonics(mags, freqs, cents))
    return out


def compare(mine: dict, refs: list[dict]) -> dict:
    """Сравнение с профилями референсов: близость хромы, строй, гармоничность."""
    if not mine.get("ok"):
        return {"ok": False, "error": mine.get("error", "нет замера")}
    refs = [r for r in refs if r.get("ok")]
    m = np.array(mine["chroma"])
    rows = []
    for r in refs:
        v = np.array(r["chroma"])
        cos = float(np.dot(m, v) / (np.linalg.norm(m) * np.linalg.norm(v) + 1e-12))
        rows.append({"cos": round(cos, 3),
                     "key": (r.get("key") or [{}])[0].get("key"),
                     "tuning_cents": r.get("tuning_cents"),
                     "harmonicity": r.get("harmonicity")})
    if not rows:
        return {"ok": True, "rows": [], "verdict": []}

    cos_med = float(np.median([r["cos"] for r in rows]))
    harm_vals = [r["harmonicity"] for r in rows if r["harmonicity"] is not None]
    harm_med = float(np.median(harm_vals)) if harm_vals else None
    verdict = []
    if cos_med < 0.85:
        verdict.append("хрома далека от референсов (%.2f): другой лад/регистр или много шума" % cos_med)
    if mine.get("harmonicity") is not None and harm_med is not None:
        d = mine["harmonicity"] - harm_med
        if d < -0.10:
            verdict.append("гармоничность ниже референсов на %.2f: звук ближе к шуму, чем к тону" % -d)
        elif d > 0.10:
            verdict.append("гармоничность выше референсов на %.2f: возможно, не хватает плотности" % d)
    tun = [abs(r["tuning_cents"] or 0) for r in rows]
    if tun and abs(mine.get("tuning_cents") or 0) > max(6.0, float(np.median(tun)) + 5):
        verdict.append("строй ушёл на %+.0f центов — проверить подстройку инструментов"
                       % (mine.get("tuning_cents") or 0))
    return {"ok": True, "rows": rows, "cos_median": round(cos_med, 3),
            "harmonicity_median": None if harm_med is None else round(harm_med, 3),
            "verdict": verdict}
