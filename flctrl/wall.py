"""wall.py — «стена» и провалы: где микс теряет тело, а не просто тише.

Провал — это не общий уровень ниже, а выпадение отдельной полосы относительно
того же трека: секция, где низ есть, верх есть, а середина рухнула. На слух это
«пустота», хотя пик-метр ничего не показывает. Поэтому дыра ищется по каждой
полосе и каждой секции относительно медианы всего трека, а не относительно
абсолютного порога.

«Стена» (толщина) — доля времени, когда полная энергия держится в узком
коридоре вокруг медианы: у плотного металла она высокая, у рыхлого микса низкая.

    python -m flctrl.wall mix.wav --refs a.mp3 b.mp3
"""
from __future__ import annotations

import argparse
import json

import numpy as np

BANDS = np.array([63, 125, 250, 500, 1000, 2000, 4000, 8000, 12000], dtype=float)
HOLE_DB = 8.0          # насколько полоса должна упасть ниже своей медианы
FLOOR_DB = -70.0       # ниже этого полоса не считается (там просто тишина)


def _frames(path: str, a: float | None, b: float | None):
    import pan as pan_mod

    x, fs = pan_mod._read(path, a, b)
    L, R, freqs, nframes, hop_s = pan_mod._stft(x, fs)
    mid = np.abs((L + R) / 2) ** 2
    return mid, freqs, hop_s


def _band_power(mid: np.ndarray, freqs: np.ndarray) -> np.ndarray:
    """Мощность по полосам: (кадры, полосы)."""
    out = np.zeros((mid.shape[0], len(BANDS)))
    for j, fc in enumerate(BANDS):
        lo, hi = fc / 2 ** 0.5, fc * 2 ** 0.5
        m = (freqs >= lo) & (freqs < hi)
        if m.any():
            out[:, j] = mid[:, m].sum(axis=1)
    return out


def _db(power: np.ndarray) -> np.ndarray:
    return 10 * np.log10(np.maximum(power, 1e-30))


def analyze(path: str, a: float | None = None, b: float | None = None,
            section_s: float = 8.0, hole_db: float = HOLE_DB) -> dict:
    """Найти провалы и оценить толщину стены."""
    mid, freqs, hop_s = _frames(path, a, b)
    band_pow = _band_power(mid, freqs)
    band_db = _db(band_pow)
    total_db = _db(mid.sum(axis=1))

    # медиана по каждому полосу за весь трек — «норма» самого микса
    med = np.median(band_db, axis=0)
    loud_enough = med > FLOOR_DB

    # секции
    sec_frames = max(1, int(section_s / max(hop_s, 1e-6)))
    sections = []
    for start in range(0, band_db.shape[0], sec_frames):
        sl = slice(start, min(band_db.shape[0], start + sec_frames))
        if sl.stop - sl.start < 2:
            continue
        level = band_db[sl].mean(axis=0)
        t = round(start * hop_s + (a or 0.0), 1)   # время в файле, не в окне
        deficits = []
        for j, fc in enumerate(BANDS):
            if not loud_enough[j]:
                continue
            drop = float(med[j] - level[j])
            if drop >= hole_db:
                deficits.append({"band": int(fc), "drop_db": round(drop, 1)})
        sections.append({"t": t, "deficits": deficits,
                         "total_db": round(float(total_db[sl].mean()), 1)})

    holes = [{"t": s["t"], **d} for s in sections for d in s["deficits"]]

    # толщина: доля времени в узком коридоре вокруг медианы полной энергии
    med_total = float(np.median(total_db))
    tight = np.abs(total_db - med_total) <= 6.0
    thickness = float(tight.mean())

    # ровность спектра: разброс полосных медиан относительно среднего наклона
    band_med = np.where(loud_enough, med, np.nan)
    flat = float(np.nanstd(band_med - np.nanmean(band_med))) if np.any(loud_enough) else 0.0

    return {
        "path": path,
        "bands": [{"fc": int(fc), "median_db": round(float(med[j]), 1),
                   "alive": bool(loud_enough[j])}
                  for j, fc in enumerate(BANDS)],
        "sections": sections,
        "holes": holes,
        "hole_count": len(holes),
        "thickness": round(thickness, 3),
        "band_spread_db": round(flat, 2),
        "total_median_db": round(med_total, 1),
    }


def compare(mine: dict, refs: list[dict]) -> dict:
    """Сравнить толщину и провалы с референсами."""
    if not refs:
        return {"vs_refs": None}
    th = [r["thickness"] for r in refs]
    holes = [r["hole_count"] for r in refs]
    return {
        "thickness": {"mine": mine["thickness"], "ref_median": round(float(np.median(th)), 3),
                      "verdict": ("плотнее жанра" if mine["thickness"] > float(np.median(th))
                                  else "рыхлее жанра")},
        "holes": {"mine": mine["hole_count"], "ref_median": float(np.median(holes)),
                  "verdict": ("провалов больше, чем у референсов"
                              if mine["hole_count"] > float(np.median(holes))
                              else "провалов не больше, чем у референсов")},
        "band_spread": {"mine": mine["band_spread_db"],
                        "ref_median": round(float(np.median([r["band_spread_db"] for r in refs])), 2)},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Провалы и толщина микса")
    ap.add_argument("mine")
    ap.add_argument("--refs", nargs="*", default=[])
    ap.add_argument("--a", type=float, default=None)
    ap.add_argument("--b", type=float, default=None)
    ap.add_argument("--section", type=float, default=8.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    mine = analyze(args.mine, args.a, args.b, args.section)
    refs = [analyze(r, args.a, args.b, args.section) for r in args.refs]
    mine["compare"] = compare(mine, refs)
    if args.json:
        print(json.dumps(mine, ensure_ascii=False, indent=1))
        return 0
    print("толщина стены %.3f | провалов %d | разброс полос %.2f дБ" % (
        mine["thickness"], mine["hole_count"], mine["band_spread_db"]))
    if mine["compare"].get("thickness"):
        c = mine["compare"]
        print("против референсов: %s (%s), %s" % (
            c["thickness"]["verdict"], c["thickness"]["ref_median"], c["holes"]["verdict"]))
    for h in mine["holes"][:12]:
        print("  %.0f с: полоса %d Гц провалилась на %.1f дБ" % (
            h["t"], h["band"], h["drop_db"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
