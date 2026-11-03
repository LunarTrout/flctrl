"""sandbox_eq.py — подбираем EQ на микс в песочнице, чтобы полосы сошлись с референсами.

Не трогает проект: работает с суммой шин (mix_buses.wav). Строит 5 полос (RBJ biquad),
подбирает гейны координатным спуском, печатает результат и пишет превью.

    python sandbox_eq.py
"""
from __future__ import annotations

import glob
import json
import os

import numpy as np
import soundfile as sf

P = r"C:\Users\epicc\Desktop\PROJECTS\vers1"
MIX = os.path.join(P, "_sandbox", "mix_buses.wav")
REFS = os.path.join(P, "_refs")
OUT = os.path.join(P, "_sandbox")
FC = np.array([31.5, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 12500])
SR = 44100


def bands_of(x: np.ndarray, sr: int = SR) -> np.ndarray:
    m = x.mean(axis=1)
    w = np.hanning(m.size)
    sp = np.abs(np.fft.rfft(m * w)) ** 2
    f = np.fft.rfftfreq(m.size, 1 / sr)
    return np.array([10 * np.log10(max(sp[(f >= c / 2 ** (1 / 6)) & (f < c * 2 ** (1 / 6))].mean(), 1e-20))
                     for c in FC])


def biquad(kind: str, fc: float, gain_db: float, q: float = 0.707, sr: int = SR):
    A = 10 ** (gain_db / 40.0)
    w0 = 2 * np.pi * fc / sr
    alpha = np.sin(w0) / (2 * q)
    if kind == "peak":
        b0, b1, b2 = 1 + alpha * A, -2 * np.cos(w0), 1 - alpha * A
        a0, a1, a2 = 1 + alpha / A, -2 * np.cos(w0), 1 - alpha / A
    elif kind == "high_shelf":
        cw = np.cos(w0)
        b0 = A * ((A + 1) + (A - 1) * cw + 2 * np.sqrt(A) * alpha)
        b1 = -2 * A * ((A - 1) + (A + 1) * cw)
        b2 = A * ((A + 1) + (A - 1) * cw - 2 * np.sqrt(A) * alpha)
        a0 = (A + 1) - (A - 1) * cw + 2 * np.sqrt(A) * alpha
        a1 = 2 * ((A - 1) - (A + 1) * cw)
        a2 = (A + 1) - (A - 1) * cw - 2 * np.sqrt(A) * alpha
    else:  # low_shelf
        cw = np.cos(w0)
        b0 = A * ((A + 1) - (A - 1) * cw + 2 * np.sqrt(A) * alpha)
        b1 = 2 * A * ((A - 1) - (A + 1) * cw)
        b2 = A * ((A + 1) - (A - 1) * cw - 2 * np.sqrt(A) * alpha)
        a0 = (A + 1) + (A - 1) * cw + 2 * np.sqrt(A) * alpha
        a1 = -2 * ((A - 1) + (A + 1) * cw)
        a2 = (A + 1) + (A - 1) * cw - 2 * np.sqrt(A) * alpha
    return np.array([b0, b1, b2]) / a0, np.array([1.0, a1 / a0, a2 / a0])


def apply_eq(x: np.ndarray, spec, sr: int = SR) -> np.ndarray:
    from scipy.signal import lfilter
    y = x.astype(np.float64).copy()
    for kind, fc, gain, q in spec:
        if abs(gain) < 0.05:
            continue
        b, a = biquad(kind, fc, gain, q, sr)
        y = lfilter(b, a, y, axis=0)
    return y


BANDS = [
    ("low_shelf", 45.0, 0.70),
    ("peak", 260.0, 0.90),
    ("peak", 520.0, 0.80),
    ("high_shelf", 1400.0, 0.70),
    ("peak", 3200.0, 0.80),
    ("high_shelf", 9000.0, 0.70),
]


def main() -> int:
    x, sr = sf.read(MIX, always_2d=True)
    x = x[: int(30 * sr)]
    tgt = np.mean([bands_of(sf.read(p, always_2d=True)[0][: int(30 * sr)])
                   for p in sorted(glob.glob(os.path.join(REFS, "*.wav")))], axis=0)
    base = bands_of(x)
    print("Δ до:", np.round(base - tgt, 1).tolist(), flush=True)

    gains = {i: 0.0 for i in range(len(BANDS))}

    def curve(g):
        return [(k, fc, g[i], q) for i, (k, fc, q) in enumerate(BANDS)]

    def cost(g):
        y = apply_eq(x, curve(g))
        return float(np.mean((bands_of(y) - tgt) ** 2))

    grid = [-9, -6, -4, -2, 0, 2, 4, 6, 9]
    best = cost(gains)
    for _ in range(4):
        for i in range(len(BANDS)):
            cur = gains[i]
            for v in grid:
                trial = dict(gains)
                trial[i] = v
                c = cost(trial)
                if c < best - 1e-9:
                    best, cur = c, v
            gains[i] = cur

    spec = curve(gains)
    y = apply_eq(x, spec)
    d = bands_of(y) - tgt
    print("EQ:", [(k, fc, round(gains[i], 1), q) for i, (k, fc, q) in enumerate(BANDS)], flush=True)
    print("Δ после:", np.round(d, 1).tolist(), flush=True)
    print("средняя |Δ| %.2f, макс %.2f" % (np.abs(d).mean(), np.abs(d).max()), flush=True)

    os.makedirs(OUT, exist_ok=True)
    peak = float(np.abs(y).max()) or 1.0
    sf.write(os.path.join(OUT, "sandbox_eq.wav"), (y / peak * 0.89).astype(np.float32), 44100)
    json.dump({"eq": [{"kind": k, "fc": fc, "gain_db": round(gains[i], 1), "q": q}
                      for i, (k, fc, q) in enumerate(BANDS)],
               "delta_before": (base - tgt).tolist(), "delta_after": d.tolist()},
              open(os.path.join(OUT, "sandbox_eq.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("превью:", os.path.join(OUT, "sandbox_eq.wav"), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
