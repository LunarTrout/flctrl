"""liveview.py -- живая картина микса по полосам из профилей дорожек и пиков.

Идея: один раз снять спектральный профиль каждой дорожки (соло-захват),
дальше в реальном времени читать пики и складывать профили с их весом.

    профиль дорожки: 28 третьоктавных уровней + опорный уровень
    оценка микса:     сумма профилей, сдвинутых по текущему пику
    метрики:          Zwicker по этой сумме (соны, резкость, доли областей)

Это ОЦЕНКА, а не измерение: пик не равен RMS, профиль зависит от материала.
Годится для «кто где сидит сейчас», не годится для финального решения.

  python liveview.py profile solo.wav out.json
  python liveview.py sum prof1.json prof2.json --peaks 0.5 0.3
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import perceptual as P  # noqa: E402
import listen_layer as L  # noqa: E402


def profile_from_audio(x, fs: int) -> dict:
    """Профиль дорожки: медианные уровни 28 полос и опорный RMS."""
    lv, _t = P.band_levels(x, fs)
    mono = x.mean(axis=1) if x.ndim > 1 else x
    rms = float(np.sqrt(np.mean(mono ** 2)) + 1e-12)
    active = lv.mean(axis=1) > (lv.mean() - 30)
    if active.sum() > 5:
        lv = lv[active]
    return {
        "levels_db": [round(float(v), 2) for v in np.median(lv, axis=0)],
        "ref_rms": round(rms, 6),
        "frames": int(lv.shape[0]),
    }


def profile_file(path: str) -> dict:
    x, fs = L.load_section(path, None)
    return profile_from_audio(x, fs)


def estimate(profiles: list[dict], peaks: list[float], offset_db: float = 0.0) -> dict:
    """Оценить полосный профиль микса: сумма профилей, сдвинутых по пикам.

    profiles[i] — профиль дорожки; peaks[i] — её текущий пик (0..1).
    offset_db — то же усиление прослушивания, что и при реальном замере:
    без него абсолютные соны несравнимы (профили сняты без калибровки).
    """
    acc = np.zeros(len(P.FC))
    used = []
    for prof, peak in zip(profiles, peaks):
        if not peak or peak < 1e-6:
            continue
        ref = max(float(prof.get("ref_rms", 1e-6)), 1e-9)
        shift = 20.0 * np.log10(peak / ref)
        levels = np.array(prof["levels_db"], dtype=float) + shift + offset_db
        acc += 10.0 ** (levels / 10.0)
        used.append(bool(True))
    total = 10.0 * np.log10(np.maximum(acc, 1e-30))
    m = measure_array_from_profile(total)
    out = dict(m)
    out["tracks_used"] = int(sum(used))
    out["levels_db"] = [round(float(v), 1) for v in total]
    return out


def measure_array_from_profile(levels_db: np.ndarray) -> dict:
    """Метрики по одному полосному профилю (28 полос)."""
    row = np.asarray(levels_db, dtype=float)[None, :]
    N, phon, spec, sharp = P.zwicker_series(row)
    regions = {}
    for name, lo, hi in P.REGIONS:
        m = (P.BARK_Z >= lo) & (P.BARK_Z < hi)
        regions[name] = float(spec[0, m].sum())
    tot = max(sum(regions.values()), 1e-9)
    return {
        "N": round(float(N[0]), 1),
        "phon": round(float(phon[0]), 1),
        "sharp": round(float(sharp[0]), 3),
        "regions_pct": {k: round(100.0 * v / tot, 1) for k, v in regions.items()},
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "profile":
        src, dst = sys.argv[2], sys.argv[3]
        prof = profile_file(src)
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(prof, f, ensure_ascii=False)
        print("профиль записан:", dst, "полос:", len(prof["levels_db"]))
        return 0
    if cmd == "sum":
        args = sys.argv[2:]
        peaks = []
        if "--peaks" in args:
            i = args.index("--peaks")
            peaks = [float(v) for v in args[i + 1:]]
            args = args[:i]
        profiles = []
        for p in args:
            with open(p, encoding="utf-8") as f:
                profiles.append(json.load(f))
        if not peaks:
            peaks = [1.0] * len(profiles)
        r = estimate(profiles, peaks)
        print("дорожек учтено:", r["tracks_used"])
        print("соны %.1f | фоны %.1f | резкость %.3f" % (r["N"], r["phon"], r["sharp"]))
        print("доли:", r["regions_pct"])
        return 0
    print("неизвестная команда")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
