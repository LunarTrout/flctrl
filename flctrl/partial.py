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

# Границы областей в герцах — для моделей, которые работают не в Bark (MG/ERB).
FREQ_REGIONS = (
    ("sub", 0.0, 60.0),
    ("bass", 60.0, 250.0),
    ("lowmid", 250.0, 560.0),
    ("mid", 560.0, 1278.0),
    ("upmid", 1278.0, 3000.0),
    ("pres", 3000.0, 6000.0),
    ("air", 6000.0, 30000.0),
)
TC = np.array([25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400,
               500, 630, 800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000,
               6300, 8000, 10000, 12500, 16000], dtype=float)  # 29 полос ISO


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


def bands29(x: np.ndarray, fs: int, hop_s: float = 0.20) -> tuple[np.ndarray, np.ndarray]:
    """Покадровые уровни 29 третьоктавных полос (25 Гц…16 кГц), dB SPL.

    Именно этот набор требует ISO 532-2 / модель Мура-Гласберга: 28 полос
    perceptual.py туда не подходят (phonometry отвергает форму не (T,29)).
    """
    from scipy.signal import butter, sosfilt

    if x.ndim == 2:
        x = x.mean(axis=1)
    hop = max(1, int(round(hop_s * fs)))
    n = len(x)
    out = np.empty((max(1, 1 + (n - hop) // hop), len(TC)))
    for j, fc in enumerate(TC):
        lo = fc / 2 ** (1 / 6)
        hi = fc * 2 ** (1 / 6)
        nyq = fs / 2 * 0.99
        if lo >= nyq:                      # полоса выше Найквиста — её просто нет
            out[:, j] = 1e-30
            continue
        sos = butter(4, [max(lo, 10.0), min(hi, nyq)],
                     btype="bandpass", fs=fs, output="sos")
        y = sosfilt(sos, x)
        frames = 1 + (len(y) - hop) // hop
        z = y[:frames * hop].reshape(frames, hop)
        out[:frames, j] = (z ** 2).mean(axis=1)
    out = 10 * np.log10(np.maximum(out, 1e-30) / (20e-6) ** 2)
    t = np.arange(out.shape[0]) * hop_s
    return out, t


def mg_series(lv29: np.ndarray):
    """Громкость по ISO 532-2 (Мур-Гласберг): N по кадрам и специфическая (T,372)."""
    from phonometry import psychoacoustics as psy

    lv29 = np.atleast_2d(lv29)
    N = np.empty(len(lv29))
    spec = None
    for i, row in enumerate(lv29):
        r = psy.loudness_moore_glasberg_from_third_octave(row)
        N[i] = r.loudness
        s = np.asarray(r.specific, dtype=float)
        if spec is None:
            spec = np.empty((len(lv29), len(s)))
        spec[i] = s
        if i == 0:
            centre = np.asarray(r.centre_frequencies, dtype=float)
    return N, spec, centre


def _region_masks(centre: np.ndarray) -> dict[str, np.ndarray]:
    return {name: (centre >= lo) & (centre < hi) for name, lo, hi in FREQ_REGIONS}


def _n_series(lv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """N по кадрам и специфическая громкость (T,240)."""
    N, _phon, spec, _sharp = P.zwicker_series(lv)
    return N, spec


def _hz_masks() -> dict:
    """Области в герцах — по ним считается честное отношение уровней."""
    edges = {"sub": (0, 60), "bass": (60, 120), "lowmid": (120, 300),
             "mid": (300, 1200), "upmid": (1200, 3000), "pres": (3000, 6000),
             "air": (6000, 30000)}
    out = {}
    for name, (lo, hi) in edges.items():
        out[name] = (TC >= lo) & (TC < hi)
    return out


def _sections_by_region(spec_mix, spec_rest, spec_solo, masks, t, section_s,
                        regions_global, offset: float = 0.0,
                        p_stem=None, p_mix=None) -> list:
    """Слышимость по секциям: где именно инструмент пропадает.

    Глобальное «A = 0.2» скрывает, что в куплете всё в порядке, а в припеве
    гитары забивают клавиши. Здесь то же отношение «слышно / соло», но
    посчитанное по каждой секции и каждой области отдельно.
    """
    frames = spec_mix.shape[0]
    hop = float(t[1] - t[0]) if len(t) > 1 else 0.1
    per_sec = max(1, int(round(section_s / max(hop, 1e-6))))
    out = []
    for start in range(0, frames, per_sec):
        sl = slice(start, min(frames, start + per_sec))
        if sl.stop - sl.start < 2:
            continue
        reg = {}
        sh = {}
        snr = {}
        total_solo = float(np.mean(spec_solo[sl].sum(axis=1)))
        for name, mask in masks.items():
            if not mask.any():
                continue
            part = np.maximum(spec_mix[sl][:, mask].sum(axis=1)
                              - spec_rest[sl][:, mask].sum(axis=1), 0.0)
            solo = spec_solo[sl][:, mask].sum(axis=1)
            rest = spec_rest[sl][:, mask].sum(axis=1)
            denom = float(np.mean(solo))
            reg[name] = (round(float(np.clip(np.mean(part) / denom, 0, 1)), 3)
                         if denom > 1e-9 else 0.0)
            sh[name] = (round(denom / total_solo, 3) if total_solo > 1e-9 else 0.0)
            # отношение «инструмент / остальное» в полосе — в дБ УРОВНЯ, а не
            # громкости: громкость сжимает уровень (≈0.23 на дБ), и правка,
            # посчитанная по ней, была бы вчетверо меньше нужной
            r_mean = float(np.mean(rest))
            if p_stem is not None and p_mix is not None:
                hz = _hz_masks()
                m = hz.get(name)
                ps = float(np.mean(p_stem[sl][:, m].sum(axis=1))) if (m is not None and m.any()) else 0.0
                pm = float(np.mean(p_mix[sl][:, m].sum(axis=1))) if (m is not None and m.any()) else 0.0
                pr = max(pm - ps, 1e-30)
                snr[name] = (round(float(np.clip(10 * np.log10(max(ps, 1e-30) / pr), -60, 60)), 1)
                             if ps > 1e-30 else -60.0)
            else:
                snr[name] = (round(float(np.clip(10 * np.log10(max(denom, 1e-30)
                                                              / max(r_mean, 1e-30)),
                                                -60, 60)), 1)
                             if r_mean > 1e-9 else 60.0)
        out.append({"t": round(float(t[start]) + offset, 1), "regions": reg,
                    "share": sh, "snr": snr, "active": total_solo > 1e-6})
    return out


def attribute_mg(mix_path: str, stems: dict[str, str], a: float | None = None,
                 b: float | None = None, level_spl: float = 90.0,
                 section_s: float | None = None) -> dict:
    """Слышимость по ISO 532-2 с кэшем: модель дорогая, файлы меняются редко."""
    import cache
    import os as _os

    parts = [_os.path.abspath(mix_path)]
    parts += [k for k in sorted(stems)]
    parts += [_os.path.abspath(p) for k, p in sorted(stems.items())]
    parts += [a, b, level_spl, section_s]
    return cache.memo("mg", parts,
                      lambda: _attribute_mg(mix_path, stems, a, b, level_spl, section_s))


def _attribute_mg(mix_path: str, stems: dict[str, str], a: float | None = None,
                  b: float | None = None, level_spl: float = 90.0,
                  section_s: float | None = None) -> dict:
    """То же, что attribute(), но на модели ISO 532-2 (Мур-Гласберг).

    Разница принципиальная: Zwicker считает громкость по 240 полосам Bark,
    MG — по 372 ERB-полосам с другим правилом суммирования. Для решений
    «сколько слышно» важна именно вторая: она стандартизована (ISO 532-2) и
    даёт по-полосную специфическую громкость, по которой видно, ГДЕ источник
    пропадает.
    """
    mix, fs = _read(mix_path, a, b)
    n = len(mix)
    lv_mix_raw, t = bands29(mix, fs)

    tot = 10 * np.log10(np.sum(10 ** (lv_mix_raw / 10), axis=1, keepdims=True) + 1e-30)
    offset = level_spl - float(np.median(tot))
    lv_mix = lv_mix_raw + offset
    p_mix = 10 ** (lv_mix / 10)

    N_mix, spec_mix, centre = mg_series(lv_mix)
    masks = _region_masks(centre)

    out: dict = {"model": "iso532-2", "dur": float(t[-1] - t[0] + 0.1) if len(t) else 0.0,
                 "level_spl": level_spl, "stems": {}}

    for name, path in stems.items():
        x, fs_s = _read(path, a, b)
        if fs_s != fs:
            # стем мог быть отрендерен в другой частоте — приводим к миксу,
            # иначе полоса выше половины меньшей частоты просто отсутствует
            from math import gcd

            from scipy.signal import resample_poly

            g = gcd(int(fs_s), int(fs))
            x = resample_poly(x, fs // g, fs_s // g)
        x = _align(x, n)
        lv_raw, _ = bands29(x, fs)
        lv = lv_raw + offset
        p = 10 ** (lv / 10)

        N_solo, spec_solo, _ = mg_series(lv)
        p_rest = np.maximum(p_mix - p, 10 ** (FLOOR_DB / 10))
        N_rest, spec_rest, _ = mg_series(10 * np.log10(p_rest))
        N_partial = np.maximum(N_mix - N_rest, 0.0)

        p_plus = p_mix + p * (10 ** 0.1 - 1.0)
        N_plus, _, _ = mg_series(10 * np.log10(p_plus))
        dN = float(np.mean(N_plus - N_mix))

        regions = {}
        for rname, mask in masks.items():
            solo_r = spec_solo[:, mask].sum(axis=1)
            part_r = np.maximum(spec_mix[:, mask].sum(axis=1) - spec_rest[:, mask].sum(axis=1), 0.0)
            denom = float(np.mean(solo_r))
            regions[rname] = {
                "A": float(np.clip(np.mean(part_r) / denom, 0, 1)) if denom > 1e-9 else 0.0,
                "solo": denom,
                "partial": float(np.mean(part_r)),
            }

        mean_partial = float(np.mean(N_partial))
        mean_solo = float(np.mean(N_solo))
        row = {
            "N_solo": mean_solo,
            "N_partial": mean_partial,
            "audib": float(np.clip(mean_partial / mean_solo, 0.0, 1.0)) if mean_solo > 1e-9 else 0.0,
            "audib_p10": float(np.clip(np.percentile(
                N_partial / np.maximum(N_solo, 1e-9), 10), 0, 1)),
            "dN_per_db": dN,
            "regions": regions,
        }
        if section_s:
            row["sections"] = _sections_by_region(spec_mix, spec_rest, spec_solo,
                                                  masks, t, section_s, regions,
                                                  offset=float(a or 0.0),
                                                  p_stem=p, p_mix=p_mix)
        out["stems"][name] = row

    total = sum(v["N_partial"] for v in out["stems"].values()) or 1e-9
    for v in out["stems"].values():
        v["share_pct"] = 100.0 * v["N_partial"] / total
    return out


def attribute(mix_path: str, stems: dict[str, str], a: float | None = None,
              b: float | None = None, level_spl: float = 90.0,
              model: str = "mg") -> dict:
    """Главная функция: частичная громкость и слышимость каждого стема.

    model="mg" (по умолчанию) — ISO 532-2, по-полосная специфическая громкость.
    model="zwicker" — прежний путь на 240 полосах Bark (для сверки).
    """
    if model in ("mg", "iso532-2", "moore"):
        return attribute_mg(mix_path, stems, a, b, level_spl)
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
