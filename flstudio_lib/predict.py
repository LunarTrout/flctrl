"""predict.py -- предсказание результата правки без её применения.

Идея: воспринимаемая громкость по полосам аддитивна в мощности.
Значит, зная профиль микса и профиль дорожки, можно:

    новый_микс = 10·log10( 10^(микс/10) − 10^(дорожка/10) + 10^((дорожка+правка)/10) )

и пересчитать метрики (резкость, доли областей, соны) ДО того, как что-то менять.

Что учитывается: линейные правки — громкость и полосная коррекция (EQ).
Что НЕ учитывается: нелинейность плагинов, компрессия, маскировка,
перекрёстные влияния — для них нужен реальный замер.

  ../.venv313/Scripts/python.exe predict.py --mix mix.wav --track stem.wav --gain -2 --eq 300:-3 --eq 6000:2
  ../.venv313/Scripts/python.exe predict.py --mix mix.wav --stems DIR     # проверка модели
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import perceptual as P  # noqa: E402

FC = P.FC
REGIONS = P.REGIONS
BARK_Z = P.BARK_Z


def profile_db(path: str, a: float | None = None, b: float | None = None) -> np.ndarray:
    """Медианный уровень по 28 третьоктавным полосам, дБ."""
    import soundfile as sf
    with sf.SoundFile(path) as f:
        fs = f.samplerate
        if a is not None and b is not None:
            f.seek(int(a * fs))
            x = f.read(int((b - a) * fs), dtype="float64", always_2d=True)
        else:
            x = f.read(dtype="float64", always_2d=True)
    lv, _t = P.band_levels(x, fs)
    return np.median(lv, axis=0)


def metrics(prof: np.ndarray) -> dict:
    """Метрики по одному полосному профилю (через Zwicker)."""
    row = P.calibrate(prof[None, :])[0]
    N, phon, spec, sharp = P.zwicker_series(row[None, :])
    out = {"N": float(N[0]), "phon": float(phon[0]), "sharp": float(sharp[0])}
    regions = {}
    for name, lo, hi in REGIONS:
        m = (BARK_Z >= lo) & (BARK_Z < hi)
        regions[name] = float(spec[0, m].sum())
    tot = max(sum(regions.values()), 1e-9)
    out["regions_pct"] = {k: 100.0 * v / tot for k, v in regions.items()}
    return out


def eq_curve(freqs: np.ndarray, bands: list[tuple[float, float]]) -> np.ndarray:
    """Полосная коррекция: гауссовы купола вокруг указанных частот.

    bands: список (частота Гц, дБ). Ширина купола — 1/2 октавы.
    """
    gain = np.zeros_like(freqs, dtype=float)
    for fc, db in bands:
        w = 0.5
        gain += db * np.exp(-0.5 * ((np.log2(freqs / fc)) / w) ** 2)
    return gain


def predict(mix: np.ndarray, track: np.ndarray, gain_db: float = 0.0,
            eq: list[tuple[float, float]] | None = None) -> dict:
    """Предсказать метрики микса после правки одной дорожки."""
    eq = eq or []
    pm = 10.0 ** (mix / 10.0)
    pt = 10.0 ** (track / 10.0)
    t_new_db = track + gain_db + eq_curve(FC, eq)
    pt_new = 10.0 ** (t_new_db / 10.0)
    rest = np.maximum(pm - pt, 1e-12)          # вклад остального микса
    new = 10.0 * np.log10(np.maximum(rest + pt_new, 1e-12))
    return {"before": metrics(mix), "after": metrics(new), "profile_after": new}


def delta(before: dict, after: dict) -> list[str]:
    lines = []
    lines.append("соны      %6.1f → %6.1f  (%+.1f)" % (
        before["N"], after["N"], after["N"] - before["N"]))
    lines.append("резкость  %6.3f → %6.3f  (%+.3f)" % (
        before["sharp"], after["sharp"], after["sharp"] - before["sharp"]))
    for k in before["regions_pct"]:
        b, a = before["regions_pct"][k], after["regions_pct"][k]
        lines.append("%-8s %6.1f%% → %6.1f%%  (%+.1f)" % (k, b, a, a - b))
    return lines


def check_stems(mix_path: str, stems_dir: str) -> int:
    """Проверка модели: сумма стемов должна дать профиль, близкий к миксу."""
    import soundfile as sf
    with sf.SoundFile(mix_path) as f:
        dur = len(f) / f.samplerate
    names = sorted(glob.glob(os.path.join(stems_dir, "*.wav")))
    if not names:
        print("нет стемов в", stems_dir)
        return 2
    mix = profile_db(mix_path, 0, min(60, dur))
    acc = np.zeros_like(mix)
    for n in names:
        try:
            acc += 10.0 ** (profile_db(n, 0, min(60, dur)) / 10.0)
        except Exception as e:
            print("  пропуск", os.path.basename(n), e)
    rec = 10.0 * np.log10(np.maximum(acc, 1e-12))
    d = rec - mix
    print("стедов: %d" % len(names))
    print("расхождение по полосам, дБ:")
    for fc, v in zip(FC, d):
        if abs(v) > 0.5:
            print("  %6.0f Гц  %+5.1f" % (fc, v))
    print("среднее отклонение: %+.2f дБ, максимум по модулю: %.2f дБ"
          % (float(np.mean(d)), float(np.max(np.abs(d)))))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mix", required=True)
    ap.add_argument("--track")
    ap.add_argument("--stems")
    ap.add_argument("--gain", type=float, default=0.0)
    ap.add_argument("--eq", action="append", default=[])
    ap.add_argument("--from", dest="a", type=float)
    ap.add_argument("--to", dest="b", type=float)
    a = ap.parse_args()

    if a.stems:
        return check_stems(a.mix, a.stems)

    eq = []
    for item in a.eq:
        fc, db = item.split(":")
        eq.append((float(fc), float(db)))

    mix = profile_db(a.mix, a.a, a.b)
    track = profile_db(a.track, a.a, a.b)
    r = predict(mix, track, a.gain, eq)
    print("правка: дорожка %s, громкость %+0.1f дБ, EQ %s"
          % (os.path.basename(a.track), a.gain, eq or "нет"))
    for line in delta(r["before"], r["after"]):
        print("  " + line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
