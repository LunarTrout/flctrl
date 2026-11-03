"""Сверка инкрементальных метрик с файловыми на одном и том же звуке.

Правило: потоковая версия обязана дать те же числа, что файловая, иначе она не
измерение, а догадка. Тест офлайновый: FL не нужна.
"""
from __future__ import annotations

import os
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import incr  # noqa: E402
import perceptual  # noqa: E402

FS = 44100
BLOCK = 256


def signal(seconds: float = 3.0, fs: int = FS) -> np.ndarray:
    rng = np.random.default_rng(7)
    n = int(seconds * fs)
    t = np.arange(n) / fs
    left = 0.30 * np.sin(2 * np.pi * 220 * t) + 0.10 * rng.standard_normal(n)
    right = 0.28 * np.sin(2 * np.pi * 330 * t + 0.4) + 0.10 * rng.standard_normal(n)
    env = np.minimum(1.0, t / 0.05) * np.minimum(1.0, (seconds - t) / 0.2)
    return np.stack([left * env, right * env], axis=1).astype(np.float32)


def feed_all(x: np.ndarray):
    rms, bands, st = incr.RmsMeter(FS), incr.BandMeter(FS), incr.StereoMeter()
    loud = incr.LoudnessMeter(FS)
    for i in range(0, len(x), BLOCK):
        chunk = x[i:i + BLOCK]
        rms.feed(chunk)
        bands.feed(chunk)
        st.feed(chunk)
        loud.feed(chunk)
    return rms.value(), bands.value(), st.value(), loud.value()


def main() -> int:
    x = signal()
    rms_v, band_v, st_v, loud_v = feed_all(x)

    # --- файловые эталоны
    file_rms = float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))
    file_peak = float(np.max(np.abs(x)))
    lv, _t = perceptual.band_levels(x, FS)
    file_bands = np.mean(lv, axis=0)

    tmp = os.path.join(tempfile.gettempdir(), "incr_check.wav")
    import soundfile as sf
    sf.write(tmp, x, FS)
    file_lufs = perceptual.lufs(x.astype(np.float64), FS)

    checks = []
    checks.append(("rms", abs(rms_v["rms"] - file_rms) < 1e-6,
                   "%.6f / %.6f" % (rms_v["rms"], file_rms)))
    checks.append(("peak", abs(rms_v["peak"] - file_peak) < 1e-6,
                   "%.6f / %.6f" % (rms_v["peak"], file_peak)))

    if band_v["bands"]:
        d = np.max(np.abs(np.array(band_v["bands"]) - file_bands))
        checks.append(("bands max|Δ|", d < 0.1, "%.3f дБ" % d))
    else:
        checks.append(("bands", False, "нет кадров"))

    if loud_v.get("lufs") is not None and file_lufs == file_lufs:
        d = abs(loud_v["lufs"] - file_lufs)
        checks.append(("lufs", d < 0.1, "%.2f / %.2f (=%.2f LU)" % (loud_v["lufs"], file_lufs, d)))
    else:
        checks.append(("lufs", False, "нет данных"))

    # стерео: файловая формула из perceptual.summarize
    L, R = x[:, 0].astype(np.float64), x[:, 1].astype(np.float64)
    mid, side = (L + R) / 2, (L - R) / 2
    e = lambda a: float(np.mean(a ** 2)) + 1e-12  # noqa: E731
    file_sm = 10 * np.log10(e(side) / e(mid))
    checks.append(("stereo side/mid", abs(st_v["side_mid_db"] - file_sm) < 0.05,
                   "%.2f / %.2f" % (st_v["side_mid_db"], file_sm)))

    try:
        os.remove(tmp)
    except OSError:
        pass

    ok = True
    for name, passed, detail in checks:
        print("%-18s %s  %s" % (name, "OK  " if passed else "ПЛОХО", detail))
        ok = ok and passed
    print("\nитог:", "зелено" if ok else "есть расхождения")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
