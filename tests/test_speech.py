"""Проверка SII на сигналах с известным ответом.

Свойства, которые обязаны выполняться, иначе вся модель — украшение:
1. важности полос нормированы (сумма 1) — как в стандарте;
2. вокал без маскера разборчив (SII близко к 1);
3. чем громче маскер, тем ниже SII (монотонность);
4. значение всегда в 0..1.
"""
import os
import sys
import tempfile

import numpy as np
import soundfile as sf

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import speech  # noqa: E402

FS = 32000


def speechlike(seed: int, dur: float = 4.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(dur * FS)
    noise = rng.standard_normal(n)
    # речевой контур: слоги ~4 Гц, полоса 160…8000
    t = np.arange(n) / FS
    env = 0.5 * (1 + np.sign(np.sin(2 * np.pi * 4.0 * t)))
    env = np.convolve(env, np.ones(int(0.02 * FS)) / (0.02 * FS), mode="same")
    from scipy.signal import butter, sosfilt
    sos = butter(4, [160, 8000], btype="bandpass", fs=FS, output="sos")
    return 0.08 * sosfilt(sos, noise) * env


def masker(level_db: float, seed: int = 99, dur: float = 4.0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    n = int(dur * FS)
    from scipy.signal import butter, sosfilt
    sos = butter(4, [100, 9000], btype="bandpass", fs=FS, output="sos")
    x = sosfilt(sos, rng.standard_normal(n))
    x = x / (np.sqrt((x ** 2).mean()) + 1e-12)
    return x * (10 ** (level_db / 20))


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="flsii_")
    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-44s %s" % (name, detail))

    total = float(speech.IMPORTANCE.sum())
    check("важности полос нормированы", abs(total - 1.0) < 0.005, "сумма %.4f" % total)
    check("18 полос 160…8000", len(speech.FREQS) == 18
          and speech.FREQS[0] == 160 and speech.FREQS[-1] == 8000, "")

    v = speechlike(1)
    vp = os.path.join(tmp, "vox.wav")
    sf.write(vp, v, FS)

    quiet = np.zeros_like(v)
    qp = os.path.join(tmp, "quiet.wav")
    sf.write(qp, quiet, FS)
    clean = speech.intelligibility(vp, qp)
    check("без маскера разборчиво", clean["sii"] > 0.75, "SII %.3f" % clean["sii"])

    results = []
    for lvl in (-40, -24, -12, 0):
        m = masker(lvl)
        mp = os.path.join(tmp, "m%d.wav" % (lvl + 100))
        sf.write(mp, m, FS)
        r = speech.intelligibility(vp, mp)
        results.append((lvl, r["sii"]))

    vals = [s for _, s in results]
    check("монотонность по уровню маскера", all(a >= b for a, b in zip(vals, vals[1:])),
          ", ".join("%d дБ → %.3f" % (l, s) for l, s in results))
    check("громкий маскер убивает разборчивость", vals[-1] < 0.35, "SII %.3f" % vals[-1])
    check("значения в 0..1", all(0.0 <= s <= 1.0 for s in vals), "")

    # ровный шум маскирует сильнее тихого — проверим и оценку словами
    check("оценка словами не пустая", bool(speech._grade(vals[-1])), speech._grade(vals[-1]))

    # относительная оценка: та же величина среди слабых и сильных референсов
    high = speech._grade(0.30, [0.01, 0.06, 0.12, 0.21])
    low = speech._grade(0.30, [0.32, 0.40, 0.55, 0.61])
    check("та же величина читается по-разному в разной базе",
          "лучше 100%" in high and "хуже" in low, "%s | %s" % (high, low))
    check("базовая линия задаёт процентиль",
          speech.intelligibility(vp, qp, baseline=[0.01, 0.06, 0.12]).get("percentile")
          == 100.0, "")

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
