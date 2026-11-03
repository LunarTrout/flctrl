"""Проверка точности решения: предсказание должно сбываться.

План говорит «бас в середине на −10 дБ, поднять на 3». Это осмысленно только
если после реального подъёма на 3 дБ измеренное отношение и правда поднимется
примерно на 3 дБ. Здесь это проверяется честно: микс пересобирается с
изменённым стемом, и измерение делается заново.

Допуск не нулевой: общая громкость микса при подъёме одного источника растёт,
и нормировка уровня (90 дБ SPL) забирает часть прибавки.
"""
import os
import sys
import tempfile

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "flctrl"))

import partial  # noqa: E402

FS = 24000
SEC = 8.0


def band_noise(fc: float, n: int, seed: int, width: float = 0.5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    sos = butter(4, [fc * (1 - width / 2), fc * (1 + width / 2)], btype="bandpass",
                 fs=FS, output="sos")
    y = sosfilt(sos, x)
    return y / (np.sqrt((y ** 2).mean()) + 1e-12)


def write(path: str, left: np.ndarray, right: np.ndarray | None = None) -> str:
    if right is None:
        right = left
    sf.write(path, np.stack([left, right], axis=1), FS)
    return path


def snr_of_stem(mix: str, stems: dict, stem: str, region: str) -> dict:
    attr = partial.attribute_mg(mix, stems, None, None, section_s=SEC)
    row = attr["stems"][stem]
    secs = row.get("sections") or []
    vals = [s["snr"][region] for s in secs if region in (s.get("snr") or {})]
    return {"snr": float(np.median(vals)) if vals else None,
            "audib": row["audib"], "share": row["share_pct"]}


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="fldec_")
    n = int(SEC * FS)
    t = np.arange(n) / FS

    guitar = 0.30 * band_noise(1000, n, 1) * (0.6 + 0.4 * np.sin(2 * np.pi * 2 * t))
    bass = 0.05 * band_noise(1000, n, 2)          # тот же диапазон, но тише
    kick = 0.10 * band_noise(120, n, 3)

    gb = write(os.path.join(tmp, "gtr.wav"), guitar * 0.9, guitar)
    bb = write(os.path.join(tmp, "bass.wav"), bass, bass * 0.95)
    kb = write(os.path.join(tmp, "kick.wav"), kick, kick)

    def mix_of(bass_gain: float, name: str) -> str:
        left = 0.9 * guitar + bass_gain * bass + kick
        right = guitar + 0.95 * bass_gain * bass + kick
        return write(os.path.join(tmp, name), left, right)

    stems = {"gtr": gb, "bass": bb, "kick": kb}

    m0 = mix_of(1.0, "mix0.wav")
    r0 = snr_of_stem(m0, stems, "bass", "mid")

    # поднимаем бас ровно на 3 дБ — файл, который подставляется в микс
    bass_db = 10 ** (3 / 20)
    bb3 = write(os.path.join(tmp, "bass3.wav"), bass * bass_db, bass * 0.95 * bass_db)
    stems3 = {"gtr": gb, "bass": bb3, "kick": kb}
    m3 = mix_of(bass_db, "mix3.wav")
    r3 = snr_of_stem(m3, stems3, "bass", "mid")

    passed, failed = [], []

    def check(name, cond, detail=""):
        (passed if cond else failed).append(name)
        print(("ок      " if cond else "ПАДЕНИЕ ") + "%-46s %s" % (name, detail))

    check("отношение измерено до правки", r0["snr"] is not None, "%.1f дБ" % (r0["snr"] or 0))
    delta = (r3["snr"] or 0) - (r0["snr"] or 0)
    check("подъём на 3 дБ виден в измерении", 2.0 <= delta <= 3.6,
          "изменилось на %+.2f дБ" % delta)
    check("направление верное", delta > 0, "%+.2f" % delta)
    check("слышимость тоже выросла", r3["audib"] > r0["audib"],
          "%.3f → %.3f" % (r0["audib"], r3["audib"]))
    check("доля выросла", r3["share"] > r0["share"],
          "%.1f → %.1f %%" % (r0["share"], r3["share"]))

    print("\nитог: %d прошло, %d упало" % (len(passed), len(failed)))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
