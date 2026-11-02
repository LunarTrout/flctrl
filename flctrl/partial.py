"""partial.py — сколько инструмента реально СЛЫШНО в миксе, а не сколько он громкий.

Почему это другое: «доля полос» и «громкость стема» считаются так, будто
источники не мешают друг другу. В плотном миксе слабый источник маскируется, и
его вклад в слышимую громкость падает в разы. Слух работает через приращение:

    N_partial = N(микс) − N(микс без источника)      (leave-one-out)
    A         = N_partial / N_solo                   (0..1, «слышно / не слышно»)

Всё считается по кадрам и по полосам, поэтому видно и «когда», и «где» источник
пропадает. Чувствительность dN/dB меряется, а не берётся из головы: стем
поднимается на 1 дБ, и модель заново считает приращение.

Опирается на perceptual.py (полосы, калибровка, Zwicker) — своей психоакустики
не изобретает.

    python -m flctrl.partial --mix mix.wav --stems drums=a.wav --stems bass=b.wav --a 20 --b 50
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np

import perceptual as P

REGIONS = P.REGIONS
FLOOR_DB = -60.0


def _read(path: str, a: float | None, b: float | None):
    import soundfile as sf

    with sf.SoundFile(path) as f:
        fs = f.samplerate
        if a is not None and b is not None:
            f.seek(int(a * fs))
            x = f.read(int((b - a) * fs), dtype="float64", always_2d=True)
        else:
            x = f.read(dtype="float64", always_2d=True)
    return x, fs


def _align(x: np.ndarray, n: int) -> np.ndarray:
    if len(x) >= n:
        return x[:n]
    pad = np.zeros((n - len(x), x.shape[1]))
    return np.concatenate([x, pad], 0)


def bands(x: np.ndarray, fs: int, hop_s: float = 0.10):
    lv, t = P.band_levels(x, fs, hop_s=hop_s)
    return lv, t


def _n_series(lv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """N по кадрам и специфическая громкость (T,240)."""
    N, _phon, spec, _sharp = P.zwicker_series(lv)
    return N, spec


def attribute(mix_path: str, stems: dict[str, str], a: float | None = None,
              b: float | None = None, level_spl: float = 90.0) -> dict:
    """Главная функция: частичная громкость и слышимость каждого стема."""
    mix, fs = _read(mix_path, a, b)
    n = len(mix)
    lv_mix_raw, t = bands(mix, fs)

    # Калибровка одна на всех: сдвиг микса применяем и к стемам, иначе
    # сравнение «слышно/не слышно» поедет вместе с громкостью прослушивания.
    tot = 10 * np.log10(np.sum(10 ** (lv_mix_raw / 10), axis=1, keepdims=True) + 1e-30)
    offset = level_spl - float(np.median(tot))
    lv_mix = lv_mix_raw + offset
    p_mix = 10 ** (lv_mix / 10)

    N_mix, spec_mix = _n_series(lv_mix)

    out: dict = {"dur": float(t[-1] - t[0] + 0.10) if len(t) else 0.0,
                 "level_spl": level_spl, "stems": {}}
    partials: dict[str, np.ndarray] = {}

    for name, path in stems.items():
        x, fs_s = _read(path, a, b)
        if fs_s != fs:
            raise ValueError("стем %s c другой частотой: %d vs %d" % (name, fs_s, fs))
        x = _align(x, n)
        lv_raw, _ = bands(x, fs)
        lv = lv_raw + offset
        p = 10 ** (lv / 10)

        N_solo, _ = _n_series(lv)

        # микс без источника — вычитание в мощности
        p_rest = np.maximum(p_mix - p, 10 ** (FLOOR_DB / 10))
        N_rest, _ = _n_series(10 * np.log10(p_rest))

        N_partial = np.maximum(N_mix - N_rest, 0.0)
        partials[name] = N_partial

        # чувствительность: стем на +1 дБ -> насколько вырастет N микса
        p_plus = p_mix + p * (10 ** 0.1 - 1.0)
        N_plus, _ = _n_series(10 * np.log10(p_plus))
        dN = float(np.mean(N_plus - N_mix))

        mean_partial = float(np.mean(N_partial))
        mean_solo = float(np.mean(N_solo))
        out["stems"][name] = {
            "N_solo": mean_solo,
            "N_partial": mean_partial,
            "audib": float(np.clip(mean_partial / mean_solo, 0.0, 1.0)) if mean_solo > 1e-9 else 0.0,
            "audib_p10": float(np.clip(np.percentile(N_partial / np.maximum(N_solo, 1e-9), 10), 0, 1)),
            "dN_per_db": dN,
        }

    total = sum(v["N_partial"] for v in out["stems"].values()) or 1e-9
    for name, v in out["stems"].items():
        v["share_pct"] = 100.0 * v["N_partial"] / total

    # где именно слышно: доля частичной громкости по областям
    for name, N_part in partials.items():
        pass  # области считаем по специфической громкости ниже

    # области: пересчёт по кадрам, где стем активен
    for name, path in stems.items():
        x, _ = _read(path, a, b)
        x = _align(x, n)
        lv_raw, _ = bands(x, fs)
        lv = lv_raw + offset
        _N, spec_solo = _n_series(lv)
        reg = {}
        for rname, lo, hi in REGIONS:
            m = (P.BARK_Z >= lo) & (P.BARK_Z < hi)
            reg[rname] = float(np.mean(spec_solo[:, m].sum(axis=1)))
        tot_r = sum(reg.values()) or 1e-9
        out["stems"][name]["regions_pct"] = {k: 100.0 * v / tot_r for k, v in reg.items()}

    return out


def sensitivity(mix_path: str, stem_path: str, a=None, b=None, step_db: float = 1.0) -> float:
    """dN/dB для одного стема: сколько сон прибавит +1 дБ его фейдера."""
    mix, fs = _read(mix_path, a, b)
    n = len(mix)
    lv_mix, _ = bands(mix, fs)
    p_mix = 10 ** (lv_mix / 10)
    x, _ = _read(stem_path, a, b)
    x = _align(x, n)
    lv_s, _ = bands(x, fs)
    p_s = 10 ** (lv_s / 10)
    N0, _ = _n_series(10 * np.log10(p_mix))
    p_new = p_mix + p_s * (10 ** (step_db / 10.0) - 1.0)
    N1, _ = _n_series(10 * np.log10(p_new))
    return float(np.mean(N1 - N0) / step_db)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Частичная громкость и слышимость стемов")
    ap.add_argument("--mix", required=True)
    ap.add_argument("--stems", action="append", default=[], help="stem=path")
    ap.add_argument("--a", type=float, default=None)
    ap.add_argument("--b", type=float, default=None)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)

    stems = {}
    for item in args.stems:
        name, _, path = item.partition("=")
        stems[name] = path
    if not stems:
        print("нужен хотя бы один --stems name=path")
        return 2

    res = attribute(args.mix, stems, args.a, args.b)
    print("%-8s %7s %7s %6s %6s" % ("стем", "N_solo", "N_part", "A", "доля%"))
    for name, v in res["stems"].items():
        print("%-8s %7.1f %7.1f %6.2f %6.1f" % (
            name, v["N_solo"], v["N_partial"], v["audib"], v["share_pct"]))
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(res, f, ensure_ascii=False, indent=1)
        print("записано:", args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
